import difflib
import json

import markdown as md
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Max
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.permissions import has_cap, require
from apps.audit.services import log as audit_log
from apps.jobs.handlers import queue_brd_regenerate, queue_brd_sync, queue_brd_update, queue_entry
from apps.jobs.services import QueueFull, queued_message
from apps.notify import services as notifications
from apps.projects.models import SIDES, Activity, ProjectMembership
from apps.projects.selectors import visible_projects

from .forms import (
    BRDEditForm, MembershipForm, NewPersonForm, SegmentForm, SpeakerNameForm, TimelineEntryForm,
)
from .models import BRD, BRDVersion, Speaker, TimelineEntry, TranscriptSegment
from .services import audio as audio_service
from .services import brd as brd_service
from .services import llm, pipeline

MD_EXTENSIONS = ["extra", "sane_lists", "toc", "nl2br"]


def render_markdown(text):
    return md.markdown(text or "", extensions=MD_EXTENSIONS)


def get_project(user, pk):
    project = visible_projects(user).filter(pk=pk).first()
    if not project:
        raise Http404("Project not found")
    return project


def _playable_audio_url(entry):
    """Prefer the denoised rendition: it is 16 kHz mono wav, which every browser can seek."""
    media = entry.media_set.first()
    if media and media.denoised:
        return media.denoised.url
    if media and media.original:
        return media.original.url
    return entry.upload.url if entry.upload else ""


def get_entry(user, pk):
    entry = get_object_or_404(TimelineEntry.objects.select_related("project"), pk=pk)
    get_project(user, entry.project_id)
    return entry


# --------------------------------------------------------------------------- timeline
@login_required
def timeline(request, pk):
    project = get_project(request.user, pk)
    entries = project.timeline.select_related("author").prefetch_related("speakers", "media_set")

    kind = request.GET.get("kind", "")
    if kind:
        entries = entries.filter(kind=kind)

    require(request.user, "timeline.view", project)
    can_add = has_cap(request.user, "timeline.add", project)
    form = TimelineEntryForm(initial={"occurred_at": timezone.now()})
    brd = BRD.objects.filter(project=project).first()

    return render(
        request,
        "intake/timeline.html",
        {
            "project": project,
            "entries": entries,
            "form": form,
            "can_add": can_add,
            "kind": kind,
            "kind_choices": TimelineEntry._meta.get_field("kind").choices,
            "brd": brd,
            "pending": project.timeline.filter(include_in_brd=True, brd_applied=False)
            .exclude(status__in=["draft", "failed"]).count(),
            "tab": "timeline",
        },
    )


@login_required
@require_POST
def entry_create(request, pk):
    project = get_project(request.user, pk)
    require(request.user, "timeline.add", project)
    form = TimelineEntryForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, "; ".join(f"{k}: {v[0]}" for k, v in form.errors.items()))
        return redirect("intake:timeline", project.pk)

    entry = form.save(commit=False)
    entry.project = project
    entry.author = request.user
    if entry.upload:
        entry.original_name = entry.upload.name
        if pipeline.classify(entry.upload.name) == "audio":
            entry.kind = "audio"
    entry.status = "queued"
    entry.save()
    Activity.log(request.user, f"added timeline entry “{entry.title}”", project=project)
    job, created = queue_entry(entry, request.user)
    messages.success(request, queued_message(job, created))
    return redirect("intake:entry_detail", entry.pk)


