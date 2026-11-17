from django import forms
from django.conf import settings
from django.db.models import Q

from apps.accounts.models import Team, User
from apps.projects.models import Label, Project

from .models import Ticket, TicketComment


class DateInput(forms.DateInput):
    input_type = "date"


def _staff():
    """Active internal people. Client-side logins are never ticket assignees: the Customer role, or a
    customer link with no role at all (see User.is_client_login). Staff who are merely linked to a
    customer record, such as a BDA on that customer's account, are internal and can be assigned."""
    return User.objects.filter(is_active=True).exclude(
        Q(role__slug=User.CLIENT_ROLE_SLUG) | Q(role__isnull=True, customer__isnull=False)
    )


def _always_ids():
    names = getattr(settings, "TICKET_ALWAYS_ASSIGNEES", ())
    return set(_staff().filter(username__in=names).values_list("pk", flat=True))


def assignable_users(project=None):
    """-> (everyone who can be picked as a ticket's assignee, ids that must never be filtered out).

    Anyone active on the internal staff can be assigned a ticket, whether or not they are on the project
    (client contacts never can). The Team box on the ticket form only narrows the list. The returned
    ids are the always-available people from settings.TICKET_ALWAYS_ASSIGNEES, which that filter never hides."""
    return _staff(), _always_ids()


class TeamAwareSelect(forms.Select):
    """A <select> whose <option>s each carry the user's team as a data attribute, so the
    assignee dropdown can be narrowed to one team client-side — no reload, no extra request.
    `team_map` is {user_id: team_id}, built once in TicketForm.__init__ to avoid a query per
    option. `pinned_ids` get data-pinned="1" so the team filter leaves them in the list. With
    `team_only`, everyone else gets data-extra="1": offered only once their own team is picked."""

    def __init__(self, *args, team_map=None, pinned_ids=(), team_only=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.team_map = team_map or {}
        self.pinned_ids = set(pinned_ids)
        self.team_only = team_only

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        # Django wraps each choice's value in ModelChoiceIteratorValue (not a plain str/int) —
        # unwrap it via .value before casting, or int() itself raises a TypeError.
        raw = getattr(value, "value", value)
        team_id = self.team_map.get(int(raw)) if raw else None
        if team_id:
            option["attrs"]["data-team"] = team_id
        if raw and int(raw) in self.pinned_ids:
            option["attrs"]["data-pinned"] = "1"
        elif raw and self.team_only:
            option["attrs"]["data-extra"] = "1"
        return option


class TicketForm(forms.ModelForm):
    class Meta:
        model = Ticket
        fields = [
            "project", "type", "title", "description", "priority", "severity",
            "team", "assignee", "labels", "due_date",
            "steps_to_reproduce", "expected_result", "actual_result", "environment",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 5, "placeholder": "What happened? What do you need?"}),
            "steps_to_reproduce": forms.Textarea(attrs={"rows": 4, "placeholder": "1. …\n2. …\n3. …"}),
            "expected_result": forms.Textarea(attrs={"rows": 2}),
            "actual_result": forms.Textarea(attrs={"rows": 2}),
            "due_date": DateInput(),
        }

    def __init__(self, *args, project=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        project = project or (self.instance.project if self.instance.pk else None)

        assignee_qs, pinned = assignable_users(project)
        if project:
            self.fields["project"].initial = project
            self.fields["project"].widget = forms.HiddenInput()
            self.fields["project"].queryset = Project.objects.filter(pk=project.pk)
            self.fields["labels"].queryset = Label.objects.filter(Q(project=project) | Q(project__isnull=True))
        else:
            from apps.projects.selectors import visible_projects

            projects = visible_projects(user) if user else Project.objects.all()
            self.fields["project"].queryset = projects
            self.fields["labels"].queryset = Label.objects.filter(project__isnull=True)

        # keep whoever is assigned now selectable on edit, even if they've since left the project
        if self.instance.assignee_id:
            assignee_qs = assignee_qs | User.objects.filter(pk=self.instance.assignee_id)

        # widget must be swapped in BEFORE the queryset is assigned — assigning .queryset
        # populates .choices on whatever widget is current *at that moment*; setting the
        # widget afterward silently drops those choices and renders an empty <select>.
        self.fields["assignee"].widget = TeamAwareSelect(
            team_map=dict(assignee_qs.values_list("pk", "team_id")), pinned_ids=pinned,
            attrs={"data-search": "1", "data-placeholder": "Search assignee…"})
        self.fields["assignee"].queryset = assignee_qs
        # "Yashashri kulkarni (Yashashri)": searchable by name or username
        self.fields["assignee"].label_from_instance = (
            lambda u: f"{u.get_full_name()} ({u.username})" if u.get_full_name() else u.username)
        self.fields["team"].queryset = Team.objects.all()
        self.fields["team"].required = False
        self.fields["assignee"].required = False
        self.fields["labels"].required = False
        self.fields["severity"].required = False


class TicketCommentForm(forms.ModelForm):
    class Meta:
        model = TicketComment
        fields = ["body"]
        widgets = {"body": forms.Textarea(attrs={"rows": 3, "placeholder": "Add a comment…"})}
