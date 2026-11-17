from django import forms
from django.utils.crypto import get_random_string
from django.utils.text import slugify

from apps.accounts.models import Role, User
from apps.projects.models import ProjectMembership

from .models import Speaker, TimelineEntry, TranscriptSegment


class DateTimeInput(forms.DateTimeInput):
    input_type = "datetime-local"

    def format_value(self, value):
        value = super().format_value(value)
        return value[:16] if value else value


class TimelineEntryForm(forms.ModelForm):
    class Meta:
        model = TimelineEntry
        fields = ["title", "kind", "occurred_at", "body", "upload", "include_in_brd"]
        widgets = {
            "body": forms.Textarea(attrs={"rows": 5, "placeholder": "Notes, decisions, requirements discussed…"}),
            "occurred_at": DateTimeInput(),
            "upload": forms.ClearableFileInput(attrs={
                "accept": ".txt,.md,.csv,.json,.srt,.vtt,.pdf,.docx,.pptx,audio/*,video/mp4",
            }),
        }

    def clean(self):
        data = super().clean()
        if not data.get("body") and not data.get("upload"):
            raise forms.ValidationError("Add some text or attach a file.")
        return data


class ParticipantChoiceField(forms.ModelChoiceField):
    """Renders the project's People list: name, side and position, with data-* for the JS."""

    def __init__(self, *args, project=None, **kwargs):
        self.project = project
        self.briefs = {}
        if project is not None:
            self.briefs = {
                m.user_id: m for m in project.memberships.select_related("user", "role")
            }
        super().__init__(*args, **kwargs)

    def label_from_instance(self, obj):
        membership = self.briefs.get(obj.pk)
        if membership is None:
            return obj.display_name
        bits = [membership.get_side_display()]
        if membership.position:
            bits.insert(0, membership.position)
        return f"{obj.display_name} — {' · '.join(bits)}"


class SpeakerSelect(forms.Select):
    """Adds data-name/data-side to each option so the transcript can relabel live."""

    def __init__(self, *args, field=None, **kwargs):
        self.field = field
        super().__init__(*args, **kwargs)

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        pk = getattr(value, "value", value)
        membership = (self.field.briefs.get(pk) if self.field else None) if pk else None
        if membership is not None:
            option["attrs"]["data-name"] = membership.user.display_name
            option["attrs"]["data-side"] = membership.side
        return option


class SpeakerNameForm(forms.ModelForm):
    """Name a diarized voice and map it to somebody from the project's People list."""

    class Meta:
        model = Speaker
        fields = ["display_name", "user", "side", "notes"]
        widgets = {"notes": forms.TextInput(attrs={"placeholder": "optional remark"})}

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        participants = (
            User.objects.filter(memberships__project=project).distinct()
            if project is not None
            else User.objects.none()
        )
        field = ParticipantChoiceField(
            queryset=participants,
            project=project,
            required=False,
            label="Project member",
            empty_label="— not on the People list —",
        )
        field.widget = SpeakerSelect(field=field, choices=field.choices)
        self.fields["user"] = field
        self.fields["display_name"].required = False
        self.fields["display_name"].widget.attrs["placeholder"] = "or type a name"

    def clean(self):
        data = super().clean()
        if not data.get("display_name") and not data.get("user"):
            raise forms.ValidationError("Pick someone from the People list or type a name.")
        if data.get("user") and not data.get("side"):
            membership = self.fields["user"].briefs.get(data["user"].pk)
            if membership:
                data["side"] = membership.side
        return data


class SegmentForm(forms.ModelForm):
    """One editable line of the transcript."""

    delete = forms.BooleanField(required=False)

    class Meta:
        model = TranscriptSegment
        fields = ["speaker_label", "start", "end", "text"]
        widgets = {
            "text": forms.Textarea(attrs={"rows": 2}),
            "start": forms.NumberInput(attrs={"step": "0.1", "min": "0"}),
            "end": forms.NumberInput(attrs={"step": "0.1", "min": "0"}),
        }

    def __init__(self, *args, labels=None, **kwargs):
        super().__init__(*args, **kwargs)
        if labels:
            self.fields["speaker_label"] = forms.ChoiceField(
                choices=[(l, l) for l in labels], required=True, label="Speaker"
            )

    def clean(self):
        data = super().clean()
        if data.get("end") is not None and data.get("start") is not None and data["end"] < data["start"]:
            self.add_error("end", "End must be at or after the start.")
        return data


