from django import forms

from apps.projects.models import Task
from apps.sprints.models import Requirement, Sprint
from apps.tickets.models import Ticket

from .models import DevUpdate


class FilesField(forms.CharField):
    """Edit `files_changed` (a JSON list) as one path per line."""

    widget = forms.Textarea(attrs={"rows": 3, "placeholder": "one file path per line"})

    def prepare_value(self, value):
        if isinstance(value, (list, tuple)):
            return "\n".join(value)
        return value or ""

    def clean(self, value):
        value = super().clean(value)
        return [line.strip() for line in (value or "").splitlines() if line.strip()]


class DevUpdateForm(forms.ModelForm):
    files_changed = FilesField(required=False, label="Files changed")

    class Meta:
        model = DevUpdate
        fields = [
            "headline", "details", "kind", "status", "milestone", "milestone_done",
            "sprint", "requirement", "ticket", "task",
            "files_changed", "branch", "commit", "pr_url",
        ]
        widgets = {
            "details": forms.Textarea(attrs={"rows": 6, "placeholder": "What changed and why (markdown)"}),
            "milestone": forms.TextInput(attrs={"placeholder": "e.g. M2 · Auth API"}),
        }

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        project = project or (self.instance.project if self.instance.pk else None)
        if project:
            self.fields["sprint"].queryset = Sprint.objects.filter(project=project)
            self.fields["requirement"].queryset = Requirement.objects.filter(project=project)
            self.fields["ticket"].queryset = Ticket.objects.filter(project=project)
            self.fields["task"].queryset = Task.objects.filter(project=project)
        for name in ("sprint", "requirement", "ticket", "task"):
            self.fields[name].required = False
