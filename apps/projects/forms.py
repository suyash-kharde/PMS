import re

from django import forms
from django.db import models

from apps.accounts.models import Team, User

from .models import (
    Comment, Customer, Label, Milestone, Project, ProjectDocument, ProjectLink, Task, Workflow, WorkflowState,
)


INDUSTRY_OTHER = "__other__"
INDUSTRY_CHOICES = [
    "Information Technology (IT)",
    "Healthcare & Pharmaceuticals",
    "Banking, Financial Services & Insurance (BFSI)",
    "Manufacturing",
    "Retail & E-commerce",
    "Education & E-Learning",
]


class CustomerForm(forms.ModelForm):
    # Customer.industry stays a plain text column; these two form fields just feed it.
    industry = forms.ChoiceField(
        required=False,
        choices=[("", "Select industry")] + [(c, c) for c in INDUSTRY_CHOICES] + [(INDUSTRY_OTHER, "Other")],
    )
    industry_other = forms.CharField(
        required=False,
        max_length=120,
        label="Other industry",
        widget=forms.TextInput(attrs={"placeholder": "Enter the industry"}),
    )
    field_order = [
        "name", "code", "industry", "industry_other", "website", "account_manager", "customer_name",
        "contact_phone", "contact_email", "color", "is_active", "description", "notes",
    ]

    class Meta:
        model = Customer
        fields = [
            "name", "code", "industry", "website", "account_manager", "customer_name", "contact_phone",
            "contact_email", "color", "is_active", "description", "notes",
        ]
        labels = {
            "name": "Organization name",
            "customer_name": "Customer contact name",
            "contact_phone": "Customer primary contact",
            "contact_email": "Customer contact email",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "notes": forms.Textarea(attrs={"rows": 3}),
            "color": forms.TextInput(attrs={"type": "color"}),
            "contact_phone": forms.TextInput(attrs={
                "inputmode": "numeric",
                "pattern": "[0-9]{10}",
                "maxlength": "10",
                "placeholder": "10-digit number",
                "title": "Enter exactly 10 digits",
                "oninput": "this.value=this.value.replace(/[^0-9]/g,'')",
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["code"].required = False
        # An existing value that isn't one of the presets (older free-text entries) shows as "Other".
        current = (self.instance.industry or "").strip()
        if current:
            if current in INDUSTRY_CHOICES:
                self.initial["industry"] = current
            else:
                self.initial["industry"] = INDUSTRY_OTHER
                self.initial["industry_other"] = current

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("industry") == INDUSTRY_OTHER:
            other = (cleaned.get("industry_other") or "").strip()
            if other:
                cleaned["industry"] = other
            else:
                cleaned["industry"] = ""
                self.add_error("industry_other", "Enter the industry.")
        return cleaned

    def clean_contact_phone(self):
        phone = (self.cleaned_data.get("contact_phone") or "").strip()
        if not phone:
            return phone
        if not re.fullmatch(r"[0-9]{10}", phone):
            raise forms.ValidationError("Enter exactly 10 digits (numbers only).")
        if Customer.objects.filter(contact_phone=phone).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("This number is already registered to another customer.")
        return phone


class DateInput(forms.DateInput):
    input_type = "date"


class ProjectForm(forms.ModelForm):
    member_ids = forms.ModelMultipleChoiceField(
        queryset=User.objects.all(), required=False, label="Members"
    )

    class Meta:
        model = Project
        fields = [
            "name", "key", "customer", "description", "parent", "status", "priority", "owner", "team",
            "workflow", "start_date", "due_date", "budget", "color", "po_number", "po_document",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "start_date": DateInput(),
            "due_date": DateInput(),
            "color": forms.TextInput(attrs={"type": "color"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["key"].required = False
        self.fields["parent"].queryset = Project.objects.exclude(pk=self.instance.pk or 0)
        self.fields["workflow"].queryset = Workflow.objects.all()
        self.fields["team"].queryset = Team.objects.all()
        if self.instance.pk:
            self.fields["member_ids"].initial = self.instance.members.all()

    def clean(self):
        data = super().clean()
        start, due = data.get("start_date"), data.get("due_date")
        if start and due and due < start:
            self.add_error("due_date", "Due date must be on or after the start date.")
        return data


class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = [
            "title", "description", "project", "parent", "milestone", "state", "priority",
            "assignee", "labels", "start_date", "due_date", "estimate_hours", "spent_hours",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "start_date": DateInput(),
            "due_date": DateInput(),
        }

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        project = project or self.instance.project if self.instance.pk else project
        if project:
            self.fields["project"].initial = project
            self.fields["project"].widget = forms.HiddenInput()
            self.fields["parent"].queryset = project.tasks.exclude(pk=self.instance.pk or 0)
            self.fields["milestone"].queryset = project.milestones.all()
            self.fields["state"].queryset = project.states
            self.fields["labels"].queryset = Label.objects.filter(
                models.Q(project=project) | models.Q(project__isnull=True)
            )
            members = User.objects.filter(memberships__project=project)
            self.fields["assignee"].queryset = members if members.exists() else User.objects.all()
        else:
            self.fields["state"].queryset = WorkflowState.objects.all()

    def clean(self):
        data = super().clean()
        start, due = data.get("start_date"), data.get("due_date")
        if start and due and due < start:
            self.add_error("due_date", "Due date must be on or after the start date.")
        parent = data.get("parent")
        if parent and self.instance.pk and parent.pk == self.instance.pk:
            self.add_error("parent", "A task cannot be its own parent.")
        return data


class MilestoneForm(forms.ModelForm):
    class Meta:
        model = Milestone
        fields = ["name", "description", "due_date", "completed"]
        widgets = {"due_date": DateInput(), "description": forms.Textarea(attrs={"rows": 2})}


class CommentForm(forms.ModelForm):
    class Meta:
        model = Comment
        fields = ["body"]
        widgets = {"body": forms.Textarea(attrs={"rows": 3, "placeholder": "Write a comment…"})}


class ProjectDocumentForm(forms.ModelForm):
    class Meta:
        model = ProjectDocument
        fields = ["content"]
        widgets = {"content": forms.Textarea(attrs={"rows": 24, "spellcheck": "false"})}


class WorkflowForm(forms.ModelForm):
    class Meta:
        model = Workflow
        fields = ["name", "description", "is_default"]


class WorkflowStateForm(forms.ModelForm):
    class Meta:
        model = WorkflowState
        fields = ["name", "category", "color", "order", "wip_limit"]
        widgets = {"color": forms.TextInput(attrs={"type": "color"})}


class ProjectLinkForm(forms.ModelForm):
    """A link to the project's site plus optional sample login. The saved password is never put back on
    the page: leave the box empty to keep it, type a new one to replace it, or tick the box to remove it."""

    password = forms.CharField(
        required=False, label="Sample password",
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
    )
    clear_password = forms.BooleanField(required=False, label="Remove the saved password")
    # Open through Planwise (/sites/<name>/) — see apps/projects/sites.py
    via_gateway = forms.BooleanField(required=False, label="Open through Planwise (works outside the VPN)")
    site_name = forms.CharField(required=False, max_length=32, label="Address",
                                widget=forms.TextInput(attrs={"placeholder": "gepdec_solar"}))
    rewrite_html = forms.BooleanField(required=False, initial=True, label="Fix links in pages (HTML/CSS)")
    rewrite_js = forms.BooleanField(required=False, initial=True, label="Fix links in scripts (JavaScript)")
    isolate_storage = forms.BooleanField(required=False, initial=True, label="Keep the site's browser storage separate")
    insecure = forms.BooleanField(required=False, label="The site uses a self-signed certificate")
    script_name = forms.BooleanField(required=False,
                                     label="The site supports a sub-path itself (pgAdmin, Flask apps) - send X-Script-Name")
    js_files_only = forms.BooleanField(required=False,
                                       label="In scripts, fix only file addresses (use if pages show blank)")
    mounts = forms.CharField(required=False, label="Separate API servers",
                             widget=forms.Textarea(attrs={"rows": 2, "placeholder": "/_api = http://172.16.15.169:8039"}))
    replace = forms.CharField(required=False, label="Replace text in the site's code",
                              widget=forms.Textarea(attrs={"rows": 2, "placeholder": "old text ==> new text"}))
    sync_tokens = forms.CharField(required=False, max_length=140, label="Token cookies to coordinate",
                                  widget=forms.TextInput(attrs={"placeholder": "accessToken,refreshToken (rarely needed)"}))

    GATEWAY_FIELDS = ("rewrite_html", "rewrite_js", "isolate_storage", "insecure", "js_files_only", "script_name")

    class Meta:
        model = ProjectLink
        fields = ["title", "url", "username", "notes"]
        labels = {"title": "Name", "url": "Link", "username": "Sample username", "notes": "Notes"}
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "e.g. Staging site, UAT, Admin panel"}),
            "url": forms.URLInput(attrs={"placeholder": "https://staging.example.com"}),
            "username": forms.TextInput(attrs={"autocomplete": "off"}),
            "notes": forms.Textarea(attrs={"rows": 2, "placeholder": "Optional — e.g. needs VPN, OTP is 123456"}),
        }

    def clean_url(self):
        url = self.cleaned_data["url"].strip()
        if not url.lower().startswith(("http://", "https://")):
            raise forms.ValidationError("Enter a web link starting with http:// or https://")
        return url

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        link = self.instance
        if link.pk:
            from .sites import options_for

            opts = options_for(link)
            self.initial.update({
                "via_gateway": link.via_gateway, "site_name": link.site_name,
                "mounts": "\n".join(f"{m['path']} = {m['target']}" for m in opts.get("mounts") or []),
                "replace": "\n".join(f"{r['from']} ==> {r['to']}" for r in opts.get("replace") or []),
                "sync_tokens": opts.get("sync_tokens", ""),
                **{f: opts[f] for f in self.GATEWAY_FIELDS},
            })

    def clean(self):
        from .sites import SITE_NAME_RE, parse_mounts, parse_replacements

        data = super().clean()
        if not data.get("via_gateway"):
            return data
        name = (data.get("site_name") or "").strip().lower()
        if not SITE_NAME_RE.match(name):
            self.add_error("site_name", "Use 1–32 lowercase letters, digits, - or _, starting with a letter or digit.")
        elif ProjectLink.objects.filter(site_name=name).exclude(pk=self.instance.pk).exists():
            self.add_error("site_name", f"/sites/{name}/ is already used by another link.")
        data["site_name"] = name
        try:
            data["mounts"] = parse_mounts(data.get("mounts"))
        except ValueError as exc:
            self.add_error("mounts", str(exc))
        try:
            data["replace"] = parse_replacements(data.get("replace"))
        except ValueError as exc:
            self.add_error("replace", str(exc))
        tokens = (data.get("sync_tokens") or "").strip()
        if tokens and (tokens.count(",") != 1 or not all(t.strip() for t in tokens.split(","))):
            self.add_error("sync_tokens", "Write two cookie names separated by a comma.")
        data["sync_tokens"] = tokens
        return data

    def save(self, commit=True):
        link = super().save(commit=False)
        data = self.cleaned_data
        link.via_gateway = bool(data.get("via_gateway"))
        if link.via_gateway:
            link.site_name = data["site_name"]
            link.gateway_options = {
                **{f: bool(data.get(f)) for f in self.GATEWAY_FIELDS},
                "mounts": data.get("mounts") or [], "sync_tokens": data.get("sync_tokens", ""),
                "replace": data.get("replace") or [],
            }
        raw = self.cleaned_data.get("password")
        if self.cleaned_data.get("clear_password"):
            link.password = ""
        elif raw:
            link.set_password(raw)
        if commit:
            link.save()
        return link
