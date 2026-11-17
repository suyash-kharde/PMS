"""What each kind of job does. Every handler reuses the existing pipeline code; the only change is
that it now runs in the worker instead of inside someone's page request.

Kinds and lanes:
  timeline_audio   audio  denoise, speaker separation, transcription of a recording
  timeline_doc     ai     text extraction from a note / text / PDF / DOCX / PPTX
  brd_update       ai     fold every processed-but-not-yet-applied entry into one new BRD version
  brd_regenerate   ai     the Regenerate button
  brd_sync         ai     the Sync button on one entry
  build_plan       ai     Build Plan from the frozen Tech Stack, Dev Instructions and BRD
  sprint_plan      ai     the AI sprint planner
  penpot_workspace setup  creating a project's design workspace
"""
from dataclasses import dataclass, field
from typing import Callable

from .services import Retry, enqueue


def _noop(job, *args):
    return None


def _merge_params(waiting, kind, params):
    if params:
        waiting.params = {**waiting.params, **{k: v for k, v in params.items() if v not in ("", None)}}
        waiting.save(update_fields=["params"])


@dataclass
class Spec:
    lane: str
    label: str
    handler: Callable
    default_seconds: float = 120
    per_size: float = 0  # seconds per unit of job.size (audio MB); 0 = not size based
    merge: Callable = field(default=_merge_params)
    on_failed: Callable = field(default=_noop)
    on_cancelled: Callable = field(default=_noop)


def _notify(user, title, body="", url="", kind="job", project=None):
    if user is None:
        return
    from apps.notify import services as notifications

    notifications.notify(user, title=title, body=body, url=url, kind=kind, project=project)


def _notify_failed(job):
    _notify(job.requested_by, f"{job.project.key + ': ' if job.project else ''}{job.label} failed",
            body=job.error[:300], url="/ai-queue/", kind="job_failed", project=job.project)


# --------------------------------------------------------------------------- timeline entries
def _entry(job):
    from apps.intake.models import TimelineEntry

    return TimelineEntry.objects.filter(pk=job.target_id).select_related("project", "author").first()


def run_entry(job):
    from apps.intake.services import pipeline

    entry = _entry(job)
    if entry is None:
        return ""
    if entry.status not in ("queued", "processing"):
        return f"/entries/{entry.pk}/"  # already handled elsewhere
    pipeline.process_entry(entry, regenerate_brd=False)  # the BRD is updated by its own job below
    entry.refresh_from_db()
    if job.params.get("brd", True) and entry.status == "ready" and entry.include_in_brd and not entry.brd_applied:
        queue_brd_update(entry.project, entry.author)
    return f"/entries/{entry.pk}/"


def entry_failed(job):
    entry = _entry(job)
    if entry is not None and entry.status in ("queued", "processing"):
        entry.status = "failed"
        entry.save(update_fields=["status", "updated_at"])
        entry.log(f"FAILED in the queue: {job.error[:300]}")
    _notify_failed(job)


def entry_cancelled(job):
    entry = _entry(job)
    if entry is not None and entry.status == "queued":
        entry.status = "draft"
        entry.save(update_fields=["status", "updated_at"])
        entry.log("removed from the queue; use Reprocess to queue it again")


def queue_entry(entry, user=None, brd=True, limit=False):
    """Queue a timeline entry for processing (new upload, reprocess, or adopted after a restart)."""
    from apps.intake.services import pipeline

    audio = entry.kind == "audio" or (entry.upload and pipeline.classify(entry.upload.name) == "audio")
    size = 0
    if audio and entry.upload:
        try:
            size = entry.upload.size / (1024 * 1024)
        except (OSError, ValueError):
            size = 0
    if entry.status != "queued":
        entry.status = "queued"
        entry.save(update_fields=["status", "updated_at"])
    return enqueue(
        "timeline_audio" if audio else "timeline_doc", project=entry.project, user=user or entry.author,
        target_id=entry.pk, params={"brd": brd}, label=f"{'Recording' if audio else 'Timeline entry'} · {entry.title}",
        size=size, dedupe_key=f"entry:{entry.pk}", limit=limit,
    )


# --------------------------------------------------------------------------- BRD
def _brd_url(project, version):
    return f"/projects/{project.pk}/brd/?v={version.number}"


def run_brd_update(job):
    """Fold every processed entry not yet in the BRD into ONE new version (several uploads for a
    project become one update instead of one version each)."""
    from apps.intake.services import brd, llm

    project = job.project
    entries = list(project.timeline.filter(include_in_brd=True, brd_applied=False, status="ready")
                   .order_by("occurred_at"))
    if not entries:
        return f"/projects/{project.pk}/brd/"
    try:
        version = brd.regenerate(project, entries=entries, user=job.requested_by)
    except llm.LLMError as exc:
        raise Retry(str(exc)) from exc
    for entry in entries:
        entry.log(f"BRD updated to v{version.number}")
    return _brd_url(project, version)


