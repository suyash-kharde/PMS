"""Queue mechanics: add a job, claim the next one, run it, recover after a restart, and say where a
job is in line and roughly how long it will take. What each kind of job actually does lives in
handlers.py.

Lanes keep slow audio away from quick AI calls, and each lane has its own number of worker slots
(settings / env JOB_LANES, e.g. "audio=1,ai=2,setup=1"). Within the AI lane only one job per
project runs at a time, which keeps BRD versions in order and stops one project taking every slot.
"""
import logging
import os
import statistics
from datetime import timedelta

from django.db import connection, transaction
from django.utils import timezone

from .models import ACTIVE, Job

logger = logging.getLogger(__name__)

DEFAULT_LANES = "audio=1,ai=2,setup=1"
MAX_WAITING_PER_USER = 5
RETRY_BACKOFF = 60  # seconds, times the attempt number
_ADVISORY_KEY = 7420501  # any constant: serialises claiming across worker threads and processes


class QueueFull(Exception):
    """The person already has MAX_WAITING_PER_USER jobs waiting."""


class Retry(Exception):
    """Raised by a handler for a temporary failure (e.g. the AI service returned an error)."""


def lanes():
    raw = os.environ.get("JOB_LANES", DEFAULT_LANES)
    out = {}
    for part in raw.split(","):
        name, _, n = part.partition("=")
        if name.strip():
            out[name.strip()] = max(1, int(n or 1))
    return out


# --------------------------------------------------------------------------- adding jobs
def enqueue(kind, *, project=None, user=None, target_id=None, params=None, label="", size=0, dedupe_key="",
            serial_key=None, limit=False):
    """Add a job, or join the one already waiting with the same dedupe_key. -> (job, created).
    limit=True (a person's click) refuses a sixth waiting job from the same person."""
    from .handlers import KINDS

    spec = KINDS[kind]
    params = params or {}
    with transaction.atomic():
        if dedupe_key:
            waiting = Job.objects.select_for_update().filter(dedupe_key=dedupe_key, status="queued").first()
            if waiting:
                spec.merge(waiting, kind, params)
                return waiting, False
        if limit and user is not None and getattr(user, "pk", None):
            if Job.objects.filter(requested_by=user, status="queued").count() >= MAX_WAITING_PER_USER:
                raise QueueFull(f"You already have {MAX_WAITING_PER_USER} jobs waiting. "
                                "Please wait for one to finish.")
        if serial_key is None:
            serial_key = f"project:{project.pk}" if (project is not None and spec.lane == "ai") else ""
        job = Job.objects.create(
            kind=kind, lane=spec.lane, label=label or spec.label, project=project,
            requested_by=user if getattr(user, "pk", None) else None, target_id=target_id, params=params,
            dedupe_key=dedupe_key, serial_key=serial_key, size=size or 0,
        )
    return job, True


def active_job(**filters):
    return Job.objects.filter(status__in=ACTIVE, **filters).order_by("created_at").first()


# --------------------------------------------------------------------------- claiming and running
def claim(limits=None):
    """Take the next job a free slot may run: oldest first, per-lane slot limits, never two jobs
    with the same serial_key at once. -> Job (now running) or None."""
    limits = limits or lanes()
    now = timezone.now()
    with transaction.atomic():
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [_ADVISORY_KEY])
        running = list(Job.objects.filter(status="running").values_list("lane", "serial_key"))
        busy_serials = {s for _lane, s in running if s}
        for lane, slots in limits.items():
            if sum(1 for ln, _s in running if ln == lane) >= slots:
                continue
            candidates = (Job.objects.select_for_update(skip_locked=True)
                          .filter(status="queued", lane=lane, run_after__lte=now).order_by("created_at"))
            for job in candidates[:50]:
                if job.serial_key and job.serial_key in busy_serials:
                    continue
                job.status, job.started_at, job.finished_at = "running", now, None
                job.attempts += 1
                job.save(update_fields=["status", "started_at", "finished_at", "attempts"])
                return job
    return None


def run(job):
    """Run one claimed job through its handler and record the outcome. Never raises."""
    from .handlers import KINDS

    spec = KINDS.get(job.kind)
    try:
        if spec is None:
            raise ValueError(f"unknown job kind {job.kind!r}")
        result_url = spec.handler(job) or ""
        job.refresh_from_db(fields=["status"])
        if job.status == "cancelled":
            return job
        _finish(job, "done", result_url=result_url)
    except Retry as exc:
        if job.attempts < job.max_attempts:
            job.status, job.error = "queued", f"attempt {job.attempts} failed: {exc}"
            job.run_after = timezone.now() + timedelta(seconds=RETRY_BACKOFF * job.attempts)
            job.started_at = None
            job.save(update_fields=["status", "error", "run_after", "started_at"])
            logger.warning("job %s will retry: %s", job.pk, exc)
        else:
            _finish(job, "failed", error=str(exc))
            spec.on_failed(job)
    except Exception as exc:  # noqa: BLE001 - a broken job must not stop the worker
        logger.exception("job %s failed", job.pk)
        _finish(job, "failed", error=f"{type(exc).__name__}: {exc}")
        if spec is not None:
            spec.on_failed(job)
    return job


def _finish(job, status, error="", result_url=""):
    job.status, job.finished_at = status, timezone.now()
    if error:
        job.error = error[:4000]
    if result_url:
        job.result_url = result_url[:300]
    job.save(update_fields=["status", "finished_at", "error", "result_url"])


