"""Provisioning + lifecycle for per-project Penpot workspaces.

`enqueue_for_project` is called when a Planwise project is created; the background
worker then calls `drain_workspaces` → `provision` for each queued row. Everything
is idempotent and keyed on `PenpotWorkspace.status` / the ids already stored, so a
failed run resumes rather than duplicating Penpot objects.
"""

from __future__ import annotations

import secrets
import traceback
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django.utils.text import slugify

from apps.projects.models import Activity

from .client import PenpotAuthError, PenpotClient, PenpotError
from .models import PenpotInvite, PenpotWorkspace, generate_bot_password

_SESSION_TTL = 45 * 60  # refresh the cached Penpot session well inside its lifetime

# Everything we learn about the remote Penpot side. Wiped by `_reset_remote_fields`
# so a re-provision rebuilds from scratch rather than trying (and failing) to reuse
# ids that no longer exist on the Penpot instance.
_REMOTE_FIELDS = (
    "profile_id", "team_id", "team_name",
    "penpot_project_id", "penpot_project_name",
    "file_id", "file_name",
    "access_token", "access_token_id",
    "share_link_id", "share_url",
)


# --------------------------------------------------------------------------- config
def enabled() -> bool:
    return bool(getattr(settings, "PENPOT_ENABLED", False) and settings.PENPOT_BASE_URL)


def _base_url() -> str:
    return settings.PENPOT_BASE_URL.rstrip("/")


def bot_email_for(project, *, unique=False) -> str:
    domain = getattr(settings, "PENPOT_BOT_EMAIL_DOMAIN", "planwise.local")
    key = slugify(project.key) or str(project.pk)
    if unique:
        key = f"{key}-{secrets.token_hex(3)}"
    return f"penpot+{key}@{domain}"


def _reset_remote_fields(workspace: PenpotWorkspace, *, keep_password=True) -> None:
    """Forget the Penpot side so the next provision rebuilds it. Drops the cached
    session but keeps the bot email + password by default: the remote account
    usually still exists, and that password is the only way back into it. If the
    account really is gone, `_ensure_bot` registers it again (or takes a fresh
    address); pass keep_password=False only to force a brand-new account."""
    cache.delete(f"penpot:sess:{workspace.pk}")
    for field in _REMOTE_FIELDS:
        setattr(workspace, field, "")
    if not keep_password:
        workspace.bot_password = ""
    workspace.invites.all().delete()


# --------------------------------------------------------------------------- client
def _raw_client() -> PenpotClient:
    return PenpotClient(
        _base_url(),
        timeout=getattr(settings, "PENPOT_TIMEOUT", 30.0),
        verify=getattr(settings, "PENPOT_VERIFY_SSL", True),
    )


def client_for(workspace: PenpotWorkspace) -> PenpotClient:
    """A client logged in as the workspace's bot, reusing a cached session cookie."""
    client = _raw_client()
    key = f"penpot:sess:{workspace.pk}"
    cached = cache.get(key)
    if cached:
        client.set_session(cached)
        return client
    if not (workspace.bot_email and workspace.bot_password):
        raise PenpotError("login", 0, "workspace has no bot credentials yet")
    client.login(workspace.bot_email, workspace.bot_password)
    cache.set(key, client.auth_token, _SESSION_TTL)
    return client


def _with_session(workspace, fn):
    """Run `fn(client)`, transparently re-logging in once on an auth failure."""
    client = client_for(workspace)
    try:
        return fn(client)
    except PenpotAuthError:
        cache.delete(f"penpot:sess:{workspace.pk}")
        client = client_for(workspace)
        return fn(client)


# --------------------------------------------------------------------------- enqueue
def enqueue_for_project(project, *, actor=None) -> PenpotWorkspace | None:
    """Create the queued workspace row for a new project. No network here."""
    if not enabled():
        return None
    workspace, created = PenpotWorkspace.objects.get_or_create(
        project=project,
        defaults={"status": "queued", "base_url": _base_url()},
    )
    if created:
        Activity.log(actor, "queued a Penpot design workspace", project=project)
    elif workspace.status in ("failed", "disabled"):
        workspace.mark("queued", phase="Re-queued")
    return workspace


