from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from apps.accounts.permissions import require
from apps.projects.selectors import visible_projects

from .models import ChatSession
from .services.index import reindex_project


def _get_project(user, pk):
    project = visible_projects(user).filter(pk=pk).first()
    if not project:
        raise Http404("Project not found")
    return project


@login_required
def chat(request, pk):
    project = _get_project(request.user, pk)
    require(request.user, "chat.view", project)
    session, _ = ChatSession.objects.get_or_create(
        project=project, user=request.user, defaults={"title": f"Chat about {project.key}"}
    )
    return render(request, "rag/chat.html", {
        # NOT "messages" -- that key collides with django.contrib.messages' own
        # context variable (the flash-message banners in base.html), which made
        # this render raw ChatMessage.__str__() rows as alert banners
        "project": project, "session": session, "chat_messages": session.messages.all(), "tab": "chat",
    })


@login_required
@require_POST
def reindex(request, pk):
    project = _get_project(request.user, pk)
    require(request.user, "chat.view", project)
    try:
        count = reindex_project(project)
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status=502)
    return JsonResponse({"ok": True, "chunks": count})


@login_required
@require_POST
def clear_chat(request, pk):
    project = _get_project(request.user, pk)
    require(request.user, "chat.view", project)
    ChatSession.objects.filter(project=project, user=request.user).delete()
    return JsonResponse({"ok": True})
