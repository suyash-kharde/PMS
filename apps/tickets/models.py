from django.conf import settings
from django.db import models
from django.db.models import Max
from django.urls import reverse
from django.utils import timezone

from apps.projects.models import PRIORITIES

# What kind of thing is being raised. `bug` and `change_request` are the two the
# request centred on; the rest cover the "and other things" catch-all.
TICKET_TYPES = [
    ("bug", "Bug"),
    ("change_request", "Change request"),
    ("feature", "Feature request"),
    ("task", "Task / work item"),
    ("other", "Other"),
]

# A triage lifecycle, deliberately separate from the delivery board (WorkflowState).
# new -> triaged -> accepted -> in_progress -> (blocked) -> solved -> resolved -> closed
# solved = the work is done and handed on (e.g. "Waiting for Sadhna Testing"); it is still open until resolved.
# rejected is a terminal "won't do / not a bug / duplicate" outcome.
TICKET_STATUS = [
    ("new", "New"),
    ("triaged", "Triaged"),
    ("accepted", "Accepted"),
    ("in_progress", "In progress"),
    ("blocked", "Blocked"),
    ("solved", "Solved"),
    ("resolved", "Resolved"),
    ("closed", "Closed"),
    ("rejected", "Rejected"),
]
OPEN_STATUSES = {"new", "triaged", "accepted", "in_progress", "blocked", "solved"}
CLOSED_STATUSES = {"resolved", "closed", "rejected"}

SEVERITY = [
    (1, "Blocker"),
    (2, "Major"),
    (3, "Minor"),
    (4, "Trivial"),
]


def attachment_upload_to(instance, filename):
    stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    return f"projects/{instance.ticket.project_id}/tickets/{instance.ticket_id}/{stamp}-{filename}"


class Ticket(models.Model):
    """An item raised against a project: a bug, a change request or anything else."""

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="tickets")
    number = models.PositiveIntegerField(editable=False, help_text="Per-project sequence, e.g. PLAT-14")

    type = models.CharField(max_length=20, choices=TICKET_TYPES, default="bug")
    title = models.CharField(max_length=240)
    description = models.TextField(blank=True)
    component = models.CharField(max_length=120, blank=True, help_text="Area / module the ticket concerns")

    status = models.CharField(max_length=12, choices=TICKET_STATUS, default="new")
    priority = models.PositiveSmallIntegerField(choices=PRIORITIES, default=3)
    severity = models.PositiveSmallIntegerField(
        choices=SEVERITY, null=True, blank=True, help_text="Impact of a bug / incident"
    )

    # bug / incident detail — optional, shown for those types
    steps_to_reproduce = models.TextField(blank=True)
    expected_result = models.TextField(blank=True)
    actual_result = models.TextField(blank=True)
    environment = models.CharField(max_length=200, blank=True, help_text="Browser, OS, app version…")

    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="reported_tickets"
    )
    team = models.ForeignKey(
        "accounts.Team", null=True, blank=True, on_delete=models.SET_NULL, related_name="tickets",
        help_text="Narrows the assignee list to this team's members",
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_tickets"
    )
    watchers = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="watched_tickets"
    )
    labels = models.ManyToManyField("projects.Label", blank=True, related_name="tickets")

    linked_task = models.ForeignKey(
        "projects.Task", null=True, blank=True, on_delete=models.SET_NULL, related_name="tickets",
        help_text="Delivery task this ticket was turned into",
    )
    duplicate_of = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="duplicates"
    )

    due_date = models.DateField(null=True, blank=True)
    resolution = models.TextField(blank=True, help_text="How it was resolved / why it was rejected")
    handoff_status = models.CharField(
        max_length=200, blank=True, help_text="Where a Solved ticket goes next, e.g. Waiting for Sadhna Testing"
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = ("project", "number")
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["assignee"]),
            models.Index(fields=["type"]),
        ]

    def __str__(self):
        return f"{self.key} · {self.title}"

    def get_absolute_url(self):
        return reverse("tickets:detail", args=[self.pk])

    def save(self, *args, **kwargs):
        if not self.number and self.project_id:
            last = Ticket.objects.filter(project_id=self.project_id).aggregate(n=Max("number"))["n"]
            self.number = (last or 0) + 1
        super().save(*args, **kwargs)

    @property
    def key(self):
        return f"{self.project.key}-{self.number}"

    @property
    def is_open(self):
        return self.status in OPEN_STATUSES

    @property
    def assigned_by(self):
        """Who last assigned this ticket (from its history); the reporter when nobody is recorded."""
        event = self.events.filter(verb__startswith="assigned to").select_related("actor").first()
        person = event.actor if event and event.actor_id else self.reporter
        return person.display_name if person else ""

    @property
    def solved_tooltip(self):
        """The text behind the (i) icon on a Solved ticket."""
        return f"Final status: {self.handoff_status or self.default_handoff}"

    @property
    def is_bugish(self):
        return self.type == "bug"

    @property
    def age_days(self):
        return (timezone.now() - self.created_at).days

    @property
    def default_handoff(self):
        """Where a Solved ticket goes next: back to whoever raised it, to test. Worked out, never typed."""
        who = self.reporter.display_name if self.reporter_id else ""
        return f"Waiting for {who} Testing/approval" if who else "Waiting for testing/approval"

    def apply_status(self, status):
        """Move to `status` and keep the resolved/closed timestamps in step. A Solved ticket records its handoff
        ("Waiting for <the person who raised it> Testing/approval"); the note is cleared when it leaves Solved."""
        self.status = status
        self.handoff_status = self.default_handoff if status == "solved" else ""
        self.resolved_at = timezone.now() if status == "resolved" else None
        self.closed_at = timezone.now() if status in ("closed", "rejected") else None


class TicketComment(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ticket_comments")
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.author}: {self.body[:40]}"


class TicketAttachment(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to=attachment_upload_to)
    original_name = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return self.original_name or self.file.name

    @property
    def is_image(self):
        name = (self.original_name or self.file.name).lower()
        return name.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"))


class TicketEvent(models.Model):
    """Audit trail — one row per state change, assignment, comment, etc."""

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="ticket_events"
    )
    verb = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.actor} {self.verb}"

    @classmethod
    def log(cls, ticket, actor, verb):
        return cls.objects.create(
            ticket=ticket,
            actor=actor if getattr(actor, "is_authenticated", False) else None,
            verb=verb,
        )