@login_required
def entry_detail(request, pk):
    entry = get_entry(request.user, pk)
    project = entry.project
    require(request.user, "timeline.view", project)
    can_edit = has_cap(request.user, "timeline.process", project)
    # a few of each speaker's longest turns, so you can hear who it is before naming them
    samples = {}
    for segment in entry.segments.all():
        samples.setdefault(segment.speaker_label, []).append(segment)
    speaker_forms = []
    for speaker in entry.speakers.all():
        turns = samples.get(speaker.label, [])
        longest = sorted(turns, key=lambda s: s.end - s.start, reverse=True)[:3]
        longest = sorted(longest, key=lambda s: s.start)

        # a short taster: a few seconds from the middle of each of the best turns, so you
        # hear the voice immediately instead of waiting through a whole turn
        sample = []
        for segment in longest:
            length = segment.end - segment.start
            take = min(SAMPLE_TURN_SECONDS, length)
            start = segment.start + max(0.0, (length - take) / 2)
            sample.append([round(start, 2), round(start + take, 2)])
            if sum(end - begin for begin, end in sample) >= SAMPLE_TOTAL_SECONDS:
                break

        full = [[round(s.start, 2), round(s.end, 2)] for s in longest]
        speaker_forms.append(
            {
                "speaker": speaker,
                "form": SpeakerNameForm(instance=speaker, prefix=f"sp{speaker.pk}", project=project),
                "sample_ranges": json.dumps(sample),
                "sample_seconds": round(sum(end - begin for begin, end in sample)),
                "full_ranges": json.dumps(full),
                "full_seconds": round(sum(end - begin for begin, end in full)),
                "turn_count": len(turns),
            }
        )
    return render(
        request,
        "intake/entry_detail.html",
        {
            "project": project,
            "entry": entry,
            "segments": entry.segments.all(),
            "speaker_forms": speaker_forms,
            "media": entry.media_set.first(),
            "audio_url": _playable_audio_url(entry),
            "can_edit": can_edit,
            "brd_versions": entry.brd_versions.all(),
        },
    )


MAX_CLIP_SECONDS = 300
# a voice is recognisable in a few seconds; keep the taster short so it starts instantly
SAMPLE_TURN_SECONDS = 4.0
SAMPLE_TOTAL_SECONDS = 12.0


@login_required
def entry_clip(request, pk):
    """Return a small wav built from one or more time ranges of the recording.

    Streaming a 17-minute file into an <audio> element just to hear 20 seconds is slow and
    the browser has to buffer the whole thing. Cutting the ranges server-side keeps every
    request small; the denoised rendition is already 16 kHz mono wav, so the stdlib can do
    it without ffmpeg.
    """
    import io
    import wave

    entry = get_entry(request.user, pk)
    require(request.user, "timeline.view", entry.project)

    media = entry.media_set.first()
    source = media.denoised if media and media.denoised else None
    if source is None:
        raise Http404("No wav rendition to cut from")

    ranges = []
    for chunk in (request.GET.get("ranges") or "").split(","):
        if "-" not in chunk:
            continue
        start, _, end = chunk.partition("-")
        try:
            start, end = float(start), float(end)
        except ValueError:
            continue
        if end > start:
            ranges.append((start, end))
    if not ranges:
        raise Http404("No ranges requested")

    total = sum(end - start for start, end in ranges)
    if total > MAX_CLIP_SECONDS:
        return JsonResponse({"error": f"Clip too long ({total:.0f}s, limit {MAX_CLIP_SECONDS}s)"}, status=400)

    source.open("rb")
    try:
        with wave.open(source, "rb") as wav:
            rate = wav.getframerate()
            width = wav.getsampwidth()
            channels = wav.getnchannels()
            frames = bytearray()
            for start, end in ranges:
                wav.setpos(min(int(start * rate), wav.getnframes()))
                frames += wav.readframes(max(0, int((end - start) * rate)))
    finally:
        source.close()

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(width)
        out.setframerate(rate)
        out.writeframes(bytes(frames))
    payload = buffer.getvalue()

    response = HttpResponse(payload, content_type="audio/wav")
    response["Content-Length"] = str(len(payload))
    response["Cache-Control"] = "private, max-age=3600"
    return response


@login_required
@require_POST
def entry_reprocess(request, pk):
    entry = get_entry(request.user, pk)
    require(request.user, "timeline.process", entry.project)
    if entry.status in ("queued", "processing"):
        messages.info(request, "This entry is already in the queue.")
        return redirect("intake:entry_detail", entry.pk)
    entry.brd_applied = False
    entry.save(update_fields=["brd_applied"])
    entry.log(f"queued for reprocessing by {request.user.display_name}")
    job, created = queue_entry(entry, request.user, brd=request.POST.get("skip_brd") != "1")
    messages.success(request, queued_message(job, created))
    return redirect("intake:entry_detail", entry.pk)


@login_required
@require_POST
def entry_delete(request, pk):
    entry = get_entry(request.user, pk)
    require(request.user, "timeline.process", entry.project)
    project_id = entry.project_id
    entry.delete()
    messages.success(request, "Timeline entry deleted.")
    return redirect("intake:timeline", project_id)


