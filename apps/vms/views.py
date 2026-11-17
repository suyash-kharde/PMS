import json
import os
import tempfile

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.accounts.permissions import has_cap, require
from apps.audit.services import log as audit_log
from apps.projects.models import Project
from apps.projects.selectors import visible_projects

from . import crypto
from .forms import ApprovalStepForm, ProjectVMForm, ProjectVMGitForm, ProjectVMJenkinsForm, VMForm
from .models import ApprovalStep, DeploymentApproval, DeploymentApprovalDecision, ProjectVM, VM
from .services import jenkins as jenkins_service
from .services.ssh_fs import (
    DirectoryExistsError,
    DirectoryListError,
    SSHConnectionError,
    UploadError,
    create_directory,
    list_directory,
    upload_files,
)
from .services.ssh_git import VMGitError, pull_code, push_code

MAX_UPLOAD_SIZE = 480 * 1024 * 1024  # just under nginx's own client_max_body_size (512M)


def _get_project(user, pk):
    project = visible_projects(user).filter(pk=pk).first()
    if not project:
        raise Http404("Project not found")
    return project


@login_required
def project_vms(request, pk):
    """Read-only detail view. Assigning/editing a VM happens on the project's edit page."""
    project = _get_project(request.user, pk)
    require(request.user, "vm.manage")
    return render(
        request,
        "vms/project_vms.html",
        {
            "project": project,
            "can_edit": False,
            "tab": "vms",
        },
    )


# --------------------------------------------------------------------------- VM registry (admin)
SORTS = {"name": ("name", "pk"), "name_desc": ("-name", "-pk"), "projects": ("-n", "name"), "newest": ("-created_at", "-pk")}

@login_required
def vm_list(request):
    require(request.user, "vm.manage")
    total = VM.objects.count()
    vms = VM.objects.all()
    q = (request.GET.get("q") or "").strip()
    if q:
        # name, host, OS, login name, or the key / name of a project on it (never the password or tokens)
        match = VM.objects.filter(
            Q(name__icontains=q) | Q(host__icontains=q) | Q(os__icontains=q) | Q(username__icontains=q)
            | Q(project_links__project__key__icontains=q) | Q(project_links__project__name__icontains=q)
        ).values("pk")
        vms = vms.filter(pk__in=match)
    os_filter = (request.GET.get("os") or "").strip()
    if os_filter:
        vms = vms.filter(os=os_filter)
    project_filter = (request.GET.get("project") or "").strip()
    if project_filter.isdigit():
        vms = vms.filter(pk__in=ProjectVM.objects.filter(project_id=int(project_filter)).values("vm_id"))
    use = request.GET.get("use") or ""
    vms = vms.annotate(n=Count("project_links"))
    if use == "used":
        vms = vms.filter(n__gt=0)
    elif use == "unused":
        vms = vms.filter(n=0)
    sort = request.GET.get("sort") or "name"
    vms = vms.order_by(*SORTS.get(sort, SORTS["name"]))
    rows = [(vm, VMForm(instance=vm, prefix=f"vm{vm.pk}")) for vm in vms]
    filtered = bool(q or os_filter or project_filter or use)
    return render(request, "vms/vm_list.html", {
        "rows": rows, "form": VMForm(), "q": q, "total": total, "filtered": filtered,
        "os_filter": os_filter, "project_filter": project_filter, "use": use, "sort": sort,
        "os_choices": VM.objects.exclude(os="").order_by("os").values_list("os", flat=True).distinct(),
        "project_choices": Project.objects.filter(vm_links__isnull=False).distinct().order_by("key"),
        "sort_choices": [("name", "Name A–Z"), ("name_desc", "Name Z–A"), ("projects", "Most projects"),
                         ("newest", "Newest first")],
    })


@login_required
def vm_projects(request, vm_pk):
    """The list of projects on one VM — click through from here to a project's folder."""
    require(request.user, "vm.manage")
    vm = get_object_or_404(VM, pk=vm_pk)
    links = ProjectVM.objects.filter(vm=vm).select_related("project")
    return render(request, "vms/vm_projects.html", {"vm": vm, "links": links})