BRIEF_WIDGETS = {
    "role_description": forms.Textarea(attrs={"rows": 2}),
    "responsibility": forms.Textarea(attrs={"rows": 2}),
    "remarks": forms.Textarea(attrs={"rows": 2}),
}


class MembershipForm(forms.ModelForm):
    """Add an existing user to the project, with their brief and remarks."""

    class Meta:
        model = ProjectMembership
        fields = [
            "user", "side", "position", "role", "role_description",
            "responsibility", "remarks", "is_primary_contact",
        ]
        widgets = BRIEF_WIDGETS

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.project = project
        if project is not None and not self.instance.pk:
            taken = project.memberships.values_list("user_id", flat=True)
            self.fields["user"].queryset = User.objects.exclude(pk__in=taken).order_by("first_name", "username")
        self.fields["user"].empty_label = "— pick a person —"

    def clean_user(self):
        user = self.cleaned_data["user"]
        if self.project is not None and not self.instance.pk:
            if ProjectMembership.objects.filter(project=self.project, user=user).exists():
                raise forms.ValidationError(f"{user.display_name} is already on this project.")
        return user


class NewPersonForm(forms.Form):
    """Create a user account and attach them to the project in one step."""

    first_name = forms.CharField(max_length=60)
    last_name = forms.CharField(max_length=60, required=False)
    # autocomplete is off on purpose: the password field below makes Chrome offer to fill
    # the *current* user's credentials into these boxes
    username = forms.CharField(
        max_length=150, required=False, help_text="Leave blank to derive it from the name",
        widget=forms.TextInput(attrs={"autocomplete": "off", "data-lpignore": "true"}),
    )
    email = forms.EmailField(required=False, widget=forms.EmailInput(attrs={"autocomplete": "off"}))
    phone = forms.CharField(max_length=40, required=False)
    password = forms.CharField(
        max_length=128, required=False,
        widget=forms.PasswordInput(render_value=True, attrs={"autocomplete": "new-password"}),
        help_text="Leave blank for a random password the person must reset",
    )
    system_role = forms.ModelChoiceField(queryset=Role.objects.all(), required=False, label="System role")

    side = forms.ChoiceField(choices=ProjectMembership._meta.get_field("side").choices, initial="client")
    position = forms.CharField(max_length=120, required=False, label="Position on this project")
    role_description = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)
    responsibility = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)
    remarks = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)
    is_primary_contact = forms.BooleanField(required=False)

    def clean_username(self):
        username = (self.cleaned_data.get("username") or "").strip()
        if username and User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("That username is taken.")
        return username

    def derive_username(self):
        base = (
            self.cleaned_data.get("username")
            or slugify(f"{self.cleaned_data['first_name']}.{self.cleaned_data.get('last_name', '')}").strip(".-")
            or "person"
        )
        candidate, n = base, 1
        while User.objects.filter(username__iexact=candidate).exists():
            n += 1
            candidate = f"{base}{n}"
        return candidate

    def save(self, project):
        data = self.cleaned_data
        password = data.get("password") or get_random_string(14)
        user = User.objects.create_user(
            username=self.derive_username(),
            password=password,
            first_name=data["first_name"],
            last_name=data.get("last_name", ""),
            email=data.get("email", ""),
            phone=data.get("phone", ""),
            title=data.get("position", ""),
            role=data.get("system_role"),
        )
        membership = ProjectMembership.objects.create(
            project=project,
            user=user,
            role=data.get("system_role"),
            side=data["side"],
            position=data.get("position", ""),
            role_description=data.get("role_description", ""),
            responsibility=data.get("responsibility", ""),
            remarks=data.get("remarks", ""),
            is_primary_contact=data.get("is_primary_contact", False),
        )
        return user, membership, (None if data.get("password") else password)


class BRDEditForm(forms.Form):
    markdown = forms.CharField(widget=forms.Textarea(attrs={"rows": 24, "spellcheck": "false"}))
    summary = forms.CharField(
        max_length=300, required=False, widget=forms.TextInput(attrs={"placeholder": "What changed?"})
    )
