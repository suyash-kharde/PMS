"""Rebuilds a project's RAG index: pulls every textual record on the project,
chunks it, embeds the chunks in a few batched calls, and upserts Chunk rows.
Sources with no more text (e.g. a task description was cleared) get their
old chunks dropped so search never surfaces something that no longer exists.
"""

from apps.projects.models import ProjectMembership, Task

from ..models import Chunk
from .chunk import chunk_text
from .embed import embed_texts

BATCH_SIZE = 64


def _speaker_labeled_transcript(entry):
    """"SPEAKER_00: ..." isn't searchable by name — resolve each segment's speaker
    to a real name (or whatever it's been named so far) so the chat agent can
    actually answer "what did <person> say" questions. Falls back to the flat
    ASR transcript for entries that were never diarized into segments."""
    segments = list(entry.segments.all())
    if not segments:
        return entry.transcript

    speaker_by_label = {s.label: s for s in entry.speakers.all()}
    lines = []
    for seg in segments:
        if not seg.text.strip():
            continue
        speaker = speaker_by_label.get(seg.speaker_label)
        name = speaker.resolved_name if speaker else seg.speaker_label
        lines.append(f"{name}: {seg.text.strip()}")
    return "\n".join(lines)


def _gather(project):
    """-> list of (source_type, source_id, label, text) — one text blob per source record."""
    sources = []

    sources.append((
        "project", project.id, project.name,
        f"Project {project.key}: {project.name}\n{project.description}".strip(),
    ))

    for entry in project.timeline.all().prefetch_related("segments", "speakers"):
        parts = [entry.title, entry.body, _speaker_labeled_transcript(entry), entry.summary]
        text = "\n\n".join(p for p in parts if p)
        if text.strip():
            sources.append(("timeline", entry.id, entry.title, text))

    brd = getattr(project, "brd", None)
    if brd is not None and brd.markdown:
        sources.append(("brd", brd.id, brd.title or "BRD", brd.markdown))

    for task in project.tasks.select_related("state", "assignee").prefetch_related("comments__author"):
        parts = [task.title, task.description]
        parts += [f"Comment by {c.author.display_name}: {c.body}" for c in task.comments.all()]
        parts.append(f"Status: {task.state.name if task.state_id else 'no status'}, "
                      f"Priority: {task.get_priority_display()}")
        if task.assignee_id:
            parts.append(f"Assigned to: {task.assignee.display_name}")
        text = "\n".join(p for p in parts if p)
        sources.append(("task", task.id, task.title, text))

    for m in project.memberships.select_related("user", "role"):
        parts = [
            f"{m.user.display_name} — {m.get_side_display()} side",
            f"Position: {m.position}" if m.position else "",
            f"Role: {m.role.name}" if m.role else "",
            f"Role description: {m.role_description}" if m.role_description else "",
            f"Responsibility: {m.responsibility}" if m.responsibility else "",
            f"Remarks: {m.remarks}" if m.remarks else "",
            f"Contact: {m.user.email} {m.user.phone}".strip(),
        ]
        text = "\n".join(p for p in parts if p)
        sources.append(("membership", m.id, m.user.display_name, text))

    if project.customer_id:
        c = project.customer
        parts = [
            f"Customer: {c.name} ({c.industry})" if c.industry else f"Customer: {c.name}",
            f"Description: {c.description}" if c.description else "",
            f"Notes: {c.notes}" if c.notes else "",
            f"Contact: {c.contact_email} {c.contact_phone}".strip(),
        ]
        text = "\n".join(p for p in parts if p)
        sources.append(("customer", c.id, c.name, text))

    return sources


def reindex_project(project):
    sources = _gather(project)

    # flatten to (source_type, source_id, label, ordinal, chunk_text), and remember
    # how many chunks each source ended up with so leftover higher ordinals can be dropped
    rows, chunk_counts = [], {}
    for source_type, source_id, label, text in sources:
        pieces = chunk_text(text)
        chunk_counts[(source_type, source_id)] = len(pieces)
        for ordinal, piece in enumerate(pieces):
            rows.append((source_type, source_id, label, ordinal, piece))

    # drop chunks for records that no longer exist, have gone empty, or shrank
    from django.db.models import Q

    keep = Q(pk__in=[])
    for (source_type, source_id), count in chunk_counts.items():
        keep |= Q(source_type=source_type, source_id=source_id, ordinal__lt=count)
    Chunk.objects.filter(project=project).exclude(keep).delete()

    embeddings = []
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        embeddings.extend(embed_texts([r[4] for r in batch]))

    for (source_type, source_id, label, ordinal, piece), vector in zip(rows, embeddings):
        Chunk.objects.update_or_create(
            project=project, source_type=source_type, source_id=source_id, ordinal=ordinal,
            defaults={"label": label, "text": piece, "embedding": vector},
        )

    return len(rows)
