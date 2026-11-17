"""The Documents tab: files uploaded against a project. Anyone who can open the project can see,
upload and download; deleting is for the uploader, or anyone who can edit the project. Files are never
served straight from /media/ (see core.views.media_serve) — every download is checked here."""
import mimetypes
import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.permissions import has_cap
from apps.audit.services import log as audit_log

from .models import ProjectFile
from .selectors import visible_projects

MAX_BYTES = 25 * 1024 * 1024  # per file
MAX_FILES = 10  # per upload
ALLOWED_EXT = {
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "odp", "txt", "csv", "md", "rtf",
    "png", "jpg", "jpeg", "gif", "webp", "zip",
}


def _project(user, pk):
    project = visible_projects(user).filter(pk=pk).first()
    if not project:
        raise Http404("Project not found")
    return project


def _can_delete(user, project, f):
    return f.uploaded_by_id == user.pk or has_cap(user, "project.edit", project)


@login_required
def project_files(request, pk):
    project = _project(request.user, pk)
    files = list(project.files.select_related("uploaded_by"))
    for f in files:
        f.can_delete = _can_delete(request.user, project, f)
    return render(request, "projects/project_files.html", {
        "project": project, "tab": "files", "files": files,
        "max_mb": MAX_BYTES // (1024 * 1024), "max_files": MAX_FILES,
        "allowed": ", ".join(sorted(ALLOWED_EXT)),
        "accept": ",".join("." + e for e in sorted(ALLOWED_EXT)),
    })


@login_required
@require_POST
def project_file_upload(request, pk):
    project = _project(request.user, pk)
    uploads = request.FILES.getlist("files")
    if not uploads:
        messages.error(request, "Choose at least one file to upload.")
        return redirect("projects:project_files", pk)
    note = request.POST.get("note", "").strip()[:300]
    saved, skipped = [], []
    for upload in uploads[:MAX_FILES]:
        name = os.path.basename(upload.name.replace("\\", "/"))[:255]
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in ALLOWED_EXT or upload.size > MAX_BYTES or not name:
            skipped.append(name or "(no name)")
            continue
        saved.append(ProjectFile.objects.create(
            project=project, file=upload, original_name=name, size=upload.size, note=note,
            uploaded_by=request.user))
    skipped += [u.name for u in uploads[MAX_FILES:]]
    if saved:
        audit_log(request.user, "create", "projects",
                  target=f"{project.key}: uploaded " + ", ".join(f.original_name for f in saved))
        messages.success(request, f"Uploaded {len(saved)} file{'s' if len(saved) != 1 else ''}.")
    if skipped:
        messages.warning(
            request,
            f"Skipped {len(skipped)} file(s): only common documents and images are accepted, up to "
            f"{MAX_BYTES // (1024 * 1024)} MB each and {MAX_FILES} files at a time — {', '.join(skipped)}")
    return redirect("projects:project_files", pk)


@login_required
def project_file_download(request, pk, file_pk):
    project = _project(request.user, pk)
    f = get_object_or_404(ProjectFile, pk=file_pk, project=project)
    try:
        handle = f.file.open("rb")
    except FileNotFoundError:
        raise Http404("File is missing on the server")
    inline = request.GET.get("view") == "1" and f.can_preview
    content_type = mimetypes.guess_type(f.original_name)[0] or "application/octet-stream"
    response = FileResponse(handle, as_attachment=not inline, filename=f.original_name, content_type=content_type)
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
@require_POST
def project_file_delete(request, pk, file_pk):
    project = _project(request.user, pk)
    f = get_object_or_404(ProjectFile, pk=file_pk, project=project)
    if not _can_delete(request.user, project, f):
        raise Http404("File not found")
    name = f.original_name
    f.file.delete(save=False)
    f.delete()
    audit_log(request.user, "delete", "projects", target=f"{project.key}: removed {name}")
    messages.success(request, f"Removed {name}.")
    return redirect("projects:project_files", pk)