# --------------------------------------------------------------------------- speakers
@login_required
@require_POST
def speakers_save(request, pk):
    """Name every diarized speaker on an entry, optionally mapping each to a project member."""
    entry = get_entry(request.user, pk)
    project = entry.project
    require(request.user, "timeline.process", project)

    saved, errors = 0, []
    for speaker in entry.speakers.all():
        form = SpeakerNameForm(request.POST, instance=speaker, prefix=f"sp{speaker.pk}", project=project)
        if not any(request.POST.get(f"sp{speaker.pk}-{f}") for f in ("display_name", "user", "side", "notes")):
            continue
        if form.is_valid():
            form.save()
            saved += 1
        else:
            errors.append(f"{speaker.label}: {'; '.join(form.errors.get('__all__', ['invalid']))}")

    if errors:
        messages.error(request, " ".join(errors))
    elif saved:
        entry.log(f"{saved} speaker(s) named by {request.user.display_name}")
        messages.success(request, f"Named {saved} speaker(s).")

    if not entry.speakers.filter(display_name="", user__isnull=True).exists() and entry.status == "awaiting_speakers":
        entry.status = "ready"
        entry.save(update_fields=["status", "updated_at"])
        if entry.include_in_brd and request.POST.get("update_brd", "1") == "1":
            job, created = queue_brd_update(project, request.user)
            messages.success(request, queued_message(job, created))
    if saved:
        pipeline.reindex_best_effort(entry)
    return redirect("intake:entry_detail", entry.pk)


@login_required
def transcript_edit(request, pk):
    """Correct what the ASR heard: text, speaker per line, timings, add or drop lines."""
    entry = get_entry(request.user, pk)
    project = entry.project
    require(request.user, "timeline.process", project)
    labels = list(entry.speakers.values_list("label", flat=True)) or ["SPEAKER_00"]

    if request.method == "POST":
        changed, removed = 0, 0
        for segment in list(entry.segments.all()):
            prefix = f"seg{segment.pk}"
            if request.POST.get(f"{prefix}-delete"):
                segment.delete()
                removed += 1
                continue
            form = SegmentForm(request.POST, instance=segment, prefix=prefix, labels=labels)
            if form.is_valid():
                if form.has_changed():
                    form.save()
                    changed += 1
            else:
                messages.error(request, f"Line {segment.index + 1}: {form.errors.as_text()[:200]}")

        new_text = (request.POST.get("new-text") or "").strip()
        if new_text:
            TranscriptSegment.objects.create(
                entry=entry,
                index=(entry.segments.aggregate(m=Max("index"))["m"] or 0) + 1,
                start=float(request.POST.get("new-start") or 0),
                end=float(request.POST.get("new-end") or 0),
                speaker_label=request.POST.get("new-speaker") or labels[0],
                text=new_text,
            )
            changed += 1

        # renumber and rebuild the plain-text transcript from the edited lines
        for index, segment in enumerate(entry.segments.order_by("start", "index")):
            if segment.index != index:
                TranscriptSegment.objects.filter(pk=segment.pk).update(index=index)
        entry.transcript = "\n".join(s.text for s in entry.segments.all())
        entry.brd_applied = False
        entry.save(update_fields=["transcript", "brd_applied", "updated_at"])

        if changed or removed:
            entry.log(f"transcript edited by {request.user.display_name}: {changed} changed, {removed} removed")
            Activity.log(request.user, f"edited the transcript of “{entry.title}”", project=project)
            messages.success(
                request,
                f"Transcript saved ({changed} line(s) changed, {removed} removed). "
                "Press Sync to fold the corrections into the BRD.",
            )
            pipeline.reindex_best_effort(entry)
        return redirect("intake:transcript_edit", entry.pk)

    forms_ = [
        (segment, SegmentForm(instance=segment, prefix=f"seg{segment.pk}", labels=labels))
        for segment in entry.segments.all()
    ]
    return render(
        request,
        "intake/transcript_edit.html",
        {
            "project": project,
            "entry": entry,
            "segment_forms": forms_,
            "labels": labels,
            "speakers": entry.speakers.all(),
            "tab": "timeline",
        },
    )


