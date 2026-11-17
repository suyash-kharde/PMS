"""Queue a Penpot design workspace whenever a project is created.

Wired from `PenpotConfig.ready()`. Doing this with a signal rather than inline in
the project-create view means every path that creates a `Project` (the web form,
bulk import, a management command, the admin) gets a workspace — the view no longer
has to remember. `enqueue_for_project` is idempotent and does no network I/O.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.projects.models import Project

from . import services


@receiver(post_save, sender=Project, dispatch_uid="penpot_enqueue_workspace_on_project_create")
def enqueue_workspace(sender, instance, created, **kwargs):
    if not created or not services.enabled():
        return
    try:
        services.enqueue_for_project(instance)
    except Exception:  # noqa: BLE001 - never let this block project creation
        pass