def run_pending(limit=100):
    """Run everything claimable right now, one after another (tests, `process_queue --once`)."""
    done = 0
    while done < limit:
        job = claim()
        if job is None:
            break
        run(job)
        done += 1
    return done


def cancel(job):
    """Only a waiting job can be cancelled; a running one finishes (an AI call can't be stopped)."""
    if job.status != "queued":
        return False
    from .handlers import KINDS

    _finish(job, "cancelled")
    spec = KINDS.get(job.kind)
    if spec:
        spec.on_cancelled(job)
    return True


def retry(job):
    if job.status not in ("failed", "cancelled"):
        return False
    job.status, job.error, job.attempts = "queued", "", 0
    job.run_after = job.created_at = timezone.now()
    job.started_at = job.finished_at = None
    job.save(update_fields=["status", "error", "attempts", "run_after", "created_at", "started_at", "finished_at"])
    return True


# --------------------------------------------------------------------------- after a restart
def recover_interrupted():
    """At worker start nothing can be running: put jobs cut off by a restart back in line."""
    n = Job.objects.filter(status="running").update(status="queued", started_at=None, run_after=timezone.now())
    if n:
        logger.warning("re-queued %s job(s) interrupted by a restart", n)
    return n


def adopt_orphans():
    """Work that is waiting but has no job: rows queued by code outside the web views (signals,
    management commands, older code) and entries left 'processing' by a restart."""
    from .handlers import adopt_all

    return adopt_all()


# --------------------------------------------------------------------------- position and estimate
def typical_seconds(kind, size=0):
    """Median of the last 20 finished runs of this kind, else the kind's built-in guess.
    Audio scales with its length."""
    from .handlers import KINDS

    spec = KINDS[kind]
    recent = [j.duration for j in Job.objects.filter(kind=kind, status="done", finished_at__isnull=False,
                                                       started_at__isnull=False).order_by("-finished_at")[:20]]
    recent = [d for d in recent if d is not None]
    if spec.per_size and size:
        rates = [j.duration / j.size for j in Job.objects.filter(kind=kind, status="done", size__gt=0)
                 .order_by("-finished_at")[:20] if j.duration]
        rate = statistics.median(rates) if len(rates) >= 3 else spec.per_size
        return max(30.0, rate * size + 30)
    if len(recent) >= 3:
        return float(statistics.median(recent))
    return float(spec.default_seconds)


def position_and_eta(job):
    """-> {"position": n or None, "low": s, "high": s} — seconds until the job should be done."""
    limits = lanes()
    slots = limits.get(job.lane, 1)
    now = timezone.now()
    own = typical_seconds(job.kind, job.size)
    if job.status == "running":
        elapsed = (now - job.started_at).total_seconds() if job.started_at else 0
        left = max(own - elapsed, own * 0.1)
        return {"position": None, "low": left * 0.6, "high": left * 1.6 + 20}
    if job.status != "queued":
        return {"position": None, "low": 0, "high": 0}
    ahead = list(Job.objects.filter(status="queued", lane=job.lane, created_at__lt=job.created_at))
    running = list(Job.objects.filter(status="running", lane=job.lane))
    work = sum(typical_seconds(j.kind, j.size) for j in ahead)
    for r in running:
        elapsed = (now - r.started_at).total_seconds() if r.started_at else 0
        est = typical_seconds(r.kind, r.size)
        work += max(est - elapsed, est * 0.1)
    wait = work / slots
    if job.run_after > now:
        wait = max(wait, (job.run_after - now).total_seconds())
    total = wait + own
    return {"position": len(ahead) + 1, "low": total * 0.7, "high": total * 1.5 + 20}


def minutes_text(seconds):
    m = max(1, round(seconds / 60))
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    return f"{h} hr {m} min" if m else f"{h} hr"


def describe(job):
    """Everything the page / API shows about a job."""
    data = {
        "id": job.pk, "kind": job.kind, "label": job.label, "status": job.status,
        "status_label": job.get_status_display(), "error": job.error if job.status == "failed" else "",
        "result_url": job.result_url, "active": job.is_active,
    }
    eta = position_and_eta(job)
    data["position"] = eta["position"]
    if job.status == "queued":
        data["text"] = (f"Queued · #{eta['position']} in line · usually ready within {minutes_text(eta['high'])}")
        if job.attempts:
            data["text"] += f" (retry {job.attempts + 1} of {job.max_attempts} after a temporary error)"
    elif job.status == "running":
        started = minutes_text((timezone.now() - job.started_at).total_seconds()) if job.started_at else ""
        data["text"] = f"Running for {started} · usually done within {minutes_text(eta['high'])}"
    elif job.status == "done":
        data["text"] = "Done"
    elif job.status == "failed":
        data["text"] = f"Failed: {job.error[:200]}"
    else:
        data["text"] = "Cancelled"
    return data


def queued_message(job, created):
    d = describe(job)
    lead = "Added to the queue" if created else "Already in the queue — joined the waiting job"
    return (f"{lead}: {job.label}. #{d['position']} in line, usually ready within "
            f"{minutes_text(position_and_eta(job)['high'])}. You'll get a notification when it's done."
            if job.status == "queued" else f"{lead}: {job.label}. {d['text']}.")
