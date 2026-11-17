from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = ("Email today's 'Planwise login pending' reminder. The worker sends it automatically Mon-Fri at "
            "LOGIN_REMINDER_TIME; use this to preview (--dry-run) or to send one test mail (--only someone@...).")

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="List who would get it; send nothing")
        parser.add_argument("--only", help="Send only to this one address (test); nobody else is mailed")

    def handle(self, *args, **opts):
        from apps.timesheet.services import job_login_reminders

        result = job_login_reminders(timezone.localdate(), dry_run=opts["dry_run"], only_email=opts["only"])
        label = "would send" if opts["dry_run"] else "sent"
        self.stdout.write(f"{label}: {len(result['sent'])}")
        for a in result["sent"]:
            self.stdout.write(f"  {a}")
        for a in result["failed"]:
            self.stdout.write(self.style.ERROR(f"  FAILED {a}"))
        if result["skipped"]:
            self.stdout.write(f"already mailed today: {len(result['skipped'])}")