def rebuild(workspace: PenpotWorkspace, *, actor=None, stdout=None) -> PenpotWorkspace:
    """Force a clean re-provision: forget the remote Penpot side entirely, then run.

    The escape hatch for a workspace whose Penpot objects were deleted or whose
    instance was swapped — `provision` alone will also self-heal, but this makes it
    explicit and resets the attempt counter."""
    _reset_remote_fields(workspace)
    workspace.status = "queued"
    workspace.phase = "Rebuild requested"
    workspace.last_error = ""
    workspace.attempts = 0
    workspace.save()
    Activity.log(actor, "requested a Penpot workspace rebuild", project=workspace.project)
    return provision(workspace, stdout=stdout)


# --------------------------------------------------------------------------- provision
def _ensure_bot(workspace: PenpotWorkspace, client: PenpotClient, *, log=lambda _m: None) -> None:
    """Register (or recover) the per-project bot. On return `client` is logged in as
    the bot and workspace.{profile_id,team_id,bot_email,bot_password} are set."""
    project = workspace.project
    workspace.bot_email = workspace.bot_email or bot_email_for(project)
    workspace.bot_fullname = workspace.bot_fullname or f"{project.key} Design Bot"
    workspace.bot_password = workspace.bot_password or generate_bot_password()

    def _register(email, password):
        token = client.prepare_register(workspace.bot_fullname, email, password)
        return client.register(token, fullname=workspace.bot_fullname)

    try:
        profile = _register(workspace.bot_email, workspace.bot_password)
    except PenpotError:
        # the address is already registered from an earlier run — try the stored
        # password; if we can't get in for any reason (wrong-credentials, account
        # disabled, …) the address is unrecoverable, so take a fresh one rather
        # than wedging on it forever.
        try:
            profile = client.login(workspace.bot_email, workspace.bot_password)
        except PenpotError as exc:
            log(f"previous bot address is unrecoverable ({exc}) — registering a new one")
            workspace.bot_email = bot_email_for(project, unique=True)
            workspace.bot_password = generate_bot_password()
            profile = _register(workspace.bot_email, workspace.bot_password)

    workspace.profile_id = profile.get("id", "")
    workspace.team_id = profile.get("defaultTeamId", "")
    if not client.authenticated:
        client.login(workspace.bot_email, workspace.bot_password)
    cache.set(f"penpot:sess:{workspace.pk}", client.auth_token, _SESSION_TTL)
    workspace.save()