@login_required
def vm_project_detail(request, vm_pk, project_pk):
    """The folder a project actually has on a VM, read live over SSH — `ls -la` only."""
    require(request.user, "vm.manage")
    link = get_object_or_404(ProjectVM.objects.select_related("vm", "project"), vm_id=vm_pk, project_id=project_pk)
    listing, error = None, None
    try:
        listing = list_directory(link.vm, link.folder_path)
    except (DirectoryListError, SSHConnectionError) as exc:
        error = str(exc)
    return render(request, "vms/vm_project_detail.html", {"link": link, "listing": listing, "error": error})


@login_required
@require_POST
def vm_save(request, pk=None):
    require(request.user, "vm.manage")
    vm = get_object_or_404(VM, pk=pk) if pk else None
    form = VMForm(request.POST, instance=vm, prefix=f"vm{pk}" if pk else None)
    if form.is_valid():
        obj = form.save(commit=False)
        if not pk:
            obj.created_by = request.user
        obj.save()
        audit_log(request.user, "edit" if pk else "create", "vms", target=obj.name)
        messages.success(request, "VM saved.")
    else:
        messages.error(request, "; ".join(f"{k}: {v[0]}" for k, v in form.errors.items()))
    return redirect("vms:vm_list")


@login_required
@require_POST
def vm_delete(request, pk):
    require(request.user, "vm.manage")
    vm = get_object_or_404(VM, pk=pk)
    name = vm.name
    vm.delete()
    audit_log(request.user, "delete", "vms", target=name)
    messages.success(request, "VM removed from the registry.")
    return redirect("vms:vm_list")


# --------------------------------------------------------------------------- per-project assignment
def _repo_used_elsewhere(project, repo_url, exclude_link=None):
    """Other projects already pointed at this same repo — a likely copy/paste-into-the-wrong-project mistake."""
    if not repo_url:
        return []
    qs = ProjectVM.objects.filter(git_repo_url__iexact=repo_url.strip()).exclude(project=project)
    if exclude_link:
        qs = qs.exclude(pk=exclude_link.pk)
    return list(qs.select_related("project").values_list("project__key", flat=True).distinct())


@login_required
@require_POST
def project_vm_add(request, project_pk):
    """VM section: assigns a VM to this project and creates its folder path on that VM.
    Fails cleanly if the folder is already there — no git or Jenkins wiring happens here.
    If "use_existing" is checked, this instead links to a folder that's already on the VM
    (e.g. a project that existed on that VM before it was added to Planwise) — it only
    verifies the folder is really there, it never creates or touches anything on the VM."""
    project = _get_project(request.user, project_pk)
    require(request.user, "project.edit", project)
    form = ProjectVMForm(request.POST, project=project)
    if form.is_valid():
        vm = form.cleaned_data["vm"]
        folder_path = form.cleaned_data["folder_path"]
        use_existing = form.cleaned_data["use_existing"]
        try:
            if use_existing:
                list_directory(vm, folder_path)
            else:
                create_directory(vm, folder_path)
        except (DirectoryExistsError, DirectoryListError, SSHConnectionError) as exc:
            audit_log(
                request.user, "assign_vm", "vms", target=f"{vm.name} → {project.key}", status="failed", details=str(exc)
            )
            messages.error(request, str(exc))
        else:
            link = form.save(commit=False)
            link.project = project
            link.assigned_by = request.user
            link.save()
            audit_log(request.user, "assign_vm", "vms", target=f"{link.vm.name} → {project.key}")
            if use_existing:
                messages.success(request, f"{link.vm.name} assigned to {project.key} — linked to the existing {folder_path} on the VM.")
            else:
                messages.success(request, f"{link.vm.name} assigned to {project.key} — {folder_path} created on the VM.")
    else:
        messages.error(request, "; ".join(f"{k}: {v[0]}" for k, v in form.errors.items()))
    return redirect("projects:edit", project.pk)


