"""Entry processing: extract text, denoise audio, transcribe + diarize, then update the BRD."""

import os

from django.core.files.base import ContentFile
from django.db import transaction

from apps.notify import services as notifications

from ..models import MediaAsset, Speaker, TimelineEntry, TranscriptSegment
from . import audio, brd, llm

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".log", ".csv", ".json", ".srt", ".vtt"}
AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".ogg", ".opus", ".flac", ".webm", ".aac", ".mp4"}
DOCUMENT_SUFFIXES = {".pdf", ".docx", ".pptx"}


def classify(filename: str) -> str:
    suffix = os.path.splitext(filename or "")[1].lower()
    if suffix in AUDIO_SUFFIXES:
        return "audio"
    if suffix in TEXT_SUFFIXES:
        return "text"
    if suffix in DOCUMENT_SUFFIXES:
        return "document"
    return "unknown"


def process_entry(entry: TimelineEntry, regenerate_brd=True) -> TimelineEntry:
    entry.status = "processing"
    entry.save(update_fields=["status", "updated_at"])
    entry.log("processing started")

    try:
        if entry.upload:
            kind = classify(entry.original_name or entry.upload.name)
            if kind == "audio":
                handle_audio(entry)
            elif kind == "text":
                handle_text(entry)
            elif kind == "document":
                handle_document(entry)
            else:
                entry.log(f"unsupported upload type, keeping the file as an attachment only")
        elif entry.kind == "audio":
            entry.log("marked as audio but no file attached")
    except Exception as exc:  # noqa: BLE001 - surface the failure on the entry itself
        entry.status = "failed"
        entry.save(update_fields=["status", "updated_at"])
        entry.log(f"FAILED: {type(exc).__name__}: {exc}")
        notifications.entry_failed(entry, f"{type(exc).__name__}: {exc}")
        return entry

    if entry.speakers.filter(display_name="", user__isnull=True).exists():
        entry.status = "awaiting_speakers"
        entry.save(update_fields=["status", "updated_at"])
        entry.log("waiting for speaker names before the BRD is updated")
        notifications.entry_processed(entry)
        reindex_best_effort(entry)
        return entry

    entry.status = "ready"
    entry.save(update_fields=["status", "updated_at"])
    notifications.entry_processed(entry)

    if regenerate_brd and entry.include_in_brd:
        update_brd(entry)
    reindex_best_effort(entry)
    return entry


def reindex_best_effort(entry: TimelineEntry):
    """Keep the project's chat index in sync. Best-effort: the embedding
    endpoint being down shouldn't fail an otherwise-successful entry."""
    try:
        from apps.rag.services.index import reindex_project

        reindex_project(entry.project)
    except Exception as exc:  # noqa: BLE001
        entry.log(f"chat index update skipped: {type(exc).__name__}: {exc}")


def handle_text(entry: TimelineEntry):
    entry.upload.open("rb")
    try:
        raw = entry.upload.read()
    finally:
        entry.upload.close()
    text = raw.decode("utf-8", errors="replace")
    entry.body = (entry.body + "\n\n" + text).strip() if entry.body else text
    entry.save(update_fields=["body", "updated_at"])
    entry.log(f"extracted {len(text)} characters of text")


def handle_document(entry: TimelineEntry):
    entry.upload.open("rb")
    try:
        raw = entry.upload.read()
    finally:
        entry.upload.close()
    filename = entry.original_name or os.path.basename(entry.upload.name)
    suffix = os.path.splitext(filename)[1].lower()

    extractor = {".pdf": _extract_pdf, ".docx": _extract_docx, ".pptx": _extract_pptx}[suffix]
    text = extractor(raw)

    if not text.strip():
        entry.log(f"no extractable text found in {filename} (scanned/image-only document?)")
        return

    entry.body = (entry.body + "\n\n" + text).strip() if entry.body else text
    entry.save(update_fields=["body", "updated_at"])
    entry.log(f"extracted {len(text)} characters from {suffix.lstrip('.').upper()}")


def _extract_pdf(raw: bytes) -> str:
    from io import BytesIO

    from pypdf import PdfReader

    reader = PdfReader(BytesIO(raw))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(p.strip() for p in pages if p.strip())


