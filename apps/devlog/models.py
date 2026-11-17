from django.conf import settings
from django.db import models
from django.urls import reverse

KINDS = [
    ("feature", "Feature"),
    ("bugfix", "Bug fix"),
    ("refactor", "Refactor"),
    ("test", "Tests"),
    ("docs", "Docs"),
    ("chore", "Chore"),
    ("research", "Research"),
    ("other", "Other"),
]

STATUS = [
    ("in_progress", "In progress"),
    ("completed", "Completed"),
    ("blocked", "Blocked"),
    ("needs_review", "Needs review"),
]

SOURCES = [
    ("mcp", "Claude Code (MCP)"),
    ("web", "Web UI"),
    ("admin", "Admin"),
]


class DevUpdate(models.Model):
    """One development-progress update recorded against a project — what was done, how far
    along, and which milestone it reaches. Claude Code pushes these through the MCP link;
    people can also add them by hand.
    """

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="dev_updates")

    headline = models.CharField(max_length=240, help_text="What was done")
    details = models.TextField(blank=True, help_text="Longer description (markdown)")
    kind = models.CharField(max_length=12, choices=KINDS, default="feature")
    status = models.CharField(max_length=12, choices=STATUS, default="in_progress")

    milestone = models.CharField(
        max_length=200, blank=True, help_text="Free-text milestone this work is progressing toward"
    )
    milestone_done = models.BooleanField(default=False, help_text="This update completes that milestone")

    # optional links to what the work concerns
    sprint = models.ForeignKey(
        "sprints.Sprint", null=True, blank=True, on_delete=models.SET_NULL, related_name="dev_updates"
    )
    requirement = models.ForeignKey(
        "sprints.Requirement", null=True, blank=True, on_delete=models.SET_NULL, related_name="dev_updates"
    )
    ticket = models.ForeignKey(
        "tickets.Ticket", null=True, blank=True, on_delete=models.SET_NULL, related_name="dev_updates"
    )
    task = models.ForeignKey(
        "projects.Task", null=True, blank=True, on_delete=models.SET_NULL, related_name="dev_updates"
    )

    files_changed = models.JSONField(default=list, blank=True, help_text="List of file paths touched")
    branch = models.CharField(max_length=200, blank=True)
    commit = models.CharField(max_length=120, blank=True)
    pr_url = models.URLField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="dev_updates"
    )
    source = models.CharField(max_length=8, choices=SOURCES, default="web")
    via_token = models.ForeignKey(
        "mcp.ProjectMCPToken", null=True, blank=True, on_delete=models.SET_NULL, related_name="dev_updates"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "status"], name="devlog_project_status_idx"),
            models.Index(fields=["project", "milestone"], name="devlog_project_milestone_idx"),
        ]

    def __str__(self):
        return f"{self.project.key} · {self.headline}"

    def get_absolute_url(self):
        return reverse("devlog:update_detail", args=[self.pk])

    @property
    def links(self):
        """[(label, url), …] for whatever this update is tied to."""
        out = []
        if self.sprint_id:
            out.append((self.sprint.key, self.sprint.get_absolute_url()))
        if self.requirement_id:
            out.append((self.requirement.key, self.requirement.get_absolute_url()))
        if self.ticket_id:
            out.append((self.ticket.key, self.ticket.get_absolute_url()))
        if self.task_id:
            out.append((f"Task {self.task_id}", self.task.get_absolute_url()))
        return out
