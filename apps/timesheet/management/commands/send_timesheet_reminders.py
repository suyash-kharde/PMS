from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = ("Email the 'timesheet submission pending' reminder. The worker sends it automatically on Fridays at "
            "TIMESHEET_REMINDER_TIME (when TIMESHEET_REMINDER_ENABLED=1); use this to preview (--dry-run) or to "
            "send one test mail (--only someone@...).")

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="List who would get it and for which weeks; send nothing")
        parser.add_argument("--only", help="Send only to this one address (test); nobody else is mailed")
        parser.add_argument("--date", help="Pretend today is this date (YYYY-MM-DD); only with --dry-run or --only")

    def handle(self, *args, **opts):
        from apps.timesheet.services import job_timesheet_reminders

        day = timezone.localdate()
        if opts["date"]:
            if not (opts["dry_run"] or opts["only"]):
                raise CommandError("--date only works with --dry-run or --only, so a real send can't use a pretend date.")
            try:
                day = date.fromisoformat(opts["date"])
            except ValueError:
                raise CommandError("--date must look like 2026-10-16")
        result = job_timesheet_reminders(day, dry_run=opts["dry_run"], only_email=opts["only"])
        label = "would send" if opts["dry_run"] else "sent"
        self.stdout.write(f"{label}: {len(result['sent'])}   (as of {day:%a %d %b %Y})")
        if opts["dry_run"] or opts["only"]:
            for name, weeks in result["plan"]:
                spans = ", ".join(f"{s:%d %b}–{e:%d %b}" for s, e in weeks)
                self.stdout.write(f"  {name}: {spans}")
        for a in result["failed"]:
            self.stdout.write(self.style.ERROR(f"  FAILED {a}"))
        if result["skipped"]:
            self.stdout.write(f"already mailed today: {len(result['skipped'])}")