def brd_update_failed(job):
    for entry in job.project.timeline.filter(include_in_brd=True, brd_applied=False, status="ready"):
        entry.log(f"BRD update failed: {job.error[:300]}")
    _notify_failed(job)


def merge_brd(waiting, kind, params):
    """A Regenerate click joins a waiting automatic update and turns it into a full regenerate."""
    if kind == "brd_regenerate" and waiting.kind == "brd_update":
        waiting.kind, waiting.label = "brd_regenerate", f"BRD regenerate · {waiting.project.key}"
        waiting.save(update_fields=["kind", "label"])
    _merge_params(waiting, kind, params)


def run_brd_regenerate(job):
    from apps.intake.models import BRD
    from apps.intake.services import brd, llm
    from apps.projects.models import Activity

    project = job.project
    current = BRD.objects.filter(project=project).first()
    if current and current.is_frozen:
        raise ValueError("the BRD is frozen; unfreeze it first to regenerate")
    try:
        version = brd.regenerate(project, user=job.requested_by, note=job.params.get("note", ""))
    except llm.LLMError as exc:
        raise Retry(str(exc)) from exc
    Activity.log(job.requested_by, f"regenerated the BRD (v{version.number})", project=project)
    return _brd_url(project, version)


def queue_brd_update(project, user=None):
    return enqueue("brd_update", project=project, user=user, label=f"BRD update · {project.key}",
                   dedupe_key=f"brd:{project.pk}")


def queue_brd_regenerate(project, user, note=""):
    return enqueue("brd_regenerate", project=project, user=user, params={"note": note},
                   label=f"BRD regenerate · {project.key}", dedupe_key=f"brd:{project.pk}", limit=True)


def run_brd_sync(job):
    from apps.intake.services import brd, llm
    from apps.projects.models import Activity

    entry = _entry(job)
    if entry is None:
        return ""
    if entry.speakers.filter(display_name="", user__isnull=True).exists():
        raise ValueError("name every speaker first — the BRD would otherwise credit SPEAKER_00")
    try:
        version = brd.regenerate(entry.project, entries=[entry], user=job.requested_by)
    except llm.LLMError as exc:
        raise Retry(str(exc)) from exc
    who = job.requested_by.display_name if job.requested_by else "the queue"
    entry.log(f"synced into BRD v{version.number} by {who}")
    Activity.log(job.requested_by, f"synced “{entry.title}” into BRD v{version.number}", project=entry.project)
    return _brd_url(entry.project, version)


def brd_sync_failed(job):
    entry = _entry(job)
    if entry is not None:
        entry.log(f"sync failed: {job.error[:300]}")
    _notify_failed(job)


def queue_brd_sync(entry, user):
    return enqueue("brd_sync", project=entry.project, user=user, target_id=entry.pk,
                   label=f"Sync to BRD · {entry.title}", dedupe_key=f"brd-sync:{entry.pk}", limit=True)


# --------------------------------------------------------------------------- Build Plan
def run_build_plan(job):
    from apps.intake.services import llm
    from apps.intake.services import plan as plan_service
    from apps.projects.models import Activity, ProjectDocument
    from apps.projects.views import _plan_sources

    project = job.project
    sources, ready = _plan_sources(project)
    if not ready:
        missing = ", ".join(s["label"] for s in sources if not s["ready"])
        raise ValueError(f"freeze these first: {missing}")
    tech_doc = next(s["doc"] for s in sources if s["label"] == "Tech Stack")
    dev_doc = next(s["doc"] for s in sources if s["label"] == "Development Instructions")
    brd = next(s["doc"] for s in sources if s["label"] == "BRD")
    doc, _created = ProjectDocument.objects.get_or_create(project=project, kind="build_plan")
    if doc.is_frozen:
        raise ValueError("the Build Plan is frozen; unfreeze it first to regenerate")
    try:
        markdown, _meta = plan_service.generate(project, tech_doc, dev_doc, brd)
    except llm.LLMError as exc:
        raise Retry(str(exc)) from exc
    doc.content = markdown
    doc.updated_by = job.requested_by
    doc.save(update_fields=["content", "updated_by", "updated_at"])
    Activity.log(job.requested_by, "generated the Build Plan from Tech Stack, Dev Instructions and the BRD",
                 project=project)
    url = f"/projects/{project.pk}/docs/build_plan/"
    _notify(job.requested_by, f"{project.key}: Build Plan ready", body="Generated from the frozen sources.",
            url=url, kind="job_done", project=project)
    return url


def queue_build_plan(project, user):
    return enqueue("build_plan", project=project, user=user, label=f"Build Plan · {project.key}",
                   dedupe_key=f"plan:{project.pk}", limit=True)


# --------------------------------------------------------------------------- sprint planner
def _plan_run(job):
    from apps.sprints.models import SprintPlanRun

    return SprintPlanRun.objects.filter(pk=job.target_id).first()