def _extract_docx(raw: bytes) -> str:
    from io import BytesIO

    from docx import Document

    doc = Document(BytesIO(raw))
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _extract_pptx(raw: bytes) -> str:
    from io import BytesIO

    from pptx import Presentation

    prs = Presentation(BytesIO(raw))
    parts = []
    for i, slide in enumerate(prs.slides, start=1):
        texts = []
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                texts.append(shape.text_frame.text.strip())
            if shape.has_table:
                for row in shape.table.rows:
                    cells = [c.text.strip() for c in row.cells if c.text.strip()]
                    if cells:
                        texts.append(" | ".join(cells))
        if texts:
            parts.append(f"Slide {i}: " + " — ".join(texts))
    return "\n".join(parts)


def handle_audio(entry: TimelineEntry):
    entry.upload.open("rb")
    try:
        raw = entry.upload.read()
    finally:
        entry.upload.close()
    filename = entry.original_name or os.path.basename(entry.upload.name)

    # reprocessing must replace the previous rendition, not pile up another row
    asset = entry.media_set.order_by("-created_at").first()
    if asset is None:
        asset = MediaAsset.objects.create(entry=entry, original=entry.upload.name)
    else:
        entry.media_set.exclude(pk=asset.pk).delete()
        if asset.denoised:
            asset.denoised.delete(save=False)
        asset.original = entry.upload.name
        asset.save(update_fields=["original", "denoised"])

    cleaned, denoise_meta = audio.denoise(raw, filename)
    asset.denoised.save(f"denoised-{os.path.splitext(filename)[0]}.wav", ContentFile(cleaned), save=False)
    asset.denoise_engine = denoise_meta.get("engine", "")
    asset.duration_seconds = denoise_meta.get("duration", 0)
    asset.save()
    entry.log(f"noise reduction done via '{asset.denoise_engine}' ({asset.duration_seconds:.1f}s audio)")

    result = audio.transcribe(cleaned, f"denoised-{filename}.wav", diarize=True)
    asset.asr_engine = result.get("asr_engine") or result.get("engine", "")
    asset.diarization_engine = result.get("diarization_engine", "")
    asset.language = result.get("language", "")
    asset.turns = result.get("turns", 0)
    asset.processing_seconds = result.get("processing_seconds", 0)
    asset.duration_seconds = result.get("duration", asset.duration_seconds)
    asset.save()

    segments = result.get("segments") or []
    with transaction.atomic():
        entry.segments.all().delete()
        TranscriptSegment.objects.bulk_create(
            [
                TranscriptSegment(
                    entry=entry,
                    index=index,
                    start=segment.get("start", 0),
                    end=segment.get("end", 0),
                    speaker_label=segment.get("speaker", "SPEAKER_00"),
                    text=(segment.get("text") or "").strip(),
                )
                for index, segment in enumerate(segments)
            ]
        )
        talk_time = {}
        for segment in segments:
            label = segment.get("speaker", "SPEAKER_00")
            talk_time[label] = talk_time.get(label, 0) + max(0.0, segment.get("end", 0) - segment.get("start", 0))
        for label in sorted(set(result.get("speakers") or talk_time.keys())):
            Speaker.objects.update_or_create(
                entry=entry, label=label, defaults={"talk_seconds": round(talk_time.get(label, 0), 1)}
            )

    entry.transcript = result.get("text", "")
    entry.kind = "audio"
    entry.save(update_fields=["transcript", "kind", "updated_at"])

    if not segments:
        entry.log(
            f"no speech recognised in {asset.duration_seconds:.1f}s of audio "
            f"({result.get('turns', 0)} turn(s) sent to {audio.ASR_MODEL}). "
            "The recording may be silent, music, or in an unsupported language."
        )
        return
    entry.log(
        f"diarized via '{asset.diarization_engine}' into {result.get('turns', 0)} turn(s); "
        f"transcribed via '{asset.asr_engine}' ({audio.ASR_MODEL}, language {asset.language or 'auto'}): "
        f"{len(segments)} segments, {len(result.get('speakers') or [])} speakers"
    )


def update_brd(entry: TimelineEntry, user=None):
    try:
        version = brd.regenerate(entry.project, entries=[entry], user=user or entry.author)
    except llm.LLMError as exc:
        entry.log(f"BRD update skipped: {exc}")
        return None
    entry.log(f"BRD updated to v{version.number}")  # regenerate() already notified
    return version
