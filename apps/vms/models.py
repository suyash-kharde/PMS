import hashlib
import secrets

from django.conf import settings
from django.db import models


def _hash_token(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


class VM(models.Model):
    """A machine DevOps already provisioned elsewhere. Planwise only tracks how to reach it."""

    name = models.CharField(max_length=120)
    host = models.CharField(max_length=255, help_text="IP address or hostname")
    os = models.CharField(max_length=80, blank=True)
    username = models.CharField(max_length=120, blank=True)
    password = models.CharField(max_length=255, blank=True, help_text="Optional")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="vms_registered"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.host})"


class ProjectVM(models.Model):
    """One VM assigned to one project. The repo/branch/folder here belong to this pairing,
    not to the VM itself — the same VM can host several projects, each with its own repo."""

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="vm_links")
    vm = models.ForeignKey(VM, on_delete=models.CASCADE, related_name="project_links")

    # Not URLField: a valid git remote also includes the SSH/SCP form (git@host:org/repo.git),
    # which has no scheme and fails both browser type=url validation and Django's URLValidator.
    git_repo_url = models.CharField(max_length=500, blank=True)
    git_branch = models.CharField(max_length=120, blank=True, default="main")
    git_username = models.CharField(max_length=120, blank=True, help_text="Optional, for private repos")
    git_token = models.CharField(
        max_length=500, blank=True, help_text="Stored encrypted; used only to authenticate pulls for private repos."
    )
    folder_path = models.CharField(
        max_length=255, blank=True, help_text="Where this project's code lives on the VM, e.g. /home/project-x"
    )

    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    assigned_at = models.DateTimeField(auto_now_add=True)

    # Deployment approvals (Jenkins integration) — optional, only used when this project has a
    # pipeline whose production-deploy stage should be approved from Planwise instead of the
    # Jenkins UI. None of this affects the VM/git assignment above if left blank.
    jenkins_url = models.CharField(
        max_length=500, blank=True, help_text="Base Jenkins URL, e.g. https://jenkins.example.com"
    )
    jenkins_user = models.CharField(max_length=120, blank=True)
    jenkins_api_token = models.CharField(max_length=255, blank=True)
    webhook_token_hash = models.CharField(max_length=64, blank=True, editable=False)
    webhook_token_prefix = models.CharField(max_length=16, blank=True, editable=False)

    class Meta:
        unique_together = ("project", "vm")
        ordering = ["-assigned_at"]

    def __str__(self):
        return f"{self.project.key} · {self.vm.name}"

    def generate_webhook_token(self):
        """-> the raw token (shown once, put in the Jenkinsfile's curl call). Only its hash is stored."""
        raw = f"pwhook_{secrets.token_urlsafe(24)}"
        self.webhook_token_hash = _hash_token(raw)
        self.webhook_token_prefix = raw[:14]
        return raw

    @staticmethod
    def find_by_webhook_token(raw_token):
        if not raw_token:
            return None
        return ProjectVM.objects.filter(webhook_token_hash=_hash_token(raw_token)).select_related("project").first()


class ApprovalStep(models.Model):
    """One rung in a project's deployment-approval ladder — order decides who's asked first.
    Tied to a real Planwise account: only that person (or an admin, as a fallback) can act on
    this step. Editing this list only changes future approval requests; one already in flight
    keeps the approver snapshot it started with (see DeploymentApproval.steps)."""

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="approval_steps")
    order = models.PositiveIntegerField(default=0)
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="approval_steps"
    )

    class Meta:
        ordering = ["order", "pk"]

    def __str__(self):
        return f"{self.project.key} · step {self.order}: {self.approver.display_name}"


class DeploymentApproval(models.Model):
    """One Jenkins pipeline run waiting on Planwise for its production-deploy approval chain."""

    STATUS_CHOICES = [("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected")]

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="deployment_approvals")
    project_vm = models.ForeignKey(
        ProjectVM, on_delete=models.CASCADE, related_name="deployment_approvals", null=True, blank=True
    )

    jenkins_job = models.CharField(max_length=255, blank=True)
    jenkins_build_number = models.CharField(max_length=20, blank=True)
    jenkins_build_url = models.CharField(max_length=500, blank=True)
    jenkins_input_id = models.CharField(max_length=120, blank=True)

    steps = models.JSONField(
        default=list,
        help_text="Snapshot of approvers, in order, at request time: [{'user_id': int, 'name': str}, ...]",
    )
    current_step = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")

    jenkins_notify_error = models.CharField(max_length=500, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.project.key} · build #{self.jenkins_build_number or '?'} ({self.status})"

    def _current_step(self):
        if self.status != "pending" or self.current_step >= len(self.steps):
            return None
        return self.steps[self.current_step]

    @property
    def current_step_name(self):
        step = self._current_step()
        return step["name"] if step else None

    @property
    def current_step_user_id(self):
        step = self._current_step()
        return step["user_id"] if step else None


class DeploymentApprovalDecision(models.Model):
    """One person's yes/no on one step of a DeploymentApproval's chain."""

    DECISION_CHOICES = [("approved", "Approved"), ("rejected", "Rejected")]

    approval = models.ForeignKey(DeploymentApproval, on_delete=models.CASCADE, related_name="decisions")
    step_index = models.PositiveIntegerField()
    step_name = models.CharField(max_length=120)
    decision = models.CharField(max_length=10, choices=DECISION_CHOICES)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    decided_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["decided_at"]

    def __str__(self):
        return f"{self.step_name}: {self.decision}"
