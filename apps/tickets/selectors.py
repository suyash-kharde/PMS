"""Queryset scoping for tickets — mirrors apps.projects.selectors."""

from django.db.models import Q

from apps.accounts.permissions import scope_of
from apps.projects.selectors import is_admin, visible_projects

from .models import Ticket


def visible_tickets(user):
    """Administrators see every ticket. Everyone else sees tickets on the projects they are on, plus
    ones they raised, are assigned or watch — whatever their ticket.view scope."""
    qs = Ticket.objects.select_related("project", "assignee", "reporter", "linked_task")
    if not user.is_authenticated:
        return qs.none()
    if is_admin(user):
        return qs
    mine = Q(reporter=user) | Q(assignee=user) | Q(watchers=user)
    if scope_of(user, "ticket.view") is None:
        # no explicit grant — only things the person is personally attached to
        return qs.filter(mine).distinct()
    return qs.filter(Q(project__in=visible_projects(user)) | mine).distinct()
