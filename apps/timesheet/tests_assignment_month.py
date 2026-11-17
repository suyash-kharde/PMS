"""A project assigned on any date can be edited for the whole of that month (and only that month)."""
from datetime import date, datetime
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Capability, Role, RoleCapability, User
from apps.accounts.testing import skip_password_prompt
from apps.projects.models import Project, ProjectMembership

from . import rules, services
from .models import ProjectStatusLog

TODAY = date(2026, 10, 20)  # a Tuesday


def aware(d, h=10):
    return timezone.make_aware(datetime(d.year, d.month, d.day, h, 0))


@mock.patch("apps.timesheet.services.today", return_value=TODAY)
class AssignmentMonthEditTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        role = Role.objects.create(name="Member", slug="member", rank=400)
        for tab in ("week", "people", "calendar", "holidays", "attendance", "reports"):
            cap, _ = Capability.objects.get_or_create(code=f"timesheet.tab.{tab}", defaults={"label": tab})
            RoleCapability.objects.get_or_create(role=role, capability=cap, defaults={"scope": "all"})
        self.emp = User.objects.create_user("emp", password="x", role=role)
        User.objects.filter(pk=self.emp.pk).update(date_joined=aware(date(2026, 1, 1)))
        self.emp.refresh_from_db()
        self.proj = Project.objects.create(key="NEW", name="New project", status="active")
        ProjectStatusLog.objects.update(changed_at=aware(date(2026, 1, 1)))
        member = ProjectMembership.objects.create(project=self.proj, user=self.emp, side="internal")
        ProjectMembership.objects.filter(pk=member.pk).update(joined_at=aware(date(2026, 10, 10)))  # assigned 10 Oct

    def _ids(self, day):
        return {p.pk for p in services.PersonContext(self.emp).editable_projects(day)}

    def test_whole_assignment_month_is_editable(self, _today):
        for day in (date(2026, 10, 1), date(2026, 10, 9), date(2026, 10, 10), date(2026, 10, 31)):
            with self.subTest(day=day):
                self.assertIn(self.proj.pk, self._ids(day))

    def test_earlier_months_stay_closed(self, _today):
        self.assertNotIn(self.proj.pk, self._ids(date(2026, 9, 30)))
        self.assertNotIn(self.proj.pk, self._ids(date(2026, 9, 1)))

    def test_later_months_stay_open(self, _today):
        self.assertIn(self.proj.pk, self._ids(date(2026, 11, 2)))

    def test_automatic_split_still_starts_on_the_assignment_date(self, _today):
        ctx = services.PersonContext(self.emp)
        self.assertNotIn(self.proj, ctx.eligible_projects(date(2026, 10, 9)))
        self.assertIn(self.proj, ctx.eligible_projects(date(2026, 10, 10)))

    def test_hours_can_be_saved_before_the_assignment_date_in_that_month(self, _today):
        day = date(2026, 10, 5)  # Monday, before the 10th
        errors, _ = services.save_week(self.emp, self.emp, rules.week_start(day), {(day, self.proj.pk): "4"})
        self.assertEqual(errors, [])

    def test_hours_cannot_be_saved_in_the_month_before_assignment(self, _today):
        day = date(2026, 9, 28)  # Monday in September: still the old rule
        errors, _ = services.save_week(self.emp, self.emp, rules.week_start(day), {(day, self.proj.pk): "4"})
        self.assertTrue(errors)

    def test_week_grid_offers_the_project_on_earlier_days_of_the_month(self, _today):
        monday = rules.week_start(date(2026, 10, 5))
        grid = services.week_grid(self.emp, self.emp, monday)
        by_day = {c["date"]: c for c in grid["days"]}
        self.assertIn(self.proj.pk, by_day[date(2026, 10, 6)]["eligible"])
