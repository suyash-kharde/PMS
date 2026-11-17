import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.accounts.permissions import require
from apps.projects.models import Activity
from apps.projects.selectors import visible_projects

from . import protocol
from .auth import AuthError, authenticate
from .models import ProjectMCPToken
from .tools import Ctx

# JSON-RPC: reserved server error range
AUTH_RPC_CODE = -32001


# =========================================================================== MCP endpoint
@csrf_exempt
def endpoint(request, token):
    """The per-project MCP Streamable-HTTP endpoint. One JSON-RPC message (or batch) per POST."""
    if request.method == "OPTIONS":
        resp = HttpResponse(status=204)
        resp["Allow"] = "POST, OPTIONS"
        return resp
    if request.method != "POST":
        resp = HttpResponse(status=405)
        resp["Allow"] = "POST, OPTIONS"
        return resp

    try:
        mcp_token = authenticate(request, token)
    except AuthError as exc:
        resp = JsonResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": AUTH_RPC_CODE, "message": exc.message}},
            status=exc.status,
        )
        if exc.status == 401:
            resp["WWW-Authenticate"] = "Bearer"
        return resp

    try:
        payload = json.loads(request.body or b"")
    except ValueError:
        return JsonResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": protocol.PARSE_ERROR, "message": "Invalid JSON."}},
            status=400,
        )

    ctx = Ctx(mcp_token.project, mcp_token.created_by, mcp_token)

    if isinstance(payload, list):
        responses = [r for r in (protocol.handle_message(m, ctx) for m in payload) if r is not None]
        if not responses:
            return HttpResponse(status=202)
        return JsonResponse(responses, safe=False)

    response = protocol.handle_message(payload, ctx)
    if response is None:
        return HttpResponse(status=202)
    return JsonResponse(response)


# =========================================================================== management UI
def _project(request, pk):
    project = visible_projects(request.user).filter(pk=pk).first()
    if project is None:
        raise Http404("Project not found")
    require(request.user, "mcp.manage", project)
    return project


@login_required
def manage(request, pk):
    project = _project(request, pk)
    endpoint_root = request.build_absolute_uri("/mcp/").rstrip("/")
    return render(
        request,
        "mcp/manage.html",
        {
            "project": project,
            "tab": "mcp",
            "tokens": project.mcp_tokens.select_related("created_by"),
            "new_link": request.session.pop("mcp_new_link", None),
            "endpoint_root": endpoint_root,
        },
    )


@login_required
@require_POST
def token_create(request, pk):
    project = _project(request, pk)
    raw, token_hash, prefix = ProjectMCPToken.generate()
    token = ProjectMCPToken.objects.create(
        project=project,
        created_by=request.user,
        name=(request.POST.get("name") or "Claude Code").strip()[:120],
        token_hash=token_hash,
        token_prefix=prefix,
        allow_ticket_write=bool(request.POST.get("allow_ticket_write")),
        allow_sprint_write=bool(request.POST.get("allow_sprint_write")),
    )
    request.session["mcp_new_link"] = request.build_absolute_uri(reverse("mcp:endpoint", args=[raw]))
    Activity.log(request.user, f"created a Claude Code / MCP link “{token.name}”", project=project)
    messages.success(request, "MCP link created. Copy it now — it is shown only once.")
    return redirect("mcp:manage", pk=project.pk)


@login_required
@require_POST
def token_revoke(request, pk, token_pk):
    project = _project(request, pk)
    token = get_object_or_404(project.mcp_tokens, pk=token_pk)
    token.revoke()
    Activity.log(request.user, f"revoked the Claude Code / MCP link “{token.name}”", project=project)
    messages.success(request, f"Revoked “{token.name}”.")
    return redirect("mcp:manage", pk=project.pk)
