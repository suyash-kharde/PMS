"""Queryset scoping — one place that turns a capability scope into a filter."""

from django.db.models import Q

from apps.accounts.models import SCOPE_ALL, SCOPE_TEAM
from apps.accounts.permissions import has_cap, scope_of

from .models import Customer, Project, Task


def visible_customers(user):
    """A customer is visible when any of their projects is, or when this user is the one who
    created it — otherwise a brand-new customer with no project on it yet would be invisible to
    its own creator until someone adds a project. Administrators see every customer; a wider
    customer.view scope no longer shows customers whose projects the person isn't on."""
    qs = Customer.objects.select_related("account_manager", "primary_contact")
    if not user.is_authenticated:
        return qs.none()
    if is_admin(user):
        return qs
    if scope_of(user, "customer.view") is None:
        return qs.none()
    return qs.filter(Q(projects__in=visible_projects(user)) | Q(created_by=user)).distinct()


def is_admin(user):
    """Superusers and anyone whose role can manage roles: the people who see every project."""
    return user.is_superuser or has_cap(user, "role.manage")


def visible_projects(user):
    """Administrators see every project. Everyone else sees only projects they are on (owner or
    member), whatever their role's scope and whether or not the project has a customer."""
    qs = Project.objects.select_related("owner", "team", "workflow", "parent")
    if not user.is_authenticated:
        return qs.none()
    if is_admin(user):
        return qs
    return qs.filter(Q(owner=user) | Q(members=user)).distinct()


def visible_tasks(user):
    qs = Task.objects.select_related("project", "state", "assignee", "parent")
    if not user.is_authenticated:
        return qs.none()
    if is_admin(user):
        return qs
    # tasks follow project membership too, plus anything assigned to or raised by the person
    return qs.filter(Q(assignee=user) | Q(reporter=user) | Q(project__in=visible_projects(user))).distinct()


def visible_users(user):
    from apps.accounts.models import User

    qs = User.objects.select_related("role", "team", "manager")
    if not user.is_authenticated:
        return qs.none()
    scope = scope_of(user, "user.view")
    if user.is_superuser or scope == SCOPE_ALL:
        return qs
    if scope == SCOPE_TEAM:
        reach = user.subordinate_ids()
        team_ids = user.team.descendant_ids() if user.team_id else set()
        return qs.filter(Q(id__in=reach) | Q(team_id__in=team_ids)).distinct()
    return qs.filter(pk=user.pk)
