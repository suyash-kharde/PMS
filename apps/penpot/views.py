from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from apps.accounts.permissions import require
from apps.projects.models import Activity
from apps.projects.selectors import visible_projects

from . import services
from .client import PenpotError
from .models import PenpotWorkspace


def _project(request, pk):
    project = visible_projects(request.user).filter(pk=pk).first()
    if project is None:
        raise Http404("Project not found")
    require(request.user, "penpot.manage", project)
    return project


def _workspace(project):
    return PenpotWorkspace.objects.filter(project=project).first()


@login_required
def manage(request, pk):
    project = _project(request, pk)
    workspace = _workspace(project)

    # Render from stored state only — no Penpot call on a GET. The worker keeps
    # `ready` workspaces' file list fresh (services._refresh_ready); the "Refresh
    # from Penpot" button forces it on demand.
    files = []
    if workspace and workspace.is_ready and workspace.file_id:
        files = [{"id": workspace.file_id, "name": workspace.file_name}]

    member_emails = {i.email for i in workspace.invites.all()} if workspace else set()
    invitable = (
        User.objects.filter(memberships__project=project, is_active=True)
        .exclude(email="")
        .exclude(email__in=member_emails)
        .distinct()
        if workspace and workspace.is_ready
        else User.objects.none()
    )

    return render(
        request,
        "penpot/manage.html",
        {
            "project": project,
            "tab": "design",
            "penpot_enabled": services.enabled(),
            "workspace": workspace,
            "files": files,
            "invites": workspace.invites.all() if workspace else [],
            "invitable": invitable,
            "base_url": settings.PENPOT_BASE_URL,
            "penpot_mcp_url": getattr(settings, "PENPOT_MCP_URL", ""),
            "mcp_endpoint": request.build_absolute_uri("/mcp/").rstrip("/"),
        },
    )


@login_required
@require_POST
def provision_now(request, pk):
    project = _project(request, pk)
    if not services.enabled():
        messages.error(request, "Penpot integration is disabled (set PENPOT_ENABLED=1).")
        return redirect("penpot:manage", pk=project.pk)
    workspace = services.enqueue_for_project(project, actor=request.user)
    workspace = services.provision(workspace)
    if workspace.status == "ready":
        messages.success(request, "Penpot design workspace is ready.")
    else:
        messages.error(request, f"Provisioning failed: {workspace.last_error[:200]}")
    return redirect("penpot:manage", pk=project.pk)


@login_required
@require_POST
def rebuild_now(request, pk):
    project = _project(request, pk)
    workspace = _workspace(project)
    if workspace is None:
        raise Http404("No workspace")
    if not services.enabled():
        messages.error(request, "Penpot integration is disabled (set PENPOT_ENABLED=1).")
        return redirect("penpot:manage", pk=project.pk)
    workspace = services.rebuild(workspace, actor=request.user)
    if workspace.status == "ready":
        messages.success(request, "Penpot workspace rebuilt from scratch.")
    else:
        messages.error(request, f"Rebuild failed: {workspace.last_error[:200]}")
    return redirect("penpot:manage", pk=project.pk)


@login_required
@require_POST
def sync_now(request, pk):
    project = _project(request, pk)
    workspace = _workspace(project)
    if not (workspace and workspace.is_ready):
        raise Http404("No ready workspace")
    try:
        services.sync(workspace)
        messages.success(request, "Refreshed from Penpot.")
    except PenpotError as exc:
        messages.error(request, f"Refresh failed: {exc}")
    return redirect("penpot:manage", pk=project.pk)


@login_required
@require_POST
def share_create(request, pk):
    project = _project(request, pk)
    workspace = _workspace(project)
    if not (workspace and workspace.is_ready):
        raise Http404("No ready workspace")
    try:
        services.create_public_share_link(workspace)
        Activity.log(request.user, "created a public Penpot view link", project=project)
        messages.success(request, "Public view link created.")
    except PenpotError as exc:
        messages.error(request, f"Could not create the link: {exc}")
    return redirect("penpot:manage", pk=project.pk)


@login_required
@require_POST
def share_revoke(request, pk):
    project = _project(request, pk)
    workspace = _workspace(project)
    if workspace:
        try:
            services.revoke_public_share_link(workspace)
            Activity.log(request.user, "revoked the public Penpot view link", project=project)
            messages.success(request, "Public view link revoked.")
        except PenpotError as exc:
            messages.error(request, f"Could not revoke the link: {exc}")
    return redirect("penpot:manage", pk=project.pk)


@login_required
@require_POST
def invite_create(request, pk):
    project = _project(request, pk)
    workspace = _workspace(project)
    if not (workspace and workspace.is_ready):
        raise Http404("No ready workspace")

    role = request.POST.get("role", "editor")
    if role not in ("viewer", "editor", "admin"):
        role = "editor"

    email = (request.POST.get("email") or "").strip().lower()
    user = None
    if request.POST.get("user"):
        user = User.objects.filter(pk=request.POST["user"]).first()
        if user and user.email:
            email = user.email.lower()
    if not email:
        messages.error(request, "Pick a project member or enter an email.")
        return redirect("penpot:manage", pk=project.pk)

    try:
        services.invite_member(workspace, email=email, role=role, user=user, invited_by=request.user)
        messages.success(request, f"Invited {email} to the Penpot team.")
    except PenpotError as exc:
        messages.error(request, f"Invite failed: {exc}")
    return redirect("penpot:manage", pk=project.pk)


@login_required
@require_POST
def disable_workspace(request, pk):
    project = _project(request, pk)
    workspace = _workspace(project)
    if workspace:
        delete_remote = bool(request.POST.get("delete_remote"))
        try:
            services.disable(workspace, delete_remote=delete_remote)
            messages.success(
                request,
                "Penpot workspace torn down." if delete_remote else "Penpot workspace disabled.",
            )
        except PenpotError as exc:
            messages.error(request, f"Could not disable: {exc}")
    return redirect("penpot:manage", pk=project.pk)