@login_required
@require_POST
def project_vm_folder_preview(request, project_pk):
    """AJAX: shows what's actually at a given path on a VM before the Add VM / Edit folder
    path forms are submitted — lets you confirm you're pointing at the right existing folder
    instead of finding out after the link is saved. Read-only: runs `ls -la`, nothing else."""
    project = _get_project(request.user, project_pk)
    require(request.user, "project.edit", project)
    vm_pk = request.POST.get("vm") or ""
    vm = VM.objects.filter(pk=vm_pk).first() if vm_pk.isdigit() else None
    if not vm:
        return JsonResponse({"ok": False, "error": "Choose a VM first."})
    path = (request.POST.get("path") or "").strip()
    if not path:
        return JsonResponse({"ok": False, "error": "Enter a folder path first."})
    try:
        listing = list_directory(vm, path)
    except (DirectoryListError, SSHConnectionError) as exc:
        return JsonResponse({"ok": False, "error": str(exc)})
    return JsonResponse({"ok": True, "listing": listing})


@login_required
@require_POST
def project_vm_update(request, project_pk, link_pk):
    """VM section: changes the folder path for an existing assignment. Only touches the VM's
    filesystem (creates the new folder) when the path actually changed."""
    project = _get_project(request.user, project_pk)
    require(request.user, "project.edit", project)
    link = get_object_or_404(ProjectVM, pk=link_pk, project=project)
    old_folder = link.folder_path
    form = ProjectVMForm(request.POST, instance=link, project=project)
    if form.is_valid():
        new_folder = form.cleaned_data["folder_path"]
        use_existing = form.cleaned_data["use_existing"]
        if new_folder != old_folder:
            try:
                if use_existing:
                    list_directory(link.vm, new_folder)
                else:
                    create_directory(link.vm, new_folder)
            except (DirectoryExistsError, DirectoryListError, SSHConnectionError) as exc:
                audit_log(
                    request.user, "edit", "vms", target=f"{link.vm.name} @ {project.key}", status="failed", details=str(exc)
                )
                messages.error(request, str(exc))
                return redirect("projects:edit", project.pk)
        form.save()
        audit_log(request.user, "edit", "vms", target=f"{link.vm.name} @ {project.key}")
        messages.success(request, f"{link.vm.name} updated.")
    else:
        messages.error(request, "; ".join(f"{k}: {v[0]}" for k, v in form.errors.items()))
    return redirect("projects:edit", project.pk)


@login_required
@require_POST
def project_vm_upload_code(request, project_pk, link_pk):
    """VM section: uploads one or more files (a single file, a whole folder, or an entire
    codebase — the picker on the form decides which) into this assignment's folder on the VM,
    or a subfolder of it named by `dest_subfolder`. Overwrites anything already there by the
    same name — this is a plain drop-files-in action, not a reconciliation like Pull/Push."""
    project = _get_project(request.user, project_pk)
    require(request.user, "project.edit", project)
    link = get_object_or_404(ProjectVM, pk=link_pk, project=project)
    is_ajax = request.headers.get("X-Requested-With") == "fetch"

    def fail(message):
        if is_ajax:
            return JsonResponse({"ok": False, "error": message}, status=400)
        messages.error(request, message)
        return redirect("projects:edit", project.pk)

    uploaded = request.FILES.getlist("files")
    if not uploaded:
        return fail("Choose at least one file or folder to upload.")

    paths = request.POST.getlist("paths")
    if len(paths) != len(uploaded):
        paths = [f.name for f in uploaded]  # no-JS fallback: flat filenames, structure not preserved

    total_size = sum(f.size for f in uploaded)
    if total_size > MAX_UPLOAD_SIZE:
        return fail(f"That's {total_size // (1024 * 1024)} MB total — max {MAX_UPLOAD_SIZE // (1024 * 1024)} MB per upload.")

    dest_subfolder = request.POST.get("dest_subfolder", "").strip()

    with tempfile.TemporaryDirectory() as tmp_dir:
        files_for_upload = []
        for i, (uploaded_file, relative_path) in enumerate(zip(uploaded, paths)):
            local_path = os.path.join(tmp_dir, str(i))
            with open(local_path, "wb") as fh:
                for chunk in uploaded_file.chunks():
                    fh.write(chunk)
            files_for_upload.append((relative_path, local_path))

        try:
            upload_files(link.vm, link.folder_path, dest_subfolder, files_for_upload)
        except (UploadError, DirectoryListError, SSHConnectionError) as exc:
            audit_log(
                request.user, "upload_code", "vms", target=f"{link.vm.name} @ {project.key}", status="failed", details=str(exc)
            )
            return fail(str(exc))

    audit_log(request.user, "upload_code", "vms", target=f"{link.vm.name} @ {project.key}")
    dest_label = f"{link.folder_path.rstrip('/')}/{dest_subfolder}" if dest_subfolder else link.folder_path
    messages.success(request, f"{len(uploaded)} file(s) uploaded to {dest_label} on {link.vm.name}.")
    if is_ajax:
        return JsonResponse({"ok": True})
    return redirect("projects:edit", project.pk)


