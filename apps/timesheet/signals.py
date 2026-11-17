"""Hooks into existing events. Each one only writes timesheet tables and swallows its own
errors, so login, logout and project saves behave exactly as before even if this fails."""
import logging

from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from apps.projects.models import Project, ProjectMembership

from .middleware import current_user

logger = logging.getLogger(__name__)


@receiver(user_logged_in, dispatch_uid="timesheet_login")
def on_login(sender, request, user, **kwargs):
    try:
        from . import services

        services.record_login(user)
        services.ensure_range(user, [services.today()])  # today's 8 hours as soon as they are present
    except Exception:  # noqa: BLE001
        logger.exception("timesheet: recording login failed for %s", getattr(user, "pk", None))


@receiver(user_logged_out, dispatch_uid="timesheet_logout")
def on_logout(sender, request, user, **kwargs):
    if user is None:
        return
    # Being signed out for inactivity is not a logout the person chose, and Planwise only notices it on their
    # next visit — stamping that moment would show a "logout" beside the morning's first login.
    if getattr(request, "idle_signed_out", False):
        return
    try:
        from . import services

        services.record_logout(user)
    except Exception:  # noqa: BLE001
        logger.exception("timesheet: recording logout failed for %s", getattr(user, "pk", None))


@receiver(pre_save, sender=Project, dispatch_uid="timesheet_project_status_before")
def remember_old_status(sender, instance, **kwargs):
    try:
        instance._ts_old_status = (
            Project.objects.filter(pk=instance.pk).values_list("status", flat=True).first() if instance.pk else None
        )
    except Exception:  # noqa: BLE001
        instance._ts_old_status = None


@receiver(post_save, sender=Project, dispatch_uid="timesheet_project_status_after")
def log_status_change(sender, instance, created, **kwargs):
    try:
        from . import services
        from .models import ProjectStatusLog

        old = getattr(instance, "_ts_old_status", None)
        if not created and (old is None or old == instance.status):
            return
        actor = current_user()
        ProjectStatusLog.objects.create(
            project=instance, old_status=old or "", new_status=instance.status,
            changed_by=actor if getattr(actor, "is_authenticated", False) else None,
        )
        if not created:
            services.audit(actor, "project_status", instance.key, old, instance.status)
    except Exception:  # noqa: BLE001
        logger.exception("timesheet: logging project status failed for %s", instance.pk)


def _removed_on_its_own(origin):
    """True unless the membership only went because its user or project is being deleted: the history row
    would point at something that is about to disappear, and the database refuses it when the delete commits."""
    if origin is None:
        return True
    model = origin.model if hasattr(origin, "model") else type(origin)  # a queryset, or a single object
    return issubclass(model, ProjectMembership)


@receiver(post_delete, sender=ProjectMembership, dispatch_uid="timesheet_remember_past_assignment")
def remember_past_assignment(sender, instance, origin=None, **kwargs):
    """Someone left a project: keep the dates, so they can still edit its hours for the days they were on it."""
    if instance.side != "internal" or not _removed_on_its_own(origin):
        return
    try:
        from django.utils import timezone

        from .models import PastAssignment

        actor = current_user()
        PastAssignment.objects.create(
            user_id=instance.user_id, project_id=instance.project_id,
            started_on=timezone.localtime(instance.joined_at).date() if instance.joined_at else timezone.localdate(),
            ended_on=timezone.localdate(),
            removed_by=actor if getattr(actor, "is_authenticated", False) else None,
        )
    except Exception:  # noqa: BLE001 - removing someone from a project must always work
        logger.exception("timesheet: recording past assignment failed for %s", instance.pk)

