"""Delete all business data.

    python manage.py reset_data --yes                 # keeps roles, capabilities, workflows, superusers
    python manage.py reset_data --yes --users         # also deletes non-superuser accounts and teams
    python manage.py reset_data --yes --everything    # also deletes roles, capabilities and workflows

Always destructive — requires --yes.
"""

import shutil

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import Capability, Role, Team, User, UserCapability
from apps.intake.models import BRD, BRDVersion, MediaAsset, Speaker, TimelineEntry, TranscriptSegment
from apps.projects.models import (
    Activity, Comment, Customer, Label, Milestone, Project, ProjectMembership, Task, Workflow, WorkflowState,
)


class Command(BaseCommand):
    help = "Delete all projects, customers, timeline entries and BRDs."

    def add_arguments(self, parser):
        parser.add_argument("--yes", action="store_true", help="required: confirms the deletion")
        parser.add_argument("--users", action="store_true", help="also delete non-superuser accounts and teams")
        parser.add_argument("--everything", action="store_true", help="also delete roles, capabilities and workflows")
        parser.add_argument("--keep-media", action="store_true", help="do not delete uploaded files")

    @transaction.atomic
    def handle(self, *args, **options):
        if not options["yes"]:
            raise CommandError("refusing to delete without --yes")

        counts = {}
        order = [
            ("BRD versions", BRDVersion), ("BRDs", BRD),
            ("transcript segments", TranscriptSegment), ("speakers", Speaker),
            ("media assets", MediaAsset), ("timeline entries", TimelineEntry),
            ("comments", Comment), ("activities", Activity), ("tasks", Task),
            ("milestones", Milestone), ("memberships", ProjectMembership),
            ("labels", Label), ("projects", Project), ("customers", Customer),
        ]
        for label, model in order:
            counts[label] = model.objects.all().delete()[0]

        if options["users"] or options["everything"]:
            counts["user overrides"] = UserCapability.objects.all().delete()[0]
            counts["users"] = User.objects.filter(is_superuser=False).delete()[0]
            counts["teams"] = Team.objects.all().delete()[0]

        if options["everything"]:
            counts["workflow states"] = WorkflowState.objects.all().delete()[0]
            counts["workflows"] = Workflow.objects.all().delete()[0]
            User.objects.update(role=None)
            counts["roles"] = Role.objects.all().delete()[0]
            counts["capabilities"] = Capability.objects.all().delete()[0]

        if not options["keep_media"]:
            media = settings.MEDIA_ROOT / "projects"
            if media.exists():
                shutil.rmtree(media, ignore_errors=True)
                counts["uploaded files"] = "removed"

        for label, n in counts.items():
            if n:
                self.stdout.write(f"  deleted {n} {label}")
        self.stdout.write(self.style.SUCCESS("data reset complete"))
        if not options["everything"]:
            self.stdout.write("kept: roles, capabilities, workflows, superusers")
