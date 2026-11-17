import re

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

from apps.accounts.models import User

from . import crypto
from .models import VM, ApprovalStep, ProjectVM

# git@host:org/repo.git — the SSH/SCP-style remote git itself understands but that isn't a URL
# (no scheme), so it needs its own pattern alongside the normal http(s)/ssh URL check.
_SCP_LIKE_GIT_URL = re.compile(r"^[\w.-]+@[\w.-]+:[\w./-]+$")


class VMForm(forms.ModelForm):
    class Meta:
        model = VM
        fields = ["name", "host", "os", "username", "password"]
        widgets = {"password": forms.PasswordInput(render_value=True)}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["password"].required = False


class ProjectVMForm(forms.ModelForm):
    """The VM section: picks which VM to assign (add) or shows it locked (edit), plus the
    folder path that gets created on that VM. Git and Jenkins details live in their own forms
    below, once this pairing exists.
    """

    use_existing = forms.BooleanField(
        required=False,
        label="This folder already exists on the VM — link to it instead of creating it",
    )

    class Meta:
        model = ProjectVM
        fields = ["vm", "folder_path"]
        labels = {"vm": "VM", "folder_path": "Folder path on the VM"}
        widgets = {
            "folder_path": forms.TextInput(attrs={"placeholder": "/home/project-x"}),
        }

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["folder_path"].required = True
        if self.instance.pk:
            self.fields["vm"].disabled = True
        elif project is not None:
            # a VM already assigned to this project shouldn't be offered again
            self.fields["vm"].queryset = VM.objects.exclude(project_links__project=project)


class ProjectVMGitForm(forms.ModelForm):
    """The Git section for one VM assignment: repo URL/branch, plus optional credentials for a
    private repo. Saving this never touches the VM — pulling the code is a separate action."""

    class Meta:
        model = ProjectVM
        fields = ["git_repo_url", "git_branch", "git_username", "git_token"]
        labels = {
            "git_repo_url": "Git repo URL",
            "git_branch": "Branch",
            "git_username": "Username (optional)",
            "git_token": "Access token / password (optional)",
        }
        widgets = {
            "git_repo_url": forms.TextInput(attrs={"placeholder": "https://github.com/org/repo.git or git@github.com:org/repo.git"}),
            "git_branch": forms.TextInput(attrs={"placeholder": "main"}),
            "git_token": forms.PasswordInput(render_value=True),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("git_branch", "git_username", "git_token"):
            self.fields[name].required = False
        # the stored value is encrypted ciphertext — decrypt it back so editing shows the
        # actual token instead of gibberish (same round-trip the field's own clean does in reverse)
        if self.instance.pk and self.instance.git_token:
            self.initial["git_token"] = crypto.decrypt(self.instance.git_token)

    def clean_git_repo_url(self):
        url = self.cleaned_data.get("git_repo_url", "").strip()
        if not url or _SCP_LIKE_GIT_URL.match(url):
            return url
        try:
            URLValidator(schemes=["http", "https", "ssh", "git"])(url)
        except ValidationError:
            raise forms.ValidationError(
                "Enter a valid repo URL — either https://github.com/org/repo.git "
                "or git@github.com:org/repo.git."
            )
        return url

    def clean_git_token(self):
        raw = self.cleaned_data.get("git_token", "")
        return crypto.encrypt(raw) if raw else ""


class ProjectVMJenkinsForm(forms.ModelForm):
    """The Jenkins section for one VM assignment — unrelated to VM/git wiring above it."""

    class Meta:
        model = ProjectVM
        fields = ["jenkins_url", "jenkins_user", "jenkins_api_token"]
        labels = {
            "jenkins_url": "Jenkins URL (optional)",
            "jenkins_user": "Jenkins username (optional)",
            "jenkins_api_token": "Jenkins API token (optional)",
        }
        widgets = {
            "jenkins_url": forms.TextInput(attrs={"placeholder": "https://jenkins.example.com"}),
            "jenkins_api_token": forms.PasswordInput(render_value=True),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["jenkins_url"].required = False
        self.fields["jenkins_user"].required = False
        self.fields["jenkins_api_token"].required = False


class _UserChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.display_name


class ApprovalStepForm(forms.ModelForm):
    """Adds or reassigns one rung of a project's deployment-approval ladder to a real account."""

    approver = _UserChoiceField(
        queryset=User.objects.filter(is_active=True), label="Approver", empty_label="— choose a person —"
    )

    class Meta:
        model = ApprovalStep
        fields = ["approver"]
