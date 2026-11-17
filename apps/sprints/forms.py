from django import forms

from apps.accounts.models import User

from .models import (
    AcceptanceCriterion,
    Dependency,
    ExternalService,
    FieldSpec,
    Requirement,
    RequirementRisk,
    Sprint,
    UnitTestCase,
)


class DateInput(forms.DateInput):
    input_type = "date"


def _project_members(project):
    members = User.objects.filter(memberships__project=project, is_active=True).distinct()
    return members if members.exists() else User.objects.filter(is_active=True)


class SprintForm(forms.ModelForm):
    class Meta:
        model = Sprint
        fields = ["name", "goal", "status", "start_date", "end_date", "capacity_points", "owner"]
        widgets = {
            "goal": forms.Textarea(attrs={"rows": 3, "placeholder": "What should this sprint deliver?"}),
            "start_date": DateInput(),
            "end_date": DateInput(),
        }

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        project = project or (self.instance.project if self.instance.pk else None)
        if project:
            self.fields["owner"].queryset = _project_members(project)
        self.fields["owner"].required = False


class RequirementForm(forms.ModelForm):
    class Meta:
        model = Requirement
        fields = [
            "sprint", "title", "description", "user_story", "user_story_id",
            "priority", "status", "estimate_points", "assignee",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "user_story": forms.Textarea(
                attrs={"rows": 2, "placeholder": "As a <role> I want <goal> so that <benefit>"}
            ),
        }

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        project = project or (self.instance.project if self.instance.pk else None)
        if project:
            self.fields["sprint"].queryset = Sprint.objects.filter(project=project)
            self.fields["assignee"].queryset = _project_members(project)
        self.fields["sprint"].required = False
        self.fields["assignee"].required = False


class AcceptanceCriterionForm(forms.ModelForm):
    class Meta:
        model = AcceptanceCriterion
        fields = ["format", "statement", "given_text", "when_text", "then_text", "order", "notes"]
        widgets = {
            "statement": forms.Textarea(attrs={"rows": 2, "placeholder": "User can enter first name."}),
            "given_text": forms.Textarea(attrs={"rows": 2, "placeholder": "a valid user is logged in"}),
            "when_text": forms.Textarea(attrs={"rows": 2, "placeholder": 'the user selects "Add Participant"'}),
            "then_text": forms.Textarea(attrs={"rows": 2, "placeholder": "the participant creation form is displayed"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def clean(self):
        cleaned = super().clean()
        fmt = cleaned.get("format")
        if fmt == "statement" and not (cleaned.get("statement") or "").strip():
            self.add_error("statement", "A statement is required.")
        if fmt == "gwt":
            for name in ("given_text", "when_text", "then_text"):
                if not (cleaned.get(name) or "").strip():
                    self.add_error(name, "Required for a Given/When/Then criterion.")
        return cleaned


class FieldSpecForm(forms.ModelForm):
    class Meta:
        model = FieldSpec
        fields = [
            "name", "field_type", "mandatory", "mandatory_condition",
            "max_length", "validation", "default_value", "order", "notes",
        ]


class RequirementRiskForm(forms.ModelForm):
    class Meta:
        model = RequirementRisk
        fields = [
            "category", "title", "description", "likelihood", "impact",
            "mitigation", "owner", "owner_note", "status", "target_date",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "mitigation": forms.Textarea(attrs={"rows": 3}),
            "target_date": DateInput(),
        }

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        if project:
            self.fields["owner"].queryset = _project_members(project)
        self.fields["owner"].required = False


class DependencyForm(forms.ModelForm):
    class Meta:
        model = Dependency
        fields = [
            "kind", "title", "description", "depends_on", "external_type",
            "owner", "owner_note", "due_date", "status",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "due_date": DateInput(),
        }

    def __init__(self, *args, project=None, requirement=None, **kwargs):
        super().__init__(*args, **kwargs)
        project = project or (requirement.project if requirement else None)
        qs = Requirement.objects.none()
        if project:
            qs = Requirement.objects.filter(project=project)
            if requirement:
                qs = qs.exclude(pk=requirement.pk)
            self.fields["owner"].queryset = _project_members(project)
        self.fields["depends_on"].queryset = qs
        self.fields["depends_on"].required = False
        self.fields["owner"].required = False

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("kind") == "external" and not (cleaned.get("external_type") or "").strip():
            self.add_error("external_type", "Name the external service or item this depends on.")
        return cleaned


class UnitTestCaseForm(forms.ModelForm):
    class Meta:
        model = UnitTestCase
        fields = [
            "requirement", "criterion", "title", "test_type", "user_story_id",
            "preconditions", "input_data", "steps", "expected_result",
        ]
        widgets = {
            "preconditions": forms.Textarea(attrs={"rows": 2}),
            "input_data": forms.Textarea(attrs={"rows": 2}),
            "steps": forms.Textarea(attrs={"rows": 4, "placeholder": "1. …\n2. …\n3. …"}),
            "expected_result": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, project=None, requirement=None, **kwargs):
        super().__init__(*args, **kwargs)
        project = project or (requirement.project if requirement else None)
        if requirement:
            self.fields["requirement"].queryset = Requirement.objects.filter(pk=requirement.pk)
            self.fields["requirement"].initial = requirement
            self.fields["criterion"].queryset = requirement.criteria.all()
        elif project:
            self.fields["requirement"].queryset = Requirement.objects.filter(project=project)
            self.fields["criterion"].queryset = AcceptanceCriterion.objects.filter(requirement__project=project)
        self.fields["criterion"].required = False
        self.fields["requirement"].required = False


class TestExecutionForm(forms.ModelForm):
    class Meta:
        model = UnitTestCase
        fields = ["status", "actual_result", "executed_on", "comments"]
        widgets = {
            "actual_result": forms.Textarea(attrs={"rows": 3}),
            "comments": forms.Textarea(attrs={"rows": 2}),
            "executed_on": DateInput(),
        }


class ExternalServiceForm(forms.ModelForm):
    class Meta:
        model = ExternalService
        fields = [
            "category", "name", "description", "provided_by", "status",
            "owner", "owner_note", "expected_date", "received_date", "details", "requirements",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "details": forms.Textarea(attrs={"rows": 3}),
            "expected_date": DateInput(),
            "received_date": DateInput(),
        }

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        project = project or (self.instance.project if self.instance.pk else None)
        if project:
            self.fields["owner"].queryset = _project_members(project)
            self.fields["requirements"].queryset = Requirement.objects.filter(project=project)
        self.fields["owner"].required = False
        self.fields["requirements"].required = False