def provision(workspace: PenpotWorkspace, *, stdout=None) -> PenpotWorkspace:
    project = workspace.project
    workspace.attempts += 1
    workspace.base_url = workspace.base_url or _base_url()
    workspace.mark("provisioning", phase="Starting", save=False)
    workspace.save()

    def log(msg):
        workspace.phase = msg
        workspace.save(update_fields=["phase", "updated_at"])
        if stdout:
            stdout.write(f"[penpot {project.key}] {msg}")

    try:
        client = _raw_client()

        # 1) the bot account ----------------------------------------------------
        # If we think we already have one, prove it still works. A Penpot reset or
        # instance swap leaves profile_id pointing at nothing; rather than fail
        # forever, forget the remote side and rebuild it below.
        if workspace.profile_id:
            try:
                client = client_for(workspace)
                client.get_profile()
                cache.set(f"penpot:sess:{workspace.pk}", client.auth_token, _SESSION_TTL)
            except PenpotError as exc:
                log(f"stored Penpot bot is unusable ({exc}); rebuilding the workspace")
                _reset_remote_fields(workspace)
                workspace.save()
                client = _raw_client()

        if not workspace.profile_id:
            log("Creating the Penpot account")
            _ensure_bot(workspace, client, log=log)

        if not client.authenticated:
            client = client_for(workspace)
        cache.set(f"penpot:sess:{workspace.pk}", client.auth_token, _SESSION_TTL)

        if not workspace.team_id:
            teams = client.get_teams()
            default = next((t for t in teams if t.get("isDefault")), None) or (teams[0] if teams else None)
            if default:
                workspace.team_id = default["id"]
                workspace.save(update_fields=["team_id", "updated_at"])

        # Steps 2 and 3 below are best-effort — the access token is only for the
        # team's own external tooling and the renames are cosmetic, so a failure
        # there is logged but does not fail provisioning. Steps 4 and 5 (the project
        # and the starter file) are load-bearing and raise straight to `failed`.

        # 2) an access token for the team's own tooling --------------------
        if not workspace.access_token_id:
            log("Minting an access token")
            try:
                tok = client.create_access_token(name=f"planwise-{slugify(project.key)}")
                workspace.access_token = tok.get("token", "")
                workspace.access_token_id = tok.get("id", "")
                workspace.save(update_fields=["access_token", "access_token_id", "updated_at"])
            except PenpotError as exc:  # non-fatal — session auth still works
                workspace.phase = f"access token skipped: {exc}"
                workspace.save(update_fields=["phase", "updated_at"])
                if stdout:
                    stdout.write(f"[penpot {project.key}] access token skipped: {exc}")

        # 3) name the team & project after the Planwise project ------------
        team_name = f"{project.key} · {project.name}"[:250]
        proj_name = f"{project.key} — UI"[:250]
        if workspace.team_id and workspace.team_name != team_name:
            log("Naming the design team")
            try:
                client.update_team(workspace.team_id, team_name)
                workspace.team_name = team_name
            except PenpotError:
                pass

        if not workspace.penpot_project_id:
            log("Creating the design project")
            proj = client.create_project(workspace.team_id, proj_name)
            workspace.penpot_project_id = proj.get("id", "")
            workspace.penpot_project_name = proj.get("name", proj_name)
            if not workspace.penpot_project_id:
                raise PenpotError("create-project", 0, f"no project id in response: {proj}")
        elif workspace.penpot_project_name != proj_name:
            try:
                client.rename_project(workspace.penpot_project_id, proj_name)
                workspace.penpot_project_name = proj_name
            except PenpotError:
                pass
        workspace.save()

        # 4) a starter UI file --------------------------------------------
        if not workspace.file_id:
            log("Creating the starter file")
            f = client.create_file(workspace.penpot_project_id, f"{project.key} — UI")
            workspace.file_id = f.get("id", "")
            workspace.file_name = f.get("name", f"{project.key} — UI")
            if not workspace.file_id:
                raise PenpotError("create-file", 0, f"no file id in response: {f}")
            workspace.save()

        # 5) default invites --------------------------------------------------
        default_invites = getattr(settings, "PENPOT_DEFAULT_INVITES", [])
        if default_invites:
            log("Sending team invitations")
            _send_invites(workspace, client, default_invites, role="editor")

        workspace.status = "ready"
        workspace.phase = "Ready"
        workspace.last_error = ""
        workspace.provisioned_at = timezone.now()
        workspace.synced_at = timezone.now()
        workspace.save()
        Activity.log(None, "Penpot design workspace is ready", project=project)
        if stdout:
            stdout.write(f"[penpot {project.key}] ready → {workspace.workspace_url}")
    except Exception as exc:  # noqa: BLE001 - surface on the row, never kill the worker
        workspace.mark("failed", phase="Failed", error="".join(traceback.format_exception(exc))[-3000:])
        Activity.log(None, f"Penpot workspace provisioning failed: {exc}", project=project)
        if stdout:
            stdout.write(f"[penpot {project.key}] FAILED: {exc}")
    return workspace


_REFRESH_EVERY = timedelta(minutes=10)


def drain_workspaces(stdout=None):
    if not enabled():
        return
    for workspace in PenpotWorkspace.objects.filter(status="queued").order_by("created_at"):
        provision(workspace, stdout=stdout)
    _refresh_ready(stdout=stdout)


def _refresh_ready(stdout=None):
    """Keep `ready` workspaces' stored file list current so the Design tab can render
    from the database without a per-request Penpot call (see views.manage)."""
    cutoff = timezone.now() - _REFRESH_EVERY
    stale = PenpotWorkspace.objects.filter(status="ready", synced_at__lt=cutoff)
    for workspace in stale:
        try:
            sync(workspace)
        except PenpotError as exc:  # Penpot down / transient — try again next pass
            if stdout:
                stdout.write(f"[penpot {workspace.project.key}] refresh skipped: {exc}")


# --------------------------------------------------------------------------- sync / share
def sync(workspace: PenpotWorkspace) -> list:
    """Refresh the file list (and team name) from Penpot. Returns the files."""
    if not workspace.is_ready:
        return []

    def _do(client):
        files = client.get_project_files(workspace.penpot_project_id)
        if workspace.file_id and not any(f.get("id") == workspace.file_id for f in files):
            # starter file was deleted in Penpot — adopt the first remaining one
            workspace.file_id = files[0]["id"] if files else ""
            workspace.file_name = files[0]["name"] if files else ""
        workspace.synced_at = timezone.now()
        workspace.save(update_fields=["file_id", "file_name", "synced_at", "updated_at"])
        return files

    return _with_session(workspace, _do)


