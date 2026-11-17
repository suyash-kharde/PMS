"""The per-person Activity log: record changes (with what changed), downloads, and submissions that saved nothing."""
from datetime import date

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.accounts.testing import skip_password_prompt
from apps.timesheet import services
from apps.timesheet.models import Holiday

from .context import set_current_user
from .models import AuditLog


class ActivityLogTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        self.root = User.objects.create_superuser("root", password="x")
        role = Role.objects.create(name="Member", slug="member", rank=400)
        self.member = User.objects.create_user("member", password="x", role=role)

    def tearDown(self):
        set_current_user(None)

    def _rows(self, user, **kw):
        return list(AuditLog.objects.filter(actor=user, **kw))

    def test_ac01_download_is_recorded_with_file_name_and_filters(self):
        self.client.force_login(self.root)
        r = self.client.get(reverse("timesheet:attendance_export"), {"from": "2026-09-01", "to": "2026-09-30"})
        self.assertEqual(r.status_code, 200)
        (row,) = self._rows(self.root, action="download")
        self.assertEqual(row.object_repr, "attendance_timesheet_2026-09-01_to_2026-09-30.xlsx")
        self.assertIn("from=2026-09-01", row.details)
        self.assertNotIn("/timesheet/", row.details)                    # what was done, not which page
        self.assertEqual(row.summary, "Downloaded “attendance_timesheet_2026-09-01_to_2026-09-30.xlsx”")
        self.assertEqual(row.module, "timesheet")

    def test_ac02_submission_that_saves_a_record_is_not_listed_twice(self):
        self.client.force_login(self.root)
        self.client.post(reverse("timesheet:working_day_add"), {"date": "2026-10-31", "name": "Swap"})
        self.assertTrue(self._rows(self.root, action="create", object_repr__startswith="Working day"))
        self.assertFalse(self._rows(self.root, action="action"))

    def test_ac03_refused_submission_is_recorded_with_the_reason_and_masks_secrets(self):
        self.client.force_login(self.root)
        self.client.post(reverse("timesheet:working_day_add"),
                         {"date": "2026-10-05", "name": "Monday", "api_token": "hunter2", "password": "hunter2"})
        (row,) = self._rows(self.root, action="action")
        self.assertEqual(row.status, "failed")
        self.assertEqual(row.object_repr, "Working day add")
        self.assertIn("already a normal working day", row.details)
        self.assertIn("date=2026-10-05", row.details)
        self.assertIn("api_token=•••", row.details)
        self.assertNotIn("hunter2", row.details)
        self.assertNotIn("/timesheet/", row.details)                    # what was done, not which page
        self.assertEqual(row.summary, "Tried: Working day add")

    def test_ac04_forbidden_attempt_is_recorded_against_the_person(self):
        self.client.force_login(self.member)
        r = self.client.post(reverse("timesheet:working_day_add"), {"date": "2026-10-31", "name": "X"})
        self.assertEqual(r.status_code, 403)
        (row,) = self._rows(self.member, action="action")
        self.assertEqual((row.status, row.module), ("failed", "timesheet"))
        self.assertIn("HTTP 403", row.details)

    def test_ac05_edit_shows_what_changed(self):
        holiday = Holiday.objects.create(date=date(2026, 10, 2), name="Gandhi Jayanti")
        set_current_user(self.root)
        holiday.name = "Gandhi Jayanti (national)"
        holiday.date = date(2026, 10, 3)
        holiday.save()
        (row,) = self._rows(self.root, action="edit", object_repr__startswith="Holiday")
        self.assertIn("Name: Gandhi Jayanti → Gandhi Jayanti (national)", row.details)
        self.assertIn("Date: 2026-10-02 → 2026-10-03", row.details)
        self.assertNotIn("Updated at", row.details)

    def test_ac06_timesheet_bookkeeping_rows_are_not_logged(self):
        set_current_user(self.root)
        services.audit(self.root, "settings", "x", "a", "b")
        self.assertFalse(AuditLog.objects.filter(object_repr__startswith="Timesheet audit").exists())

    def test_ac07_activity_tab_shows_the_new_rows(self):
        self.client.force_login(self.root)
        self.client.get(reverse("timesheet:attendance_export"), {"from": "2026-09-01", "to": "2026-09-30"})
        page = self.client.get(reverse("audit:user_activity", args=[self.root.pk]), {"range": "today"})
        self.assertContains(page, "Downloaded")
        self.assertContains(page, "attendance_timesheet_2026-09-01_to_2026-09-30.xlsx")