@login_required
@require_POST
def project_vm_remove(request, project_pk, link_pk):
    project = _get_project(request.user, project_pk)
    require(request.user, "project.edit", project)
    link = get_object_or_404(ProjectVM, pk=link_pk, project=project)
    vm_name = link.vm.name
    link.delete()
    audit_log(request.user, "unassign_vm", "vms", target=f"{vm_name} → {project.key}")
    messages.success(request, "VM unassigned from this project.")
    return redirect("projects:edit", project.pk)


# --------------------------------------------------------------------------- git section
@login_required
@require_POST
def project_vm_git_save(request, project_pk, link_pk):
    """Git section: saves the repo URL/branch/credentials for this VM assignment. Never touches
    the VM — use 'Pull code' for that."""
    project = _get_project(request.user, project_pk)
    require(request.user, "project.edit", project)
    link = get_object_or_404(ProjectVM, pk=link_pk, project=project)
    form = ProjectVMGitForm(request.POST, instance=link)
    if form.is_valid():
        form.save()
        audit_log(request.user, "edit", "vms", target=f"git details saved for {link.vm.name} @ {project.key}")
        messages.success(request, "Git details saved. Use Pull code to fetch it onto the VM.")
        elsewhere = _repo_used_elsewhere(project, link.git_repo_url, exclude_link=link)
        if elsewhere:
            messages.warning(
                request,
                f"Heads up: {link.git_repo_url} is also assigned to {', '.join(elsewhere)}. "
                "If that's not intentional, double-check which project this repo belongs to.",
            )
    else:
        messages.error(request, "; ".join(f"{k}: {v[0]}" for k, v in form.errors.items()))
    return redirect("projects:edit", project.pk)


@login_required
@require_POST
def project_vm_git_pull(request, project_pk, link_pk):
    """Git section: pulls this pairing's repo into its (already-created) folder on the VM —
    clones it the first time, fetches+merges on every pull after that."""
    project = _get_project(request.user, project_pk)
    require(request.user, "project.edit", project)
    link = get_object_or_404(ProjectVM, pk=link_pk, project=project)
    if not link.git_repo_url:
        messages.error(request, "Add a git repo URL first.")
        return redirect("projects:edit", project.pk)
    try:
        pull_code(
            link.vm,
            link.git_repo_url,
            link.git_branch,
            link.folder_path,
            username=link.git_username,
            token=crypto.decrypt(link.git_token),
        )
    except VMGitError as exc:
        audit_log(request.user, "pull_code", "vms", target=f"{link.vm.name} @ {project.key}", status="failed", details=str(exc))
        messages.error(request, str(exc))
    else:
        audit_log(request.user, "pull_code", "vms", target=f"{link.vm.name} @ {project.key}")
        messages.success(request, f"Code pulled into {link.folder_path} on {link.vm.name}.")
    return redirect("projects:edit", project.pk)