def run_sprint_plan(job):
    from apps.sprints.services import pipeline as sprint_pipeline

    plan_run = _plan_run(job)
    if plan_run is None:
        return ""
    if plan_run.status in ("queued", "running"):
        sprint_pipeline.run(plan_run)  # records its own progress, failure and notification
    return plan_run.get_absolute_url()


def sprint_plan_cancelled(job):
    plan_run = _plan_run(job)
    if plan_run is not None and not plan_run.is_terminal:
        plan_run.status = "cancelled"
        plan_run.save(update_fields=["status"])


def sprint_plan_failed(job):
    plan_run = _plan_run(job)
    if plan_run is not None and not plan_run.is_terminal:
        plan_run.status, plan_run.error = "failed", job.error[:2000]
        plan_run.save(update_fields=["status", "error"])
    _notify_failed(job)


def queue_sprint_plan(plan_run, user=None, limit=False):
    return enqueue("sprint_plan", project=plan_run.project, user=user or plan_run.requested_by,
                   target_id=plan_run.pk, label=f"AI sprint plan · {plan_run.project.key}",
                   dedupe_key=f"sprint:{plan_run.pk}", limit=limit)


# --------------------------------------------------------------------------- Penpot
def run_penpot(job):
    from apps.penpot import services as penpot
    from apps.penpot.models import PenpotWorkspace

    workspace = PenpotWorkspace.objects.filter(pk=job.target_id).select_related("project").first()
    if workspace is None or not penpot.enabled():
        return ""
    if workspace.status in ("queued", "provisioning"):
        penpot.provision(workspace)
    return f"/projects/{workspace.project_id}/penpot/"


def queue_penpot(workspace):
    return enqueue("penpot_workspace", project=workspace.project, target_id=workspace.pk,
                   label=f"Design workspace · {workspace.project.key}", dedupe_key=f"penpot:{workspace.pk}",
                   serial_key="")


KINDS = {
    "timeline_audio": Spec("audio", "Recording", run_entry, default_seconds=300, per_size=20,
                           on_failed=entry_failed, on_cancelled=entry_cancelled),
    "timeline_doc": Spec("ai", "Timeline entry", run_entry, default_seconds=20,
                         on_failed=entry_failed, on_cancelled=entry_cancelled),
    "brd_update": Spec("ai", "BRD update", run_brd_update, default_seconds=150, merge=merge_brd,
                       on_failed=brd_update_failed),
    "brd_regenerate": Spec("ai", "BRD regenerate", run_brd_regenerate, default_seconds=150, merge=merge_brd,
                           on_failed=_notify_failed),
    "brd_sync": Spec("ai", "Sync to BRD", run_brd_sync, default_seconds=150, on_failed=brd_sync_failed),
    "build_plan": Spec("ai", "Build Plan", run_build_plan, default_seconds=180, on_failed=_notify_failed),
    "sprint_plan": Spec("ai", "AI sprint plan", run_sprint_plan, default_seconds=600,
                        on_failed=sprint_plan_failed, on_cancelled=sprint_plan_cancelled),
    "penpot_workspace": Spec("setup", "Design workspace", run_penpot, default_seconds=60),
}
TIMELINE_KINDS = ("timeline_audio", "timeline_doc")


# --------------------------------------------------------------------------- adopting waiting work
def adopt_all(startup=False):
    """Queue work that is waiting without a job (queued by signals, commands or older code).
    At startup also put back anything a restart cut off mid-way."""
    from apps.intake.models import TimelineEntry
    from apps.jobs.models import ACTIVE, Job
    from apps.penpot.models import PenpotWorkspace
    from apps.sprints.models import SprintPlanRun

    def has_job(kinds, pk):
        return Job.objects.filter(kind__in=kinds, target_id=pk, status__in=ACTIVE).exists()

    adopted = 0
    entry_states = ("queued", "processing") if startup else ("queued",)
    for entry in TimelineEntry.objects.filter(status__in=entry_states).select_related("project", "author"):
        if not has_job(TIMELINE_KINDS, entry.pk):
            if entry.status == "processing":
                entry.log("re-queued after the worker restarted")
            queue_entry(entry)
            adopted += 1
    run_states = ("queued", "running") if startup else ("queued",)
    for plan_run in SprintPlanRun.objects.filter(status__in=run_states).select_related("project"):
        if not has_job(("sprint_plan",), plan_run.pk):
            if plan_run.status == "running":
                plan_run.status = "queued"
                plan_run.save(update_fields=["status"])
            queue_sprint_plan(plan_run)
            adopted += 1
    try:
        from apps.penpot import services as penpot

        if penpot.enabled():
            ws_states = ("queued", "provisioning") if startup else ("queued",)
            for workspace in PenpotWorkspace.objects.filter(status__in=ws_states).select_related("project"):
                if not has_job(("penpot_workspace",), workspace.pk):
                    queue_penpot(workspace)
                    adopted += 1
    except Exception:  # noqa: BLE001 - Penpot being misconfigured must not stop the queue
        pass
    return adopted
