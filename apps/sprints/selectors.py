"""Queryset scoping for the sprint module — mirrors apps.tickets.selectors."""

from django.db.models import Q

from apps.projects.selectors import is_admin, visible_projects

from .models import ExternalService, Requirement, Sprint, UnitTestCase


def _project_scoped(qs, user, personal_q=None):
    """Everything in this module is project-scoped: administrators see all of it, everyone else only
    what belongs to projects they are on (plus items they personally hold)."""
    if not user.is_authenticated:
        return qs.none()
    if is_admin(user):
        return qs
    reach = Q(project__in=visible_projects(user))
    if personal_q is not None:
        reach |= personal_q
    return qs.filter(reach).distinct()


def visible_sprints(user):
    qs = Sprint.objects.select_related("project", "owner")
    return _project_scoped(qs, user)


def visible_requirements(user):
    qs = Requirement.objects.select_related("project", "sprint", "assignee", "reporter")
    return _project_scoped(qs, user, personal_q=Q(assignee=user) | Q(reporter=user))


def visible_test_cases(user):
    qs = UnitTestCase.objects.select_related("project", "requirement", "developer")
    return _project_scoped(qs, user, personal_q=Q(developer=user))


def visible_services(user):
    qs = ExternalService.objects.select_related("project", "owner")
    return _project_scoped(qs, user, personal_q=Q(owner=user))
