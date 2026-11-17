import difflib

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone

ENTRY_KINDS = [
    ("note", "Note"),
    ("text", "Text / document"),
    ("audio", "Audio recording"),
    ("meeting", "Meeting"),
    ("decision", "Decision"),
    ("change_request", "Change request"),
]
ENTRY_STATUS = [
    ("draft", "Draft"),
    ("queued", "Queued"),
    ("processing", "Processing"),
    ("awaiting_speakers", "Awaiting speaker names"),
    ("ready", "Ready"),
    ("failed", "Failed"),
]


def upload_to(instance, filename):
    project_id = instance.project_id if hasattr(instance, "project_id") else instance.entry.project_id
    stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    return f"projects/{project_id}/intake/{stamp}-{filename}"


class TimelineEntry(models.Model):
    """One thing that happened on a project: a note, an uploaded document or a recording."""

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="timeline")
    kind = models.CharField(max_length=20, choices=ENTRY_KINDS, default="note")
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True, help_text="Typed notes, or the extracted text of an upload")
    upload = models.FileField(upload_to=upload_to, blank=True, null=True)
    original_name = models.CharField(max_length=255, blank=True)

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="timeline_entries"
    )
    occurred_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    status = models.CharField(max_length=20, choices=ENTRY_STATUS, default="draft")
    processing_log = models.TextField(blank=True)
    transcript = models.TextField(blank=True)
    summary = models.TextField(blank=True)

    include_in_brd = models.BooleanField(default=True)
    brd_applied = models.BooleanField(default=False, help_text="Already folded into a BRD version")

    class Meta:
        ordering = ["-occurred_at", "-id"]
        verbose_name_plural = "timeline entries"

    def __str__(self):
        return f"{self.project.key} · {self.title}"

    def get_absolute_url(self):
        return reverse("intake:entry_detail", args=[self.pk])

    @property
    def is_audio(self):
        return self.kind == "audio" or bool(self.media_set.exists())

    @property
    def unnamed_speakers(self):
        """A voice counts as named once it has a typed name OR a mapped project member."""
        return self.speakers.filter(display_name="", user__isnull=True).count()

    def content_for_llm(self):
        """The text the BRD writer actually reads, with speakers resolved to real names."""
        if self.segments.exists():
            names = {s.label: s.resolved_name for s in self.speakers.all()}
            lines = []
            for seg in self.segments.all():
                who = names.get(seg.speaker_label) or seg.speaker_label
                lines.append(f"[{seg.timecode}] {who}: {seg.text}")
            return "\n".join(lines)
        return self.body or self.transcript

    def log(self, message):
        stamp = timezone.now().strftime("%H:%M:%S")
        self.processing_log = f"{self.processing_log}\n[{stamp}] {message}".strip()
        self.save(update_fields=["processing_log", "updated_at"])


class MediaAsset(models.Model):
    """Original upload plus the noise-reduced rendition produced by the hush service."""

    entry = models.ForeignKey(TimelineEntry, on_delete=models.CASCADE, related_name="media_set")
    original = models.FileField(upload_to=upload_to)
    denoised = models.FileField(upload_to=upload_to, blank=True, null=True)
    duration_seconds = models.FloatField(default=0)
    sample_rate = models.PositiveIntegerField(default=16000)
    denoise_engine = models.CharField(max_length=40, blank=True)
    asr_engine = models.CharField(max_length=40, blank=True)
    diarization_engine = models.CharField(max_length=40, blank=True)
    language = models.CharField(max_length=20, blank=True, help_text="Detected or requested language")
    turns = models.PositiveIntegerField(default=0, help_text="Speaker turns sent to the ASR model")
    processing_seconds = models.FloatField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"media #{self.pk} of entry {self.entry_id}"


