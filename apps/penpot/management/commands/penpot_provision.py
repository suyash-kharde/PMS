"""Provision / re-provision Penpot design workspaces.

    python manage.py penpot_provision --all             # every queued workspace
    python manage.py penpot_provision --project PLAT     # one project (queues if needed)
    python manage.py penpot_provision --project PLAT --repair   # forget the Penpot side, rebuild
    python manage.py penpot_provision --backfill         # queue every project that has none
    python manage.py penpot_provision --loop             # keep draining (worker mode)
"""

import time

from django.core.management.base import BaseCommand, CommandError

from apps.penpot import services
from apps.penpot.models import PenpotWorkspace
from apps.projects.models import Project


class Command(BaseCommand):
    help = "Provision Penpot design workspaces for projects."

    def add_arguments(self, parser):
        parser.add_argument("--project", help="Project key or id")
        parser.add_argument("--repair", action="store_true",
                            help="with --project: forget the stored Penpot ids and rebuild from scratch")
        parser.add_argument("--all", action="store_true", help="drain every queued workspace")
        parser.add_argument("--backfill", action="store_true", help="queue projects with no workspace")
        parser.add_argument("--loop", action="store_true", help="keep draining")
        parser.add_argument("--interval", type=float, default=10.0)

    def handle(self, *args, **opts):
        if not services.enabled():
            raise CommandError("Penpot integration is disabled — set PENPOT_ENABLED=1 and PENPOT_BASE_URL.")

        if opts["backfill"]:
            n = 0
            for project in Project.objects.filter(penpot_workspace__isnull=True):
                services.enqueue_for_project(project)
                n += 1
            self.stdout.write(self.style.SUCCESS(f"queued {n} project(s)"))

        if opts["project"]:
            project = self._resolve(opts["project"])
            workspace = services.enqueue_for_project(project) or PenpotWorkspace.objects.filter(
                project=project
            ).first()
            if workspace is None:
                raise CommandError("could not queue a workspace (is PENPOT_ENABLED set?)")
            if opts["repair"]:
                workspace = services.rebuild(workspace, stdout=self.stdout)
            else:
                services.provision(workspace, stdout=self.stdout)
            self.stdout.write(self.style.SUCCESS(f"{project.key}: {workspace.status}"))
            return

        if opts["loop"]:
            self.stdout.write(self.style.SUCCESS("penpot_provision loop started"))
            while True:
                services.drain_workspaces(self.stdout)
                time.sleep(opts["interval"])

        if opts["all"] or opts["backfill"]:
            services.drain_workspaces(self.stdout)

    def _resolve(self, ref):
        project = Project.objects.filter(key__iexact=ref).first()
        if project is None and str(ref).isdigit():
            project = Project.objects.filter(pk=int(ref)).first()
        if project is None:
            raise CommandError(f"no project matching {ref!r}")
        return project