@login_required
@require_POST
def project_vm_git_push(request, project_pk, link_pk):
    """Git section: stages + commits whatever's currently in the VM's folder (with the given
    commit message), reconciles it against the remote branch's history, then pushes. Never
    clones — the folder must already be a checkout of that repo (i.e. pulled at least once),
    except the first push of an existing code folder into an empty repo, which sets git up in
    place (see push_code)."""
    project = _get_project(request.user, project_pk)
    require(request.user, "project.edit", project)
    link = get_object_or_404(ProjectVM, pk=link_pk, project=project)
    if not link.git_repo_url:
        messages.error(request, "Add a git repo URL first.")
        return redirect("projects:edit", project.pk)
    commit_message = request.POST.get("commit_message", "").strip()
    if not commit_message:
        messages.error(request, "Enter a commit message.")
        return redirect("projects:edit", project.pk)
    try:
        first_push = push_code(
            link.vm,
            link.git_repo_url,
            link.git_branch,
            link.folder_path,
            commit_message,
            username=link.git_username,
            token=crypto.decrypt(link.git_token),
        )
    except VMGitError as exc:
        audit_log(request.user, "push_code", "vms", target=f"{link.vm.name} @ {project.key}", status="failed", details=str(exc))
        messages.error(request, str(exc))
    else:
        details = f"{commit_message} (first push — git set up in the folder)" if first_push else commit_message
        audit_log(request.user, "push_code", "vms", target=f"{link.vm.name} @ {project.key}", details=details)
        if first_push:
            messages.success(
                request,
                f"First push done: git was set up in {link.folder_path} on {link.vm.name} and the code went to "
                f"{link.git_branch or 'main'} of the repo. Pull and push work normally from now on.",
            )
        else:
            messages.success(request, f"Code pushed from {link.folder_path} on {link.vm.name}.")
    return redirect("projects:edit", project.pk)


# --------------------------------------------------------------------------- jenkins section
@login_required
@require_POST
def project_vm_jenkins_save(request, project_pk, link_pk):
    """Jenkins section: saves the Jenkins URL/user/API token for this VM assignment — unrelated
    to the VM/git wiring above it, and to the approval-chain config further down the page."""
    project = _get_project(request.user, project_pk)
    require(request.user, "project.edit", project)
    link = get_object_or_404(ProjectVM, pk=link_pk, project=project)
    form = ProjectVMJenkinsForm(request.POST, instance=link)
    if form.is_valid():
        form.save()
        audit_log(request.user, "edit", "vms", target=f"jenkins details saved for {link.vm.name} @ {project.key}")
        messages.success(request, "Jenkins details saved.")
    else:
        messages.error(request, "; ".join(f"{k}: {v[0]}" for k, v in form.errors.items()))
    return redirect("projects:edit", project.pk)


# --------------------------------------------------------------------------- deployment approvals
@login_required
@require_POST
def approval_step_add(request, project_pk):
    project = _get_project(request.user, project_pk)
    require(request.user, "approval.manage")
    form = ApprovalStepForm(request.POST)
    if form.is_valid():
        step = form.save(commit=False)
        step.project = project
        step.order = (ApprovalStep.objects.filter(project=project).count())
        step.save()
        audit_log(request.user, "create", "vms", target=f"approval step '{step.approver.display_name}' on {project.key}")
        messages.success(request, f"Added approval step: {step.approver.display_name}.")
    else:
        messages.error(request, "; ".join(f"{k}: {v[0]}" for k, v in form.errors.items()))
    return redirect("vms:project_approvals", project.pk)


@login_required
@require_POST
def approval_step_update(request, project_pk, step_pk):
    project = _get_project(request.user, project_pk)
    require(request.user, "approval.manage")
    step = get_object_or_404(ApprovalStep, pk=step_pk, project=project)
    form = ApprovalStepForm(request.POST, instance=step)
    if form.is_valid():
        form.save()
        audit_log(
            request.user, "edit", "vms", target=f"approval step on {project.key} reassigned to '{step.approver.display_name}'"
        )
        messages.success(request, "Approval step updated.")
    else:
        messages.error(request, "; ".join(f"{k}: {v[0]}" for k, v in form.errors.items()))
    return redirect("vms:project_approvals", project.pk)


