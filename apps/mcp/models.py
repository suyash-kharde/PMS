import hashlib
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

TOKEN_PREFIX = "pwmcp_"


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class ProjectMCPToken(models.Model):
    """A single Claude Code / MCP access link, bound to one project and one user.

    The link Claude Code connects to is `https://<host>/mcp/<raw-token>/`. The raw
    token is shown once at creation and never stored — only its SHA-256 hash and a
    short prefix for display. Every MCP request authenticates by hashing the token
    in the URL (or `Authorization: Bearer`) and matching this row; all data access
    is then hard-scoped to `project` and filtered through the normal capability
    selectors as `created_by`.
    """

    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="mcp_tokens"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="mcp_tokens",
        help_text="Permission checks for this link run as this user.",
    )
    name = models.CharField(max_length=120, default="Claude Code")

    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    token_prefix = models.CharField(max_length=16, editable=False, help_text="For display, e.g. pwmcp_a1b2")

    # opt-in write scopes — a link is read-only unless these are set
    allow_ticket_write = models.BooleanField(
        default=False, help_text="Allow commenting on tickets, changing status, raising bugs"
    )
    allow_sprint_write = models.BooleanField(
        default=False,
        help_text="Allow changing requirement status, recording test results, adding acceptance criteria",
    )

    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.token_prefix}… · {self.project.key} · {self.name}"

    @staticmethod
    def generate():
        """-> (raw_token, token_hash, token_prefix). Store the last two; show the first once."""
        raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
        return raw, hash_token(raw), raw[: len(TOKEN_PREFIX) + 4]

    @property
    def is_active(self):
        if self.revoked_at is not None:
            return False
        if self.expires_at is not None and self.expires_at <= timezone.now():
            return False
        return True

    @property
    def status_label(self):
        if self.revoked_at is not None:
            return "revoked"
        if self.expires_at is not None and self.expires_at <= timezone.now():
            return "expired"
        return "active"

    def revoke(self):
        if self.revoked_at is None:
            self.revoked_at = timezone.now()
            self.save(update_fields=["revoked_at"])

    def touch(self):
        """Bump last_used_at, but at most once a minute (one write per burst of calls)."""
        now = timezone.now()
        if self.last_used_at is None or (now - self.last_used_at).total_seconds() > 60:
            ProjectMCPToken.objects.filter(pk=self.pk).update(last_used_at=now)
            self.last_used_at = now
