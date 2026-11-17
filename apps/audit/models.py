from django.conf import settings
from django.db import models

MODULES = [
    ("auth", "Login/Logout"),
    ("users", "Users"),
    ("permissions", "Permissions"),
    ("projects", "Projects"),
    ("vms", "VMs"),
    ("org", "Organisation"),
    ("tickets", "Tickets"),
    ("sprints", "Sprints"),
    ("brd", "Timeline / BRD"),
    ("ai", "AI Models"),
    ("bulk_import", "Bulk Import"),
    ("chat", "Chat"),
    ("mcp", "Claude / MCP"),
    ("devlog", "Dev Log"),
    ("design", "Design"),
    ("timesheet", "Timesheet"),
    ("jobs", "AI queue"),
]

ACTIONS = [
    ("create", "Create"),
    ("edit", "Edit"),
    ("delete", "Delete"),
    ("login", "Login"),
    ("logout", "Logout"),
    ("assign_vm", "Assign VM"),
    ("unassign_vm", "Unassign VM"),
    ("permission_change", "Permission Change"),
    ("activate", "Activate"),
    ("deactivate", "Deactivate"),
    ("download", "Download"),
    ("action", "Action"),
]

STATUSES = [
    ("success", "Success"),
    ("failed", "Failed"),
]


ACTION_VERBS = {
    "create": "Created", "edit": "Edited", "delete": "Deleted", "activate": "Activated", "deactivate": "Deactivated",
    "assign_vm": "Assigned a VM to", "unassign_vm": "Removed a VM from",
}
# modules whose entries are hand-written (the record text is a plain name, not "Kind › name")
_PLAIN_MODULES = {"auth", "users", "permissions", "projects", "vms"}


class AuditLog(models.Model):
    """A single recorded action, for the per-user Activity Log. Append-only."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_logs"
    )
    action = models.CharField(max_length=40, choices=ACTIONS)
    module = models.CharField(max_length=40, choices=MODULES)
    object_repr = models.CharField(max_length=255, blank=True, help_text="Human-readable record/entity affected")
    status = models.CharField(max_length=10, choices=STATUSES, default="success")
    details = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["actor", "created_at"], name="audit_actor_created_idx"),
            models.Index(fields=["module"], name="audit_module_idx"),
            models.Index(fields=["action"], name="audit_action_idx"),
            models.Index(fields=["status"], name="audit_status_idx"),
        ]

    def __str__(self):
        return f"{self.actor} {self.action} ({self.module}) — {self.object_repr}"

    @property
    def summary(self):
        """One readable sentence: what the person actually did, e.g. Created ticket “PW-12 Fix login”."""
        name = self.object_repr
        if self.action == "login":
            return f"Failed sign-in attempt as “{name}”" if self.status == "failed" else "Signed in"
        if self.action == "logout":
            return "Signed out"
        if self.action == "password_change":
            return "Changed their password"
        if self.action == "permission_change":
            return f"Changed permissions — {name}" if name else "Changed permissions"
        if self.action == "download":
            return f"Downloaded “{name}”" if name else "Downloaded a file"
        if self.action == "action":
            return f"{'Tried' if self.status == 'failed' else 'Did'}: {name}" if name else "Did something"
        verb = ACTION_VERBS.get(self.action, self.get_action_display())
        module = self.get_module_display()
        if self.module not in _PLAIN_MODULES and " › " in name:
            kind, name = name.split(" › ", 1)
            return f"{verb} {kind.lower()} “{name}”"
        if self.module == "users":
            return f"{verb} user “{name}”" if name else f"{verb} a user"
        if self.module == "projects":
            return f"{verb} project “{name}”" if name else f"{verb} a project"
        if self.module == "vms":
            return f"{verb} VM “{name}”" if name else f"{verb} a VM"
        return f"{verb} a record in {module}" + (f" — “{name}”" if name else "")