@login_required
@require_POST
def approval_step_delete(request, project_pk, step_pk):
    project = _get_project(request.user, project_pk)
    require(request.user, "approval.manage")
    step = get_object_or_404(ApprovalStep, pk=step_pk, project=project)
    name = step.approver.display_name
    step.delete()
    # close the gap so remaining steps stay in a clean 0..N-1 sequence
    for i, remaining in enumerate(ApprovalStep.objects.filter(project=project).order_by("order", "pk")):
        if remaining.order != i:
            remaining.order = i
            remaining.save(update_fields=["order"])
    audit_log(request.user, "delete", "vms", target=f"approval step '{name}' on {project.key}")
    messages.success(request, f"Removed approval step: {name}.")
    return redirect("vms:project_approvals", project.pk)


@login_required
@require_POST
def approval_step_move(request, project_pk, step_pk, direction):
    project = _get_project(request.user, project_pk)
    require(request.user, "approval.manage")
    steps = list(ApprovalStep.objects.filter(project=project).order_by("order", "pk"))
    idx = next((i for i, s in enumerate(steps) if s.pk == step_pk), None)
    if idx is not None:
        swap_with = idx - 1 if direction == "up" else idx + 1
        if 0 <= swap_with < len(steps):
            steps[idx].order, steps[swap_with].order = steps[swap_with].order, steps[idx].order
            steps[idx].save(update_fields=["order"])
            steps[swap_with].save(update_fields=["order"])
    return redirect("vms:project_approvals", project.pk)


@login_required
@require_POST
def webhook_token_generate(request, project_pk, link_pk):
    """(Re)generates the secret Jenkins uses to authenticate its webhook call. The raw token is
    shown exactly once, in a flash message — only its hash is ever stored."""
    project = _get_project(request.user, project_pk)
    require(request.user, "approval.manage")
    link = get_object_or_404(ProjectVM, pk=link_pk, project=project)
    raw = link.generate_webhook_token()
    link.save(update_fields=["webhook_token_hash", "webhook_token_prefix"])
    audit_log(request.user, "edit", "vms", target=f"webhook token regenerated for {link.vm.name} @ {project.key}")
    webhook_url = request.build_absolute_uri(reverse("vms:jenkins_webhook", args=[raw]))
    messages.success(
        request,
        f"New webhook URL (copy it now — it won't be shown again): {webhook_url}",
    )
    return redirect("vms:project_approvals", project.pk)


@login_required
def project_approvals(request, pk):
    """Standalone approvals page for a project — reachable without full project-edit access.
    Anyone who can see the project gets it normally; someone named as an approver but not
    otherwise a project member/viewer can still reach this one page, same reasoning as
    approval_decide: being assigned a step is its own narrow grant."""
    project = get_object_or_404(Project, pk=pk)
    can_view = visible_projects(request.user).filter(pk=project.pk).exists()
    is_approver = ApprovalStep.objects.filter(project=project, approver=request.user).exists()
    if not (can_view or is_approver):
        raise Http404("Project not found")
    return render(request, "vms/project_approvals_page.html", {"project": project})


@csrf_exempt
@require_POST
def jenkins_webhook(request, token):
    """Called by the Jenkinsfile (not a logged-in user) right before it pauses on `input()`.
    Creates the pending approval chain for this project from its currently configured steps."""
    link = ProjectVM.find_by_webhook_token(token)
    if not link:
        return JsonResponse({"error": "invalid or unknown webhook token"}, status=404)

    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "body must be JSON"}, status=400)

    step_qs = ApprovalStep.objects.filter(project=link.project).select_related("approver").order_by("order", "pk")
    steps = [{"user_id": s.approver_id, "name": s.approver.display_name} for s in step_qs]
    if not steps:
        return JsonResponse({"error": "no approval steps configured for this project"}, status=400)

    approval = DeploymentApproval.objects.create(
        project=link.project,
        project_vm=link,
        jenkins_job=payload.get("job", ""),
        jenkins_build_number=str(payload.get("build_number", "")),
        jenkins_build_url=payload.get("build_url", ""),
        jenkins_input_id=payload.get("input_id", ""),
        steps=steps,
    )
    audit_log(None, "create", "vms", target=f"deployment approval requested for {link.project.key}")
    return JsonResponse({"ok": True, "approval_id": approval.pk, "waiting_on": approval.current_step_name})