@login_required
@require_POST
def entry_sync_brd(request, pk):
    """The Sync button: fold this entry's current content into a new BRD version."""
    entry = get_entry(request.user, pk)
    project = entry.project
    require(request.user, "brd.generate", project)

    if entry.speakers.filter(display_name="", user__isnull=True).exists():
        messages.error(request, "Name every speaker first — the BRD would otherwise credit SPEAKER_00.")
        return redirect("intake:entry_detail", entry.pk)

    try:
        job, created = queue_brd_sync(entry, request.user)
    except QueueFull as exc:
        messages.error(request, str(exc))
        return redirect("intake:entry_detail", entry.pk)
    messages.success(request, queued_message(job, created))
    return redirect("intake:entry_detail", entry.pk)


@login_required
@require_POST
def api_speaker_patch(request, pk):
    speaker = get_object_or_404(Speaker.objects.select_related("entry__project"), pk=pk)
    project = get_project(request.user, speaker.entry.project_id)
    require(request.user, "timeline.process", project)
    payload = json.loads(request.body or "{}")
    if "display_name" in payload:
        speaker.display_name = (payload["display_name"] or "").strip()
    if "user" in payload:
        speaker.user_id = payload["user"] or None
    if "side" in payload:
        speaker.side = payload["side"] or ""
    speaker.save()
    return JsonResponse({"ok": True, "name": speaker.resolved_name, "named": speaker.is_named})


# --------------------------------------------------------------------------- participants
def participants_context(request, project, **overrides):
    rows = project.memberships.select_related("user", "role")
    context = {
        "project": project,
        "client_side": [m for m in rows if m.side == "client"],
        "internal_side": [m for m in rows if m.side != "client"],
        "rows": [(m, MembershipForm(instance=m, prefix=f"m{m.pk}", project=project)) for m in rows],
        "form": MembershipForm(project=project),
        "new_person_form": NewPersonForm(),
        "can_edit": has_cap(request.user, "project.edit", project),
        "can_create_user": has_cap(request.user, "user.create"),
        "open_form": request.GET.get("add", ""),
        "tab": "people",
    }
    context.update(overrides)
    return context


@login_required
def participants(request, pk):
    project = get_project(request.user, pk)
    require(request.user, "participant.view", project)
    return render(request, "intake/participants.html", participants_context(request, project))


@login_required
@require_POST
def participant_save(request, pk, membership_pk=None):
    project = get_project(request.user, pk)
    require(request.user, "project.edit", project)
    instance = get_object_or_404(ProjectMembership, pk=membership_pk, project=project) if membership_pk else None
    form = MembershipForm(
        request.POST,
        instance=instance,
        project=project,
        prefix=f"m{instance.pk}" if instance else None,
    )
    if not form.is_valid() and instance is None:
        # keep what was typed instead of throwing it away on a redirect
        return render(
            request,
            "intake/participants.html",
            participants_context(request, project, form=form, open_form="existing"),
        )
    if form.is_valid():
        membership = form.save(commit=False)
        membership.project = project
        membership.save()
        Activity.log(
            request.user,
            f"{'updated' if instance else 'added'} {membership.user.display_name} "
            f"as a {membership.get_side_display().lower()} participant",
            project=project,
        )
        messages.success(request, f"{membership.user.display_name} saved.")
    else:
        messages.error(request, "; ".join(f"{k}: {v[0]}" for k, v in form.errors.items()))
    return redirect("intake:participants", project.pk)


@login_required
@require_POST
def participant_add_person(request, pk):
    """Create a brand-new user account and put them on the project."""
    project = get_project(request.user, pk)
    require(request.user, "project.edit", project)
    require(request.user, "user.create")

    form = NewPersonForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "intake/participants.html",
            participants_context(request, project, new_person_form=form, open_form="new"),
        )

    user, membership, generated = form.save(project)
    Activity.log(
        request.user,
        f"created {user.display_name} and added them as a {membership.get_side_display().lower()} participant",
        project=project,
    )
    if generated:
        messages.success(
            request,
            f"{user.display_name} created. Username “{user.username}”, temporary password “{generated}” — "
            "share it over a secure channel and have them change it.",
        )
    else:
        messages.success(request, f"{user.display_name} created with username “{user.username}”.")
    return redirect("intake:participants", project.pk)


@login_required
@require_POST
def participant_delete(request, pk, membership_pk):
    project = get_project(request.user, pk)
    require(request.user, "project.edit", project)
    get_object_or_404(ProjectMembership, pk=membership_pk, project=project).delete()
    messages.success(request, "Participant removed.")
    return redirect("intake:participants", project.pk)


