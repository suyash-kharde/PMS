from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Capability, User, UserCapability
from apps.accounts.permissions import invalidate
from apps.accounts.testing import skip_password_prompt
from apps.audit.context import set_current_user
from apps.intake.models import TimelineEntry
from apps.intake.services.llm import LLMError
from apps.notify.models import Notification
from apps.projects.models import Project

from . import handlers, services
from .models import Job


def fake_version(n=2):
    return SimpleNamespace(number=n)


class QueueTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        self.addCleanup(set_current_user, None)
        penpot_off = mock.patch("apps.penpot.services.enabled", return_value=False)
        penpot_off.start()
        self.addCleanup(penpot_off.stop)
        self.admin = User.objects.create_superuser("boss", password="x")
        self.user = User.objects.create_user("pat", password="x")
        self.p1 = Project.objects.create(key="P1", name="One", status="active", owner=self.admin)
        self.p2 = Project.objects.create(key="P2", name="Two", status="active", owner=self.admin)

    def _entry(self, project=None, title="Note", status="queued"):
        return TimelineEntry.objects.create(project=project or self.p1, title=title, body="hello",
                                            author=self.admin, status=status, include_in_brd=True)

    # ---------------------------------------------------------------- adding
    def test_same_request_joins_the_waiting_job(self):
        a, created_a = handlers.queue_brd_update(self.p1, self.admin)
        b, created_b = handlers.queue_brd_update(self.p1, self.admin)
        self.assertEqual((a.pk, created_a, created_b), (b.pk, True, False))
        c, _ = handlers.queue_brd_regenerate(self.p1, self.admin, note="from the button")
        a.refresh_from_db()
        self.assertEqual((c.pk, a.kind, a.params["note"]), (a.pk, "brd_regenerate", "from the button"))
        self.assertEqual(Job.objects.count(), 1)

    def test_at_most_five_waiting_clicks_per_person(self):
        for i in range(5):
            project = Project.objects.create(key=f"Q{i}", name=f"Q{i}", status="active", owner=self.admin)
            handlers.queue_brd_regenerate(project, self.user)
        with self.assertRaises(services.QueueFull):
            handlers.queue_brd_regenerate(self.p1, self.user)
        handlers.queue_brd_update(self.p1, self.user)  # automatic work is never refused

    # ---------------------------------------------------------------- claiming
    def test_lane_slots_and_one_ai_job_per_project(self):
        j1, _ = handlers.queue_brd_regenerate(self.p1, self.admin)
        j2, _ = handlers.queue_build_plan(self.p1, self.admin)   # same project: must wait for j1
        j3, _ = handlers.queue_build_plan(self.p2, self.admin)
        limits = {"ai": 2}
        self.assertEqual(services.claim(limits).pk, j1.pk)
        self.assertEqual(services.claim(limits).pk, j3.pk)       # j2 skipped: P1 already running
        self.assertIsNone(services.claim(limits))               # both ai slots busy
        Job.objects.filter(pk=j1.pk).update(status="done")
        self.assertEqual(services.claim(limits).pk, j2.pk)

    def test_retry_with_back_off_then_fail_and_notify(self):
        job, _ = handlers.queue_brd_regenerate(self.p1, self.admin)
        with mock.patch("apps.intake.services.brd.regenerate", side_effect=LLMError("500 from the AI service")):
            services.run(services.claim())
            job.refresh_from_db()
            self.assertEqual((job.status, job.attempts), ("queued", 1))
            self.assertGreater(job.run_after, timezone.now())
            self.assertIsNone(services.claim())                 # waiting out the back-off
            for _ in range(2):
                Job.objects.filter(pk=job.pk).update(run_after=timezone.now())
                services.run(services.claim())
        job.refresh_from_db()
        self.assertEqual((job.status, job.attempts), ("failed", 3))
        self.assertIn("500", job.error)
        self.assertTrue(Notification.objects.filter(user=self.admin, kind="job_failed").exists())

    def test_restart_recovery_and_adopting_waiting_work(self):
        job, _ = handlers.queue_brd_regenerate(self.p1, self.admin)
        services.claim()
        self.assertEqual(services.recover_interrupted(), 1)
        job.refresh_from_db()
        self.assertEqual(job.status, "queued")
        orphan = self._entry(title="queued by a command")
        stuck = self._entry(title="cut off by a restart", status="processing")
        self.assertEqual(handlers.adopt_all(), 1)               # only the queued one while running
        self.assertEqual(handlers.adopt_all(startup=True), 1)   # at startup also the stuck one
        stuck.refresh_from_db()
        self.assertEqual(stuck.status, "queued")
        self.assertEqual(Job.objects.filter(target_id__in=[orphan.pk, stuck.pk]).count(), 2)

    # ---------------------------------------------------------------- timeline -> BRD
    def test_several_entries_become_one_brd_update(self):
        self.client.force_login(self.admin)
        for title in ("Kick-off notes", "Follow-up"):
            r = self.client.post(reverse("intake:entry_create", args=[self.p1.pk]),
                                 {"title": title, "kind": "note", "body": "We need X", "include_in_brd": "on",
                                  "occurred_at": "2026-10-07T10:00"})
            self.assertEqual(r.status_code, 302)
        self.assertEqual(Job.objects.filter(kind="timeline_doc", status="queued").count(), 2)
        with mock.patch("apps.intake.services.brd.regenerate", return_value=fake_version(7)) as regen:
            services.run_pending()
        self.assertEqual(regen.call_count, 1)                   # both entries, one BRD version
        self.assertEqual(len(regen.call_args.kwargs["entries"]), 2)
        self.assertEqual(Job.objects.get(kind="brd_update").status, "done")
        self.assertEqual(set(TimelineEntry.objects.values_list("status", flat=True)), {"ready"})

    def test_buttons_queue_instead_of_waiting(self):
        self.client.force_login(self.admin)
        with mock.patch("apps.intake.services.brd.regenerate") as regen:
            r = self.client.post(reverse("intake:brd_regenerate", args=[self.p1.pk]), follow=True)
            regen.assert_not_called()                           # nothing ran inside the request
        self.assertContains(r, "#1 in line")
        self.assertContains(r, "AI queue")
        entry = self._entry(status="ready")
        self.client.post(reverse("intake:entry_sync_brd", args=[entry.pk]))
        self.assertTrue(Job.objects.filter(kind="brd_sync", target_id=entry.pk).exists())
        self.client.post(reverse("projects:doc_generate_plan", args=[self.p1.pk]))
        self.assertFalse(Job.objects.filter(kind="build_plan").exists())  # sources not frozen: refused at once

    # ---------------------------------------------------------------- position and estimate
    def test_position_and_estimate(self):
        a, _ = handlers.queue_brd_regenerate(self.p1, self.admin)
        b, _ = handlers.queue_brd_regenerate(self.p2, self.admin)
        self.assertEqual(services.describe(a)["position"], 1)
        info = services.describe(b)
        self.assertEqual(info["position"], 2)
        self.assertRegex(info["text"], r"#2 in line · usually ready within \d+ min")
        now = timezone.now()
        for i in range(3):  # learn from finished runs: ~60 s each
            Job.objects.create(kind="brd_regenerate", lane="ai", label="x", status="done",
                               started_at=now - timedelta(seconds=60), finished_at=now)
        self.assertAlmostEqual(services.typical_seconds("brd_regenerate"), 60, delta=1)

    # ---------------------------------------------------------------- page and permissions
    def test_queue_page_api_and_cancel(self):
        job, _ = handlers.queue_brd_regenerate(self.p1, self.admin)
        self.client.force_login(self.user)  # not on the project, didn't ask for it
        self.assertEqual(self.client.get(reverse("jobs:api_job", args=[job.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("jobs:queue")).status_code, 403)  # no "AI queue page" permission
        cap, _ = Capability.objects.get_or_create(code="aiqueue.view", defaults={"label": "AI queue page", "category": "menu"})
        UserCapability.objects.create(user=self.user, capability=cap, scope="all")
        invalidate(self.user)
        self.assertNotContains(self.client.get(reverse("jobs:queue")), job.label)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("jobs:api_job", args=[job.pk])).json()["position"], 1)
        self.assertContains(self.client.get(reverse("jobs:queue")), job.label)
        self.client.post(reverse("jobs:cancel", args=[job.pk]))
        job.refresh_from_db()
        self.assertEqual(job.status, "cancelled")
        self.client.post(reverse("jobs:retry", args=[job.pk]))
        job.refresh_from_db()
        self.assertEqual(job.status, "queued")