def create_public_share_link(workspace: PenpotWorkspace) -> str:
    """A no-login Penpot view link for the starter file, for stakeholders."""
    if not workspace.is_ready:
        raise PenpotError("share", 0, "workspace is not ready")

    def _do(client):
        file_data = client.get_file(workspace.file_id)
        pages = (file_data.get("data") or {}).get("pages") or []
        if not pages:
            idx = (file_data.get("data") or {}).get("pagesIndex") or {}
            pages = list(idx.keys())
        if not pages:
            raise PenpotError("create-share-link", 0, "could not read the file's pages")
        link = client.create_share_link(workspace.file_id, pages, who="all")
        share_id = link.get("id", "")
        workspace.share_link_id = share_id
        workspace.share_url = (
            f"{workspace.base}/#/view/{workspace.file_id}"
            f"?share-id={share_id}&page-id={pages[0]}&index=0"
        )
        workspace.save(update_fields=["share_link_id", "share_url", "updated_at"])
        return workspace.share_url

    return _with_session(workspace, _do)


def revoke_public_share_link(workspace: PenpotWorkspace) -> None:
    if not workspace.share_link_id:
        return
    old = workspace.share_link_id

    def _do(client):
        try:
            client.delete_share_link(old)
        except PenpotError:
            pass

    _with_session(workspace, _do)
    workspace.share_link_id = ""
    workspace.share_url = ""
    workspace.save(update_fields=["share_link_id", "share_url", "updated_at"])


# --------------------------------------------------------------------------- invites
def invite_member(workspace, *, email, role="editor", user=None, invited_by=None) -> PenpotInvite:
    if not workspace.is_ready:
        raise PenpotError("invite", 0, "workspace is not ready")
    email = email.strip().lower()

    def _do(client):
        return _send_invites(workspace, client, [email], role=role, users={email: user})

    invites = _with_session(workspace, _do)
    if invited_by:
        PenpotInvite.objects.filter(workspace=workspace, email=email).update(invited_by=invited_by)
    Activity.log(invited_by, f"invited {email} to the Penpot design team", project=workspace.project)
    return invites[0]


def _send_invites(workspace, client, emails, *, role="editor", users=None) -> list[PenpotInvite]:
    users = users or {}
    emails = [e.strip().lower() for e in emails if e and e.strip()]
    if not emails:
        return []
    result = client.create_team_invitations(workspace.team_id, emails, role=role)
    tokens = result.get("invitations") or []
    rows = []
    for i, email in enumerate(emails):
        token = tokens[i] if i < len(tokens) else ""
        row, _ = PenpotInvite.objects.update_or_create(
            workspace=workspace,
            email=email,
            defaults={"role": role, "token": token, "user": users.get(email)},
        )
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- lifecycle
def rename_to_match(workspace: PenpotWorkspace) -> None:
    """Push the Planwise project's current name/key onto the Penpot team & project."""
    if not workspace.is_ready:
        return
    project = workspace.project
    team_name = f"{project.key} · {project.name}"[:250]
    proj_name = f"{project.key} — UI"[:250]

    def _do(client):
        if workspace.team_id:
            client.update_team(workspace.team_id, team_name)
        if workspace.penpot_project_id:
            client.rename_project(workspace.penpot_project_id, proj_name)

    try:
        _with_session(workspace, _do)
        workspace.team_name = team_name
        workspace.penpot_project_name = proj_name
        workspace.save(update_fields=["team_name", "penpot_project_name", "updated_at"])
    except PenpotError:
        pass


def disable(workspace: PenpotWorkspace, *, delete_remote=False) -> None:
    """Stop using the workspace. Optionally tear down the Penpot side."""
    if delete_remote and workspace.profile_id:
        def _do(client):
            if workspace.access_token_id:
                try:
                    client.delete_access_token(workspace.access_token_id)
                except PenpotError:
                    pass
            client.delete_profile()  # cascades team, projects and files

        try:
            _with_session(workspace, _do)
        except PenpotError:
            pass
    cache.delete(f"penpot:sess:{workspace.pk}")
    workspace.mark("disabled", phase="Disabled")
