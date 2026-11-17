import markdown as md

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.permissions import has_cap, require
from apps.projects.models import Activity
from apps.projects.selectors import visible_projects

from .forms import DevUpdateForm
from .models import KINDS, STATUS, DevUpdate
from .selectors import milestone_progress, visible_dev_updates

MD_EXTENSIONS = ["extra", "sane_lists", "toc", "nl2br"]


def _render_markdown(text):
    return md.markdown(text or "", extensions=MD_EXTENSIONS)


def _project(user, pk):
    project = visible_projects(user).filter(pk=pk).first()
    if not project:
        raise Http404("Project not found")
    return project


def _get_update(user, pk):
    update = visible_dev_updates(user).filter(pk=pk).first()
    if not update:
        raise Http404("Update not found")
    return update


@login_required
def project_log(request, pk):
    project = _project(request.user, pk)
    require(request.user, "devlog.view", project)

    updates = visible_dev_updates(request.user).filter(project=project)
    milestone = request.GET.get("milestone", "")
    status = request.GET.get("status", "")
    kind = request.GET.get("kind", "")
    if milestone:
        updates = updates.filter(milestone=milestone)
    if status:
        updates = updates.filter(status=status)
    if kind:
        updates = updates.filter(kind=kind)

    progress = milestone_progress(visible_dev_updates(request.user).filter(project=project))

    return render(request, "devlog/log.html", {
        "project": project,
        "tab": "devlog",
        "updates": updates[:200],
        "progress": progress,
        "kind_choices": KINDS,
        "status_choices": STATUS,
        "filters": {"milestone": milestone, "status": status, "kind": kind},
        "can_manage": has_cap(request.user, "devlog.manage", project),
    })


@login_required
def update_detail(request, pk):
    update = _get_update(request.user, pk)
    return render(request, "devlog/update_detail.html", {
        "project": update.project,
        "tab": "devlog",
        "u": update,
        "details_html": _render_markdown(update.details),
        "can_manage": has_cap(request.user, "devlog.manage", update.project),
    })


@login_required
def update_form(request, pk):
    """Create (pk = project id) or edit (pk = DevUpdate id)."""
    editing = "edit" in request.resolver_match.url_name
    if editing:
        update = _get_update(request.user, pk)
        project = update.project
    else:
        update = None
        project = _project(request.user, pk)
    require(request.user, "devlog.manage", project)

    form = DevUpdateForm(request.POST or None, instance=update, project=project)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        if obj.pk is None:
            obj.project = project
            obj.created_by = request.user
            obj.source = "web"
        obj.save()
        verb = "updated" if editing else "logged"
        Activity.log(request.user, f"{verb} a dev update: “{obj.headline}”", project=project)
        messages.success(request, f"Dev update {verb}.")
        return redirect(obj.get_absolute_url())

    return render(request, "devlog/update_form.html", {
        "project": project,
        "tab": "devlog",
        "form": form,
        "u": update,
        "wide_fields": {"details", "files_changed"},
    })


@login_required
@require_POST
def update_delete(request, pk):
    update = _get_update(request.user, pk)
    require(request.user, "devlog.manage", update.project)
    project, headline = update.project, update.headline
    update.delete()
    Activity.log(request.user, f"deleted a dev update: “{headline}”", project=project)
    messages.success(request, "Dev update deleted.")
    return redirect("devlog:project_log", pk=project.pk)
