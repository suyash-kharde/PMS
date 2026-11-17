import time

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Run the timesheet scheduled jobs once (end-of-day attendance + auto-fill, Monday weekly report, "
        "month-end lock). The worker already calls this every minute; use --loop to run it standalone."
    )

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true")
        parser.add_argument("--interval", type=int, default=60)
        parser.add_argument("--clear-off-days", action="store_true",
                            help="One-off: remove auto-filled hours on weekends/holidays nobody edited (unlocked months)")
        parser.add_argument("--dry-run", action="store_true", help="With --clear-off-days: only list them")

    def handle(self, *args, **options):
        from apps.timesheet.services import clear_off_day_autofill, run_scheduled_jobs

        if options["clear_off_days"]:
            removed = clear_off_day_autofill(dry_run=options["dry_run"])
            for username, day in removed:
                self.stdout.write(f"{username} {day:%a %d %b %Y}")
            verb = "would be removed" if options["dry_run"] else "removed"
            self.stdout.write(self.style.SUCCESS(f"{len(removed)} off-day timesheet days {verb}"))
            return
        while True:
            run_scheduled_jobs()
            if not options["loop"]:
                self.stdout.write(self.style.SUCCESS("timesheet jobs done"))
                return
            time.sleep(options["interval"])