class Speaker(models.Model):
    """A diarized voice. Anonymous until someone names it and maps it to a project member."""

    entry = models.ForeignKey(TimelineEntry, on_delete=models.CASCADE, related_name="speakers")
    label = models.CharField(max_length=40, help_text="Diarizer label, e.g. SPEAKER_00")
    display_name = models.CharField(max_length=120, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="speaker_links"
    )
    side = models.CharField(max_length=10, blank=True, choices=[("client", "Client"), ("internal", "Our side")])
    talk_seconds = models.FloatField(default=0)
    notes = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ["label"]
        unique_together = ("entry", "label")

    def __str__(self):
        return self.resolved_name

    @property
    def resolved_name(self):
        if self.user_id:
            return self.user.display_name
        return self.display_name or self.label

    @property
    def is_named(self):
        return bool(self.display_name or self.user_id)


class TranscriptSegment(models.Model):
    entry = models.ForeignKey(TimelineEntry, on_delete=models.CASCADE, related_name="segments")
    index = models.PositiveIntegerField(default=0)
    start = models.FloatField(default=0)
    end = models.FloatField(default=0)
    speaker_label = models.CharField(max_length=40, default="SPEAKER_00")
    text = models.TextField(blank=True)

    class Meta:
        ordering = ["index"]

    def __str__(self):
        return f"{self.speaker_label}: {self.text[:40]}"

    @property
    def timecode(self):
        return f"{int(self.start) // 60:02d}:{int(self.start) % 60:02d}"


class BRD(models.Model):
    """The Business Requirement Document — one per project, versioned on every update."""

    project = models.OneToOneField("projects.Project", on_delete=models.CASCADE, related_name="brd")
    title = models.CharField(max_length=200, blank=True)
    is_frozen = models.BooleanField(default=False, help_text="Locked as final — no further edits or regeneration")
    frozen_at = models.DateTimeField(null=True, blank=True)
    frozen_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"BRD · {self.project.key}"

    def get_absolute_url(self):
        return reverse("intake:brd", args=[self.project_id])

    @property
    def current(self):
        return self.versions.first()

    @property
    def markdown(self):
        version = self.current
        return version.markdown if version else ""

    def next_number(self):
        latest = self.versions.first()
        return (latest.number + 1) if latest else 1

    def add_version(self, markdown, summary="", source_entry=None, created_by=None, model_name="", meta=None):
        previous = self.current
        version = BRDVersion.objects.create(
            brd=self,
            number=self.next_number(),
            markdown=markdown,
            summary=summary or "Manual update",
            source_entry=source_entry,
            created_by=created_by,
            model_name=model_name,
            meta=meta or {},
            diff=BRDVersion.build_diff(previous.markdown if previous else "", markdown),
        )
        self.save(update_fields=["updated_at"])
        return version


class BRDVersion(models.Model):
    """Immutable snapshot. Nothing edits a version in place, so history stays auditable."""

    brd = models.ForeignKey(BRD, on_delete=models.CASCADE, related_name="versions")
    number = models.PositiveIntegerField()
    markdown = models.TextField()
    summary = models.TextField(blank=True, help_text="What changed and why")
    diff = models.TextField(blank=True)
    source_entry = models.ForeignKey(
        TimelineEntry, null=True, blank=True, on_delete=models.SET_NULL, related_name="brd_versions"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="brd_versions"
    )
    model_name = models.CharField(max_length=120, blank=True)
    meta = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-number"]
        unique_together = ("brd", "number")

    def __str__(self):
        return f"{self.brd.project.key} BRD v{self.number}"

    @staticmethod
    def build_diff(old: str, new: str) -> str:
        return "\n".join(
            difflib.unified_diff(
                (old or "").splitlines(),
                (new or "").splitlines(),
                fromfile="previous",
                tofile="current",
                lineterm="",
                n=2,
            )
        )

    @property
    def stats(self):
        added = sum(1 for line in self.diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in self.diff.splitlines() if line.startswith("-") and not line.startswith("---"))
        return {"added": added, "removed": removed, "words": len(self.markdown.split())}
