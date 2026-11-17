from django.conf import settings
from django.db import models

KINDS = [
    ("entry_processed", "Recording processed"),
    ("speakers_needed", "Speaker names needed"),
    ("brd_updated", "BRD updated"),
    ("entry_failed", "Processing failed"),
    ("ticket_new", "New ticket raised"),
    ("ticket_assigned", "Ticket assigned to you"),
    ("ticket_status", "Ticket status changed"),
    ("ticket_comment", "New comment on a ticket"),
    ("requirement_assigned", "Requirement assigned to you"),
    ("testcase_result", "Unit test failed / blocked"),
    ("sprint_plan", "Sprint plan generated"),
    ("mention", "Mention"),
    ("test", "Test"),
]


class PushSubscription(models.Model):
    """One browser/device that opted in to push. Endpoint is the unique handle."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_subscriptions"
    )
    endpoint = models.TextField(unique=True)
    p256dh = models.CharField(max_length=255)
    auth = models.CharField(max_length=255)
    user_agent = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    failures = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} · {self.endpoint[:40]}…"

    def as_subscription_info(self):
        return {"endpoint": self.endpoint, "keys": {"p256dh": self.p256dh, "auth": self.auth}}


class Notification(models.Model):
    """In-app copy of everything we push, so nothing is lost when push is denied."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField(max_length=30, choices=KINDS, default="mention")
    title = models.CharField(max_length=160)
    body = models.TextField(blank=True)
    url = models.CharField(max_length=400, blank=True)
    project = models.ForeignKey(
        "projects.Project", null=True, blank=True, on_delete=models.CASCADE, related_name="notifications"
    )
    read_at = models.DateTimeField(null=True, blank=True)
    pushed = models.BooleanField(default=False)
    push_error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "read_at"])]

    def __str__(self):
        return f"{self.user}: {self.title}"

    @property
    def is_read(self):
        return self.read_at is not None
