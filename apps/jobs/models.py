"""The AI job queue: one row per piece of slow work (AI calls, audio processing, workspace set-up).

The web app only adds rows; the worker claims them (SELECT ... FOR UPDATE SKIP LOCKED under a
short advisory lock), runs them and records how long they took, which is what the queue
position and "usually ready within" estimates are computed from.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

JOB_STATUS = [
    ("queued", "Queued"),
    ("running", "Running"),
    ("done", "Done"),
    ("failed", "Failed"),
    ("cancelled", "Cancelled"),
]
ACTIVE = ("queued", "running")


class Job(models.Model):
    kind = models.CharField(max_length=30)
    lane = models.CharField(max_length=20, help_text="Jobs in a lane share its worker slots (audio, ai, setup)")
    label = models.CharField(max_length=200, help_text="What it is, for people: e.g. 'BRD update · DDMS'")
    project = models.ForeignKey("projects.Project", null=True, blank=True, on_delete=models.CASCADE,
                                related_name="jobs")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="jobs")
    target_id = models.BigIntegerField(null=True, blank=True, help_text="The entry / plan run / workspace it works on")
    params = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=10, choices=JOB_STATUS, default="queued")
    dedupe_key = models.CharField(max_length=80, blank=True,
                                  help_text="A second request with the same key joins the waiting job")
    serial_key = models.CharField(max_length=80, blank=True,
                                  help_text="Jobs with the same key never run at the same time (one per project)")
    size = models.FloatField(default=0, help_text="Audio file size in MB, used for the time estimate")
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=3)
    run_after = models.DateTimeField(default=timezone.now, help_text="Not picked before this (retry back-off)")
    created_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)
    result_url = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "lane", "created_at"], name="job_pick_idx"),
            models.Index(fields=["kind", "status", "finished_at"], name="job_timing_idx"),
            models.Index(fields=["dedupe_key", "status"], name="job_dedupe_idx"),
        ]

    def __str__(self):
        return f"#{self.pk} {self.label} ({self.status})"

    @property
    def is_active(self):
        return self.status in ACTIVE

    @property
    def duration(self):
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None