@login_required
@require_POST
def participant_move(request, pk, membership_pk):
    """Drag & drop between the client-side and our-side columns."""
    project = get_project(request.user, pk)
    require(request.user, "project.edit", project)
    membership = get_object_or_404(ProjectMembership, pk=membership_pk, project=project)
    side = json.loads(request.body or "{}").get("side")
    if side not in dict(SIDES):
        return JsonResponse({"ok": False, "error": "Unknown side"}, status=400)

    if membership.side != side:
        old = membership.get_side_display()
        membership.side = side
        membership.save(update_fields=["side"])
        Activity.log(
            request.user,
            f"moved {membership.user.display_name} from {old} to {membership.get_side_display()}",
            project=project,
        )
    return JsonResponse({"ok": True, "membership": membership.pk, "side": membership.side})


# --------------------------------------------------------------------------- BRD
@login_required
def brd_view(request, pk):
    project = get_project(request.user, pk)
    require(request.user, "brd.view", project)
    brd = BRD.objects.filter(project=project).first()
    version_number = request.GET.get("v")
    version = None
    if brd:
        version = brd.versions.filter(number=version_number).first() if version_number else brd.current

    return render(
        request,
        "intake/brd.html",
        {
            "project": project,
            "brd": brd,
            "version": version,
            "versions": brd.versions.select_related("created_by", "source_entry") if brd else [],
            "html": render_markdown(version.markdown) if version else "",
            "pending": project.timeline.filter(include_in_brd=True, brd_applied=False)
            .exclude(status__in=["draft", "failed"]),
            "can_edit": has_cap(request.user, "brd.edit", project),
            "can_generate": has_cap(request.user, "brd.generate", project),
            "can_delete": has_cap(request.user, "brd.delete", project),
            "llm": {"model": llm.MODEL, "endpoint": llm.BASE_URL},
            "tab": "brd",
        },
    )


@login_required
@require_POST
def brd_freeze(request, pk):
    project = get_project(request.user, pk)
    require(request.user, "brd.edit", project)
    brd, _ = BRD.objects.get_or_create(project=project, defaults={"title": f"{project.name} BRD"})
    if brd.is_frozen:
        brd.is_frozen, brd.frozen_at, brd.frozen_by = False, None, None
        brd.save(update_fields=["is_frozen", "frozen_at", "frozen_by"])
        Activity.log(request.user, "unfroze the BRD", project=project)
        messages.success(request, "BRD unfrozen — it can be edited or regenerated again.")
    else:
        brd.is_frozen, brd.frozen_at, brd.frozen_by = True, timezone.now(), request.user
        brd.save(update_fields=["is_frozen", "frozen_at", "frozen_by"])
        Activity.log(request.user, "froze the BRD as final", project=project)
        messages.success(request, "BRD frozen as final.")
    return redirect("intake:brd", project.pk)


@login_required
@require_POST
def brd_regenerate(request, pk):
    project = get_project(request.user, pk)
    require(request.user, "brd.generate", project)
    brd = BRD.objects.filter(project=project).first()
    if brd and brd.is_frozen:
        messages.error(request, "BRD is frozen. Unfreeze it first to regenerate.")
        return redirect("intake:brd", project.pk)
    try:
        job, created = queue_brd_regenerate(project, request.user, note=request.POST.get("note", ""))
        messages.success(request, queued_message(job, created))
    except QueueFull as exc:
        messages.error(request, str(exc))
    return redirect("intake:brd", project.pk)


@login_required
@require_POST
def brd_delete(request, pk):
    """Delete the project's whole BRD: every version. Timeline entries go back to "not yet in a BRD", so a
    later regenerate rebuilds the document from all of them. Nothing else about the project is touched."""
    from apps.rag.models import Chunk

    project = get_project(request.user, pk)
    require(request.user, "brd.delete", project)
    brd = BRD.objects.filter(project=project).first()
    if brd is None:
        messages.info(request, "This project has no BRD to delete.")
        return redirect("intake:brd", project.pk)
    if brd.is_frozen:
        messages.error(request, "BRD is frozen as final. Unfreeze it before deleting it.")
        return redirect("intake:brd", project.pk)
    versions = brd.versions.count()
    with transaction.atomic():
        brd.delete()  # its versions go with it
        project.timeline.filter(brd_applied=True).update(brd_applied=False)
        Chunk.objects.filter(project=project, source_type="brd").delete()  # the AI chat stops quoting it
    Activity.log(request.user, f"deleted the BRD ({versions} version{'s' if versions != 1 else ''})", project=project)
    audit_log(request.user, "delete", "brd", target=f"{project.key} BRD ({versions} versions)")
    messages.success(request, f"BRD deleted ({versions} version{'s' if versions != 1 else ''}).")
    return redirect("intake:brd", project.pk)


