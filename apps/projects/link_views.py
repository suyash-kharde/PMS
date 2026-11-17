"""The Project Links tab: website links for a project with optional sample logins, so anyone on the
project can open the site and sign in. Seeing the tab needs only access to the project; adding, editing
and deleting need project.edit. A saved password is never in a page — "Show"/"Copy" fetch it on demand
and each reveal is written to the audit log."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.permissions import has_cap, require
from apps.audit.services import log as audit_log

from . import sites
from .forms import ProjectLinkForm
from .models import ProjectLink
from .selectors import visible_projects


def _require_editor(user, project):
    """Adding, editing and deleting links needs project.edit — and is never open to a client-side login,
    which can only view and use the links (the middleware already closes these URLs; this is the second lock)."""
    if user.is_client_login:
        raise PermissionDenied("Customers can view and open project links but not change them.")
    require(user, "project.edit", project)


def _project(user, pk):
    project = visible_projects(user).filter(pk=pk).first()
    if not project:
        raise Http404("Project not found")
    return project


@login_required
def project_links(request, pk):
    project = _project(request.user, pk)
    return render(request, "projects/project_links.html", {
        "project": project, "tab": "links",
        "links": [(link, ProjectLinkForm(instance=link, prefix=f"l{link.pk}")) for link in project.links.all()],
        "form": ProjectLinkForm(prefix="new"),
        "can_edit": has_cap(request.user, "project.edit", project) and not request.user.is_client_login,
        "sites_enabled": sites.enabled(),
    })


@login_required
@require_POST
def project_link_save(request, pk, link_pk=None):
    project = _project(request.user, pk)
    _require_editor(request.user, project)
    link = get_object_or_404(ProjectLink, pk=link_pk, project=project) if link_pk else None
    old_site = link.site_name if link and link.via_gateway else ""
    form = ProjectLinkForm(request.POST, instance=link, prefix=f"l{link.pk}" if link else "new")
    if form.is_valid():
        obj = form.save(commit=False)
        if link is None:
            obj.project, obj.created_by = project, request.user
        obj.save()
        if old_site and old_site != (obj.site_name if obj.via_gateway else ""):
            sites.remove(old_site)  # renamed or switched off
        note = ""
        if obj.via_gateway:
            note = (f" Open it at /sites/{obj.site_name}/." if sites.push(obj)
                    else " It isn't reachable through Planwise yet; Planwise will keep trying.")
            audit_log(request.user, "edit", "projects", target=f"{project.key}: {obj.title} at /sites/{obj.site_name}/")
        messages.success(request, f"{obj.title} {'updated' if link else 'added'}.{note}")
    else:
        messages.error(request, "; ".join(f"{k}: {v[0]}" for k, v in form.errors.items()))
    return redirect("projects:project_links", project.pk)


@login_required
@require_POST
def project_link_delete(request, pk, link_pk):
    project = _project(request.user, pk)
    _require_editor(request.user, project)
    link = get_object_or_404(ProjectLink, pk=link_pk, project=project)
    title, site = link.title, (link.site_name if link.via_gateway else "")
    link.delete()
    if site:
        sites.remove(site)
    messages.success(request, f"{title} removed.")
    return redirect("projects:project_links", project.pk)


@login_required
@require_POST
def project_link_password(request, pk, link_pk):
    """The saved sample password for one link, for the Show / Copy buttons. Anyone who can open the
    project may read it (that is its purpose); each read is logged."""
    project = _project(request.user, pk)
    link = get_object_or_404(ProjectLink, pk=link_pk, project=project)
    audit_log(request.user, "view_secret", "projects", target=f"{project.key}: {link.title} sample password")
    return JsonResponse({"password": link.get_password()})


def site_auth(request):
    """Asked by nginx before every /sites/<name>/ request (auth_request, answer cached ~1 min):
    204 = let through, 401 = sign in first, 403 = no access (or no such site)."""
    name = request.headers.get("X-Site-Name", "")
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    return HttpResponse(status=204 if sites.site_allowed(request.user, name) else 403)
