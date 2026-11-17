import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

WORKSPACE_STATUS = [
    ("queued", "Queued"),
    ("provisioning", "Provisioning"),
    ("ready", "Ready"),
    ("failed", "Failed"),
    ("disabled", "Disabled"),
]

# who-can values Penpot accepts for a share link
SHARE_WHO = ["all", "team"]


def _clean_base(url: str) -> str:
    return (url or "").rstrip("/")


def generate_bot_password() -> str:
    """A long random password for the per-project Penpot bot account.

    Penpot enforces a minimum length of 8; we go well past that and mix classes so
    any future policy still accepts it.
    """
    return "Pw-" + secrets.token_urlsafe(24) + "-9Z"


class PenpotWorkspace(models.Model):
    """The isolated Penpot workspace that backs one Planwise project.

    Isolation is by *account*: each project gets its own Penpot profile (a "bot"),
    that profile's own default team, one Penpot project inside it, and a starter
    file. The bot belongs to exactly one team, so its credentials — and any access
    token minted for it — can only ever reach this one project's designs.

    Provisioning is done by the background worker (`penpot_provision` /
    `process_queue`) against the Penpot RPC API; the Design tab polls `status`.
    """

    project = models.OneToOneField(
        "projects.Project", on_delete=models.CASCADE, related_name="penpot_workspace"
    )

    status = models.CharField(max_length=12, choices=WORKSPACE_STATUS, default="queued")
    phase = models.CharField(max_length=160, blank=True, help_text="Current step, shown live")
    last_error = models.TextField(blank=True)
    attempts = models.PositiveIntegerField(default=0)

    # the Penpot instance this workspace lives on (frozen at provision time so links
    # keep working even if the setting later changes)
    base_url = models.URLField(blank=True)

    # the per-project bot account
    bot_email = models.EmailField(blank=True)
    bot_fullname = models.CharField(max_length=160, blank=True)
    bot_password = models.CharField(max_length=128, blank=True, help_text="Bot login password (sensitive)")
    profile_id = models.CharField(max_length=40, blank=True)

    # an access token for the bot — stored for the team's own tooling; note the
    # Planwise↔Penpot server calls use session auth, see services.PenpotClient
    access_token = models.CharField(max_length=600, blank=True, help_text="Bot API access token (sensitive)")
    access_token_id = models.CharField(max_length=40, blank=True)

    # the Penpot objects
    team_id = models.CharField(max_length=40, blank=True)
    team_name = models.CharField(max_length=250, blank=True)
    penpot_project_id = models.CharField(max_length=40, blank=True)
    penpot_project_name = models.CharField(max_length=250, blank=True)
    file_id = models.CharField(max_length=40, blank=True, help_text="The starter UI file")
    file_name = models.CharField(max_length=250, blank=True)

    # a public, no-login view link for stakeholders (Penpot share link)
    share_link_id = models.CharField(max_length=40, blank=True)
    share_url = models.URLField(blank=True)

    provisioned_at = models.DateTimeField(null=True, blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Penpot workspace"

    def __str__(self):
        return f"{self.project.key} · Penpot ({self.status})"

    # ------------------------------------------------------------------ helpers
    @property
    def is_ready(self):
        return self.status == "ready" and bool(self.file_id)

    @property
    def is_pending(self):
        return self.status in ("queued", "provisioning")

    @property
    def base(self):
        return _clean_base(self.base_url or getattr(settings, "PENPOT_BASE_URL", ""))

    @property
    def dashboard_url(self):
        if not (self.base and self.team_id):
            return ""
        return f"{self.base}/#/dashboard/team/{self.team_id}"

    @property
    def workspace_url(self):
        """The file editor — for designers with a Penpot login on the team."""
        if not (self.base and self.penpot_project_id and self.file_id):
            return ""
        return f"{self.base}/#/workspace/{self.penpot_project_id}/{self.file_id}"

    @property
    def viewer_url(self):
        """The built-in viewer for the file (still needs a Penpot login)."""
        if not (self.base and self.file_id):
            return ""
        return f"{self.base}/#/view/{self.file_id}"

    def invite_accept_url(self, token: str) -> str:
        return f"{self.base}/#/auth/verify-token?token={token}" if self.base else ""

    @property
    def masked_password(self):
        return _mask(self.bot_password)

    @property
    def masked_token(self):
        return _mask(self.access_token)

    def mark(self, status, phase="", error="", save=True):
        self.status = status
        self.phase = phase
        if error:
            self.last_error = error[:4000]
        if save:
            self.save(update_fields=["status", "phase", "last_error", "updated_at"])


def _mask(value: str) -> str:
    if not value:
        return "—"
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:3]}…{value[-3:]} ({len(value)} chars)"


class PenpotInvite(models.Model):
    """A team invitation Planwise asked Penpot to send for a project member.

    Penpot returns the invite token in the API response, so we keep a direct
    accept link even when SMTP delivery is slow or disabled.
    """

    workspace = models.ForeignKey(PenpotWorkspace, on_delete=models.CASCADE, related_name="invites")
    email = models.EmailField()
    role = models.CharField(max_length=10, default="editor")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    token = models.TextField(blank=True)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["email"]
        unique_together = ("workspace", "email")

    def __str__(self):
        return f"{self.email} → {self.workspace.project.key} ({self.role})"

    @property
    def accept_url(self):
        return self.workspace.invite_accept_url(self.token) if self.token else ""