@login_required
def brd_edit(request, pk):
    project = get_project(request.user, pk)
    require(request.user, "brd.edit", project)
    brd, _ = BRD.objects.get_or_create(project=project, defaults={"title": f"{project.name} BRD"})
    if brd.is_frozen:
        messages.error(request, "BRD is frozen. Unfreeze it first to make further edits.")
        return redirect("intake:brd", project.pk)
    form = BRDEditForm(request.POST or None, initial={"markdown": brd.markdown})
    if request.method == "POST" and form.is_valid():
        version = brd.add_version(
            markdown=form.cleaned_data["markdown"],
            summary=form.cleaned_data.get("summary") or "Manual edit",
            created_by=request.user,
            model_name="manual",
        )
        notifications.brd_updated(version)
        messages.success(request, f"Saved as v{version.number}.")
        return redirect("intake:brd", project.pk)
    return render(request, "intake/brd_edit.html", {"project": project, "brd": brd, "form": form})


@login_required
def brd_history(request, pk):
    project = get_project(request.user, pk)
    require(request.user, "brd.view", project)
    brd = get_object_or_404(BRD, project=project)
    left_number = request.GET.get("a")
    right_number = request.GET.get("b")
    versions = list(brd.versions.select_related("created_by", "source_entry"))

    right = brd.versions.filter(number=right_number).first() or (versions[0] if versions else None)
    left = brd.versions.filter(number=left_number).first() or (versions[1] if len(versions) > 1 else None)

    diff_lines = []
    if right:
        diff_lines = list(
            difflib.unified_diff(
                (left.markdown if left else "").splitlines(),
                right.markdown.splitlines(),
                fromfile=f"v{left.number}" if left else "empty",
                tofile=f"v{right.number}",
                lineterm="",
                n=3,
            )
        )
    return render(
        request,
        "intake/brd_history.html",
        {
            "project": project,
            "brd": brd,
            "versions": versions,
            "left": left,
            "right": right,
            "diff_lines": diff_lines,
            "tab": "brd",
        },
    )


@login_required
def brd_download(request, pk, number=None):
    project = get_project(request.user, pk)
    brd = get_object_or_404(BRD, project=project)
    version = brd.versions.filter(number=number).first() if number else brd.current
    if not version:
        raise Http404("No BRD version yet")
    response = HttpResponse(version.markdown, content_type="text/markdown; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{project.key}-BRD-v{version.number}.md"'
    return response


@login_required
def api_entry_status(request, pk):
    """Polled by the timeline page so processing state updates without a reload."""
    entry = get_entry(request.user, pk)
    return JsonResponse(
        {
            "id": entry.pk,
            "status": entry.status,
            "status_label": entry.get_status_display(),
            "speakers": [
                {"id": s.pk, "label": s.label, "name": s.resolved_name, "named": s.is_named}
                for s in entry.speakers.all()
            ],
            "segments": entry.segments.count(),
            "brd_applied": entry.brd_applied,
            "log": entry.processing_log[-2000:],
        }
    )


@login_required
def service_status(request):
    require(request.user, "role.manage")
    return render(
        request,
        "intake/services.html",
        {
            "audio": audio_service.health(),
            "llm": llm.health() | {"model": llm.MODEL},
            "config": {
                "LLM_BASE_URL": llm.BASE_URL,
                "LLM_MODEL": llm.MODEL,
                "DENOISE_URL": audio_service.DENOISE_URL,
                "DENOISE_MODEL": audio_service.DENOISE_MODEL,
                "ASR_URL": audio_service.ASR_URL,
                "ASR_MODEL": audio_service.ASR_MODEL,
                "DIAR_MODEL": audio_service.DIAR_MODEL,
            },
        },
    )
