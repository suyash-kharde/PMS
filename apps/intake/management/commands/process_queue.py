"""Background worker: runs the AI job queue (apps.jobs) and the timesheet's scheduled jobs.

    python manage.py process_queue --loop      # container mode
    python manage.py process_queue --once      # run whatever is waiting, then exit
    python manage.py process_queue --entry 12  # reprocess one entry directly, outside the queue
    python manage.py process_queue --plan-run 3

In --loop mode one thread per lane slot (JOB_LANES, default "audio=1,ai=2,setup=1") claims and runs
jobs, so a long recording never holds up a BRD update. The main thread adopts waiting work that
has no job yet (rows queued by signals or commands), keeps Penpot workspaces in sync, re-publishes
project sites to the site gateway every few minutes and runs the timesheet jobs once a minute,
unaffected by how long any AI job takes.
"""

import os
import threading
import time
import traceback

from django.core.management.base import BaseCommand
from django.db import close_old_connections, connection

from apps.intake.models import TimelineEntry
from apps.intake.services import pipeline


class Command(BaseCommand):
    help = "Run the AI job queue (recordings, BRD updates, Build Plans, sprint plans) and timesheet jobs."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true", help="keep running")
        parser.add_argument("--once", action="store_true", help="run whatever is waiting, then exit")
        parser.add_argument("--entry", type=int, help="process one entry by id, whatever its status")
        parser.add_argument("--plan-run", type=int, help="run one sprint plan generation by id")
        parser.add_argument("--interval", type=float, default=float(os.environ.get("WORKER_INTERVAL", "5")))

    def handle(self, *args, **options):
        if options.get("plan_run"):
            from apps.sprints.models import SprintPlanRun
            from apps.sprints.services import pipeline as sprint_pipeline

            sprint_pipeline.run(SprintPlanRun.objects.get(pk=options["plan_run"]))
            return

        if options.get("entry"):
            entry = TimelineEntry.objects.get(pk=options["entry"])
            self.stdout.write(f"[entry {entry.pk}] {entry.project.key} · {entry.title}")
            pipeline.process_entry(entry)
            entry.refresh_from_db()
            self.stdout.write(self.style.SUCCESS(f"[entry {entry.pk}] -> {entry.status}"))
            return

        from apps.jobs import services as jobs

        if not options.get("loop"):
            jobs.adopt_orphans()
            self.stdout.write(f"ran {jobs.run_pending()} job(s)")
            return

        jobs.recover_interrupted()
        from apps.jobs.handlers import adopt_all

        adopted = adopt_all(startup=True)
        lanes = jobs.lanes()
        self.stdout.write(self.style.SUCCESS(
            f"worker started; AI queue lanes {lanes}; re-queued {adopted} waiting item(s)"))
        for lane, slots in lanes.items():
            for n in range(slots):
                threading.Thread(target=self._slot, args=({lane: slots}, options["interval"]),
                                 name=f"job-{lane}-{n + 1}", daemon=True).start()

        last_tick = last_sites = 0.0
        while True:
            try:
                close_old_connections()
                jobs.adopt_orphans()
                self._penpot_refresh()
            except Exception:  # noqa: BLE001 - housekeeping must not kill the worker
                self.stderr.write(traceback.format_exc())
            if time.monotonic() - last_tick >= 60:
                last_tick = time.monotonic()
                self._timesheet_tick()
            if time.monotonic() - last_sites >= 180:
                last_sites = time.monotonic()
                self._sites_sync()
            time.sleep(options["interval"])

    def _slot(self, limits, interval):
        """One worker slot: claim a job from its lane, run it, repeat."""
        from apps.jobs import services as jobs

        while True:
            job = None
            try:
                close_old_connections()
                job = jobs.claim(limits)
                if job is None:
                    time.sleep(interval)
                    continue
                self.stdout.write(f"[job {job.pk}] {job.label} — started ({threading.current_thread().name})")
                jobs.run(job)
                self.stdout.write(f"[job {job.pk}] {job.label} -> {job.status}"
                                  + (f": {job.error[:200]}" if job.error and job.status != "done" else ""))
            except Exception:  # noqa: BLE001 - a slot must survive anything
                self.stderr.write(traceback.format_exc())
                time.sleep(interval)
            finally:
                connection.close()  # each job starts on a fresh connection (CONN_MAX_AGE=0 style)

    def _penpot_refresh(self):
        from apps.penpot import services as penpot

        if penpot.enabled():
            penpot._refresh_ready(stdout=self.stdout)

    def _sites_sync(self):
        """Keep the site gateway's list in line with the Project Links opened through Planwise
        (it may have been down, or restarted empty)."""
        try:
            from apps.projects import sites

            pushed, removed = sites.sync_all()
            if pushed or removed:
                self.stdout.write(f"[sites] gateway synced: {pushed} published, {removed} removed")
        except Exception:  # noqa: BLE001
            self.stderr.write(traceback.format_exc())

    def _timesheet_tick(self):
        """Timesheet scheduled jobs (attendance, auto-fill, weekly report, month lock). Runs on the
        main thread, so AI jobs never delay the 18:30 absent check."""
        try:
            from apps.timesheet.services import run_scheduled_jobs

            run_scheduled_jobs()
        except Exception:  # noqa: BLE001
            self.stderr.write(traceback.format_exc())