def _after_decision_redirect(request, project):
    """Being a named approver is its own narrow grant — it doesn't require otherwise being able
    to see the rest of the project. project_approvals() grants access on that same basis, so
    sending them back there always works, whether or not they can see anything else here."""
    return redirect("vms:project_approvals", project.pk)


@login_required
@require_POST
def approval_decide(request, project_pk, approval_pk):
    # Deliberately not _get_project()/visible_projects() here: an assigned approver may not be
    # a project member at all, and being named on this specific pending step is by itself
    # sufficient authorization for this one action — it isn't general project access.
    project = get_object_or_404(Project, pk=project_pk)
    approval = get_object_or_404(DeploymentApproval, pk=approval_pk, project=project)
    # only the person assigned to the current step can act on it — an admin can also step in,
    # e.g. if that person is unavailable.
    if approval.current_step_user_id != request.user.pk and not has_cap(request.user, "approval.manage"):
        messages.error(request, "This approval step isn't assigned to you.")
        return _after_decision_redirect(request, project)
    decision = request.POST.get("decision")
    if approval.status != "pending" or decision not in ("approve", "reject"):
        messages.error(request, "This request has already been decided, or the action wasn't recognized.")
        return _after_decision_redirect(request, project)

    step_name = approval.current_step_name
    DeploymentApprovalDecision.objects.create(
        approval=approval,
        step_index=approval.current_step,
        step_name=step_name,
        decision="approved" if decision == "approve" else "rejected",
        decided_by=request.user,
    )

    if decision == "reject":
        approval.status = "rejected"
        approval.decided_at = timezone.now()
        approval.save(update_fields=["status", "decided_at"])
        audit_log(request.user, "edit", "vms", target=f"{step_name} rejected deployment on {project.key}")
        messages.success(request, f"Rejected — {step_name}'s decision was recorded.")
        if approval.project_vm and approval.project_vm.jenkins_url:
            try:
                jenkins_service.abort_build(approval.project_vm, approval.jenkins_build_url, approval.jenkins_input_id)
            except jenkins_service.JenkinsCallError as exc:
                approval.jenkins_notify_error = str(exc)
                approval.save(update_fields=["jenkins_notify_error"])
                messages.error(request, f"Recorded here, but couldn't stop the Jenkins build: {exc}")
        return _after_decision_redirect(request, project)

    # approved this step — advance, or finish the chain
    approval.current_step += 1
    if approval.current_step >= len(approval.steps):
        approval.status = "approved"
        approval.decided_at = timezone.now()
        approval.save(update_fields=["current_step", "status", "decided_at"])
        audit_log(request.user, "edit", "vms", target=f"deployment fully approved on {project.key}")
        messages.success(request, f"{step_name} approved — that was the last step, resuming the Jenkins build.")
        if approval.project_vm and approval.project_vm.jenkins_url:
            try:
                jenkins_service.proceed_input(approval.project_vm, approval.jenkins_build_url, approval.jenkins_input_id)
            except jenkins_service.JenkinsCallError as exc:
                approval.jenkins_notify_error = str(exc)
                approval.save(update_fields=["jenkins_notify_error"])
                messages.error(request, f"Recorded here, but couldn't resume the Jenkins build: {exc}")
    else:
        approval.save(update_fields=["current_step"])
        audit_log(request.user, "edit", "vms", target=f"{step_name} approved, next: {approval.current_step_name}")
        messages.success(request, f"{step_name} approved — now waiting on {approval.current_step_name}.")
    return _after_decision_redirect(request, project)
