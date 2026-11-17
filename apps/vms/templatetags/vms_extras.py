from django import template

from ..forms import ApprovalStepForm, ProjectVMForm, ProjectVMGitForm, ProjectVMJenkinsForm
from ..models import ProjectVM
from ..services import github as gh_service

register = template.Library()


@register.simple_tag
def project_vm_links(project):
    """Every VM assigned to this project, for read-only or edit display."""
    return ProjectVM.objects.filter(project=project).select_related("vm")


@register.simple_tag
def project_vm_add_form(project):
    """A fresh 'assign a VM' form (VM + folder path only), pre-filtered to VMs not already on
    this project."""
    return ProjectVMForm(project=project)


@register.simple_tag
def project_vm_edit_form(link):
    """A form pre-filled with this pairing's folder path, for editing in place."""
    return ProjectVMForm(instance=link)


@register.simple_tag
def project_vm_git_form(link):
    """The Git section's form, pre-filled with this pairing's repo/branch/credentials."""
    return ProjectVMGitForm(instance=link)


@register.simple_tag
def project_vm_jenkins_form(link):
    """The Jenkins section's form, pre-filled with this pairing's Jenkins config."""
    return ProjectVMJenkinsForm(instance=link)


@register.simple_tag
def git_commits(link, limit=3):
    """Latest commits on this pairing's branch — GitHub only, read-only, no clone."""
    if not link.git_repo_url:
        return []
    return gh_service.recent_commits(link.git_repo_url, link.git_branch, limit=limit)


@register.simple_tag
def git_version(link):
    """The latest release tag for this pairing's repo, or None if there isn't one."""
    if not link.git_repo_url:
        return None
    return gh_service.latest_version(link.git_repo_url)


@register.simple_tag
def approval_step_add_form():
    """A fresh 'add an approver' form — a dropdown of real accounts, not free text."""
    return ApprovalStepForm()


@register.simple_tag
def approval_step_edit_form(step):
    """A form pre-filled with this step's current approver, for reassigning in place."""
    return ApprovalStepForm(instance=step)
