"""Timesheet tests. IDs match the test case sheet (docs: dev_guid/timesheet/test_cases.xlsx)."""
from datetime import date, datetime, timedelta
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Capability, Role, RoleCapability, Team, User, UserCapability
from apps.accounts.testing import skip_password_prompt
from apps.projects.models import Project, ProjectMembership

from . import rules, services
from .models import (
    Attendance, Holiday, JobRun, ProjectStatusLog, TeamHolidayLink, TimesheetAudit, TimesheetDay, TimesheetEntry,
    TimesheetMainProject, WorkingDay,
)

TODAY = date(2026, 9, 30)  # a Wednesday; Sep 2026 has 30 days, so Oct 1 locks September


def aware(d, h=10):
    return timezone.make_aware(datetime(d.year, d.month, d.day, h, 0))


class RulesTests(SimpleTestCase):
    def test_af01_split_examples(self):
        cases = {1: [8], 2: [4, 4], 3: [4, 2, 2], 5: [4, 1, 1, 1, 1], 9: [8] + [0] * 8}
        for n, expected in cases.items():
            self.assertEqual(list(rules.split_hours(list(range(1, n + 1))).values()), expected)

    def test_af02_remainder_project_and_zero_projects(self):
        self.assertEqual(rules.split_hours([1, 2, 3], remainder_project_id=3), {1: 2, 2: 2, 3: 4})
        self.assertEqual(rules.split_hours([1, 2, 3], remainder_project_id=99), {1: 4, 2: 2, 3: 2})
        self.assertEqual(rules.split_hours([]), {})

    def test_wk03_holiday_on_weekend_is_one_day(self):
        sat = date(2026, 10, 3)
        self.assertEqual(rules.day_kind(sat, {sat}), rules.HOLIDAY)

    def test_wk04_only_first_saturday_is_a_workday(self):
        self.assertEqual(rules.day_kind(date(2026, 10, 3), {}), rules.WORKDAY)   # 1st Saturday
        self.assertEqual(rules.day_kind(date(2026, 10, 10), {}), rules.WEEKEND)  # 2nd
        self.assertEqual(rules.day_kind(date(2026, 10, 31), {}), rules.WEEKEND)  # 5th
        self.assertEqual(rules.day_kind(date(2026, 10, 4), {}), rules.WEEKEND)   # Sunday
        self.assertEqual(rules.day_kind(date(2026, 9, 5), {}), rules.WORKDAY)    # 1st Saturday of September

    def test_wd01_working_day_opens_a_weekend_date(self):
        sat, sun = date(2026, 10, 31), date(2026, 10, 4)       # a 5th Saturday and a Sunday: normally off
        plain = rules.HolidayDays()
        self.assertEqual(rules.day_kind(sat, plain), rules.WEEKEND)
        opened = rules.HolidayDays()
        opened.working = {sat: object(), sun: object()}
        self.assertEqual(rules.day_kind(sat, opened), rules.WORKDAY)
        self.assertEqual(rules.day_kind(sun, opened), rules.WORKDAY)
        self.assertEqual(rules.day_kind(date(2026, 10, 10), opened), rules.WEEKEND)  # other Saturdays stay off
        both = rules.HolidayDays({sat: object()})               # a holiday on the same date still wins
        both.working = {sat: object()}
        self.assertEqual(rules.day_kind(sat, both), rules.HOLIDAY)
        self.assertEqual(rules.day_kind(date(2026, 10, 5), opened), rules.WORKDAY)   # Monday unchanged

    def test_lk01_month_end_lock_all_lengths(self):
        for last in (date(2026, 2, 28), date(2028, 2, 29), date(2026, 4, 30), date(2026, 1, 31)):
            self.assertFalse(rules.is_locked(last, last))
            self.assertTrue(rules.is_locked(last, last + timedelta(days=1)))

    def test_me01_validation(self):
        self.assertEqual(rules.validate_day_hours(["4", "", "2"]), ([4, 0, 2], None))
        for bad in (["1.5"], ["-1"], ["abc"], ["20", "5"], ["5", "4"]):
            self.assertIsNone(rules.validate_day_hours(bad)[0])


@mock.patch("apps.timesheet.services.today", return_value=TODAY)
class TimesheetTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        JobRun.objects.create(key=services.START_KEY, ran_at=aware(date(2026, 8, 1)))
        self.admin_role = Role.objects.create(name="Administrator", slug="admin", rank=10)
        self.member_role = Role.objects.create(name="Member", slug="member", rank=400)
        # every role keeps all six Timesheet tabs, as the 0010 migration gives existing roles
        for tab in ("week", "people", "calendar", "holidays", "attendance", "reports"):
            cap, _ = Capability.objects.get_or_create(code=f"timesheet.tab.{tab}", defaults={"label": tab})
            for role in (self.admin_role, self.member_role):
                RoleCapability.objects.get_or_create(role=role, capability=cap, defaults={"scope": "all"})
        # the migration gives "Manage holidays & working days" to the Administrator role only
        manage_cap, _ = Capability.objects.get_or_create(
            code="timesheet.holidays.manage", defaults={"label": "Manage holidays", "category": "timesheet"})
        RoleCapability.objects.get_or_create(role=self.admin_role, capability=manage_cap, defaults={"scope": "all"})
        self.admin = User.objects.create_user("admin", password="x", role=self.admin_role)
        self.manager = User.objects.create_user("manager", password="x", role=self.member_role)
        self.emp = User.objects.create_user("emp", password="x", role=self.member_role, manager=self.manager)
        self.other = User.objects.create_user("other", password="x", role=self.member_role)
        self.lead = User.objects.create_user("lead", password="x", role=self.member_role)
        for u in (self.admin, self.manager, self.emp, self.other, self.lead):
            User.objects.filter(pk=u.pk).update(date_joined=aware(date(2026, 1, 1)))
            u.refresh_from_db()
        self.p1 = self._project("P1", owner=self.lead)
        self.p2 = self._project("P2")
        self.p3 = self._project("P3")
        for p in (self.p1, self.p2, self.p3):
            self._assign(self.emp, p)
        ProjectStatusLog.objects.update(changed_at=aware(date(2026, 1, 1)))  # projects created long ago

    def _project(self, key, status="active", owner=None):
        return Project.objects.create(key=key, name=f"Project {key}", status=status, owner=owner)

    def _assign(self, user, project, on=date(2026, 8, 1)):
        m = ProjectMembership.objects.create(project=project, user=user, side="internal")
        ProjectMembership.objects.filter(pk=m.pk).update(joined_at=aware(on))

    def _set_status(self, project, status, on):
        project.status = status
        project.save()
        log = ProjectStatusLog.objects.filter(project=project).latest("pk")
        ProjectStatusLog.objects.filter(pk=log.pk).update(changed_at=aware(on, 15))

    def _present(self, user, day):
        Attendance.objects.create(user=user, date=day, status="present", first_login=aware(day, 11))

    def _hours(self, user, day):
        return {e.project.key: e.hours for e in TimesheetEntry.objects.filter(day__user=user, day__date=day)}

    # ---------------------------------------------------------------- auto-fill
    def test_af03_present_workday_gets_split(self, _today):
        self._present(self.emp, TODAY)
        services.ensure_range(self.emp, [TODAY])
        self.assertEqual(self._hours(self.emp, TODAY), {"P1": 4, "P2": 2, "P3": 2})

    def test_af04_completed_project_excluded(self, _today):
        self.p3.status = "completed"
        self.p3.save()
        self._present(self.emp, TODAY)
        services.ensure_range(self.emp, [TODAY])
        self.assertEqual(self._hours(self.emp, TODAY), {"P1": 4, "P2": 4})

    def test_af05_no_projects_fills_nothing_and_warns(self, _today):
        self._present(self.other, TODAY)
        services.ensure_range(self.other, [TODAY])
        self.assertEqual(self._hours(self.other, TODAY), {})
        self.assertTrue(services.week_grid(self.other, self.other, rules.week_start(TODAY))["no_projects"])

    def test_af06_remainder_project_changed_by_manager(self, _today):
        self._present(self.emp, TODAY)
        services.set_remainder_project(self.manager, self.emp, self.p3)
        self.assertEqual(self._hours(self.emp, TODAY), {"P1": 2, "P2": 2, "P3": 4})
        with self.assertRaises(PermissionDenied):
            services.set_remainder_project(self.emp, self.emp, self.p2)

    def test_pr01_completion_date_logic(self, _today):
        done_on = date(2026, 9, 22)  # Tuesday
        self._set_status(self.p3, "completed", done_on)
        ctx = services.PersonContext(self.emp)
        self.assertIn(self.p3, ctx.eligible_projects(date(2026, 9, 21)))
        self.assertIn(self.p3, ctx.eligible_projects(done_on))  # hours on the completion day stay
        self.assertNotIn(self.p3, ctx.eligible_projects(date(2026, 9, 23)))

    def test_pr02_reactivated_project_counts_from_that_day(self, _today):
        self._set_status(self.p3, "completed", date(2026, 9, 10))
        self._set_status(self.p3, "active", date(2026, 9, 25))
        ctx = services.PersonContext(self.emp)
        self.assertNotIn(self.p3, ctx.eligible_projects(date(2026, 9, 20)))
        self.assertIn(self.p3, ctx.eligible_projects(date(2026, 9, 25)))

    def test_ec04_assignment_mid_month_changes_split_from_that_date(self, _today):
        p4 = self._project("P4")
        self._assign(self.emp, p4, on=date(2026, 9, 29))
        ctx = services.PersonContext(self.emp)
        self.assertEqual(len(ctx.eligible_projects(date(2026, 9, 28))), 3)
        self.assertEqual(len(ctx.eligible_projects(date(2026, 9, 29))), 4)

    # ---------------------------------------------------------------- attendance
    def test_at01_login_marks_present_and_fills(self, _today):
        with mock.patch("django.utils.timezone.now", return_value=aware(TODAY, 11)):
            services.record_login(self.emp)
            services.record_logout(self.emp, aware(TODAY, 18))
        att = Attendance.objects.get(user=self.emp, date=TODAY)
        self.assertEqual(att.status, "present")
        self.assertEqual(timezone.localtime(att.last_logout).hour, 18)

    def test_at02_no_login_workday_is_absent_with_no_hours(self, _today):
        day = date(2026, 9, 28)  # Monday, finished
        services.job_close_day(day)
        self.assertEqual(Attendance.objects.get(user=self.emp, date=day).status, "absent")
        self.assertEqual(self._hours(self.emp, day), {})

    def _state(self, user, day):
        return services.attendance_state(user, day, {}, TODAY, date(2026, 8, 1))

    def test_at04_early_login_present_late_login_absent(self, _today):
        day = date(2026, 9, 28)  # Monday
        services.record_login(self.emp, aware(day, 8))     # before 10:00 -> still Present
        services.record_login(self.other, aware(day, 19))  # only after 18:30 -> Absent
        self.assertEqual(self._state(self.emp, day), "present")
        self.assertEqual(self._state(self.other, day), "absent")

    def test_at08_recheck_turns_old_early_login_absences_present(self, _today):
        day = date(2026, 9, 28)
        Attendance.objects.create(user=self.emp, date=day, status="absent", first_login=aware(day, 9))  # old rule
        Attendance.objects.create(user=self.other, date=day, status="absent", overridden=True, first_login=aware(day, 9))
        self.assertEqual(services.job_recheck_early_logins(), 1)
        self.assertEqual(Attendance.objects.get(user=self.emp, date=day).status, "present")
        self.assertEqual(sum(self._hours(self.emp, day).values()), 8)                       # hours filled
        self.assertEqual(Attendance.objects.get(user=self.other, date=day).status, "absent")  # override kept

    def test_at05_use_inside_window_counts_as_present(self, _today):
        from apps.accounts.models import PageVisit

        day = date(2026, 9, 28)
        services.record_login(self.emp, aware(day, 9))  # logged in early, then worked after 10
        PageVisit.objects.create(user=self.emp, path="/timesheet/", visited_at=aware(day, 10) + timedelta(minutes=20))
        self.assertEqual(self._state(self.emp, day), "present")

    def test_at06_today_pending_until_1830_then_absent_login_makes_present(self, _today):
        with mock.patch("apps.timesheet.services.now_local", return_value=aware(TODAY, 15)):
            self.assertEqual(self._state(self.emp, TODAY), "absent")  # not logged in yet = Absent
        with mock.patch("apps.timesheet.services.now_local", return_value=aware(TODAY, 19)):
            services.run_scheduled_jobs(now=aware(TODAY, 19))
            self.assertEqual(Attendance.objects.get(user=self.emp, date=TODAY).status, "absent")
        services.record_login(self.other, aware(TODAY, 12))  # a login inside the window = Present at once
        self.assertEqual(Attendance.objects.get(user=self.other, date=TODAY).status, "present")

    def test_at07_first_visit_of_day_saved_without_new_sign_in(self, _today):
        self.client.force_login(self.other)
        Attendance.objects.filter(user=self.other).delete()  # as if signed in on an earlier day
        start, _end = services.work_window(timezone.localdate())
        with mock.patch("django.utils.timezone.now", return_value=start + timedelta(hours=1)):
            self.client.get(reverse("timesheet:holidays"))
            self.client.get(reverse("timesheet:holidays"))  # second visit: first time is kept
        att = Attendance.objects.get(user=self.other, date=timezone.localdate())
        self.assertEqual(att.status, "present")
        self.assertEqual(timezone.localtime(att.first_login).hour, timezone.localtime(start).hour + 1)

    def test_at03_override_by_manager_and_not_by_self(self, _today):
        day = date(2026, 9, 28)
        services.job_close_day(day)
        with self.assertRaises(PermissionDenied):
            services.override_attendance(self.emp, self.emp, day, "present")
        services.override_attendance(self.manager, self.emp, day, "present", "client visit")
        self.assertEqual(self._hours(self.emp, day), {"P1": 4, "P2": 2, "P3": 2})
        self.assertTrue(TimesheetAudit.objects.filter(entity="attendance", target_user=self.emp).exists())

    # ---------------------------------------------------------------- weekends / holidays
    def test_wk01_off_day_gets_no_hours_and_no_attendance(self, _today):
        sat = date(2026, 9, 26)  # 4th Saturday: off
        services.job_close_day(sat)
        self.assertEqual(self._hours(self.emp, sat), {})
        self.assertFalse(TimesheetDay.objects.filter(user=self.emp, date=sat).exists())
        self.assertFalse(Attendance.objects.filter(user=self.emp, date=sat).exists())

    def test_wk06_weekend_off_days_are_read_only(self, _today):
        """Sundays and non-1st Saturdays are read-only for employees; admins (Administrator / Devadmin) can
        still enter hours on them. The 1st Saturday is a workday for everyone."""
        monday = rules.week_start(TODAY)
        first_sat, sunday = date(2026, 10, 3), date(2026, 10, 4)
        fourth_sat = date(2026, 9, 26)
        for day in (sunday, fourth_sat):
            errors, _ = services.save_week(self.emp, self.emp, rules.week_start(day), {(day, self.p1.pk): "4"})
            self.assertTrue(errors, f"emp saved hours on {day}")
            self.assertEqual(self._hours(self.emp, day), {})
        for day in (sunday, fourth_sat):
            errors, _ = services.save_week(self.admin, self.emp, rules.week_start(day), {(day, self.p1.pk): "4"})
            self.assertEqual(errors, [], f"admin could not save hours on {day}")
            self.assertEqual(self._hours(self.emp, day).get("P1"), 4)
        errors, _ = services.save_week(self.emp, self.emp, monday, {(first_sat, self.p1.pk): "4"})
        self.assertEqual(errors, [])
        self.assertEqual(self._hours(self.emp, first_sat).get("P1"), 4)
        grid = services.week_grid(self.admin, self.emp, monday)
        editable = {c["date"]: c["editable"] for c in grid["days"]}
        self.assertTrue(editable[first_sat])
        self.assertTrue(editable[sunday])
        editable = {c["date"]: c["editable"] for c in services.week_grid(self.emp, self.emp, monday)["days"]}
        self.assertTrue(editable[first_sat])
        self.assertFalse(editable[sunday])

    def test_wk05_first_saturday_is_filled_like_a_workday(self, _today):
        sat = date(2026, 9, 5)
        self._present(self.emp, sat)
        services.job_close_day(sat)
        self.assertEqual(self._hours(self.emp, sat), {"P1": 4, "P2": 2, "P3": 2})
        services.job_close_day(sat)  # someone else did not log in that Saturday
        self.assertEqual(Attendance.objects.get(user=self.other, date=sat).status, "absent")

    def test_hd01_holiday_replaces_absent_and_delete_reverts(self, _today):
        day = date(2026, 9, 28)
        services.job_close_day(day)  # absent, no hours
        holiday = services.save_holiday(self.admin, day, "Test holiday")
        state = lambda: services.attendance_state(self.emp, day, services.holiday_map(day, day, self.emp),  # noqa: E731
                                                  TODAY, services.go_live_date())
        self.assertEqual((self._hours(self.emp, day), state()), ({}, "holiday"))  # off: no hours, not absent
        services.delete_holiday(self.admin, holiday)
        self.assertEqual((self._hours(self.emp, day), state()), ({}, "absent"))
        self.assertEqual(TimesheetAudit.objects.filter(entity="holiday").count(), 2)

    def test_hd04_holidays_and_weekends_get_no_hours(self, _today):
        JobRun.objects.filter(key=services.START_KEY).update(ran_at=aware(date(2026, 9, 29)))  # go-live 29 Sep
        Holiday.objects.create(date=date(2026, 9, 25), name="Ganpati Visarjan")  # Fri, before go-live
        Holiday.objects.create(date=date(2026, 10, 2), name="Gandhi Jayanti")    # Fri, upcoming
        days = [date(2026, 9, 24), date(2026, 9, 25), date(2026, 9, 26), date(2026, 9, 27), date(2026, 10, 2)]
        services.ensure_range(self.emp, days)
        for day in days:  # holidays, weekend, and a workday before go-live: nothing filled
            self.assertEqual(self._hours(self.emp, day), {}, day)
        # holidays are read-only even for an admin; weekend off days for everyone but an admin (test_wk06)
        monday = rules.week_start(date(2026, 9, 26))
        for actor, day in ((self.emp, date(2026, 9, 25)), (self.admin, date(2026, 9, 25)),  # holiday
                           (self.emp, date(2026, 9, 27))):                                   # Sunday
            errors, _ = services.save_week(actor, self.emp, monday, {(day, self.p1.pk): "3"})
            self.assertTrue(errors, f"{actor} saved hours on {day}")
            self.assertEqual(self._hours(self.emp, day), {})
        grid = services.week_grid(self.admin, self.emp, rules.week_start(date(2026, 10, 2)))
        self.assertFalse({c["date"]: c["editable"] for c in grid["days"]}[date(2026, 10, 2)])

    def test_hd02_duplicate_holiday_rejected(self, _today):
        services.save_holiday(self.admin, date(2026, 10, 2), "Gandhi Jayanti")
        with self.assertRaises(ValueError):
            services.save_holiday(self.admin, date(2026, 10, 2), "Again")

    def test_hd03_employee_gets_403_on_holiday_changes(self, _today):
        self.client.force_login(self.emp)
        r = self.client.post(reverse("timesheet:holidays"), {"date": "2026-10-02", "name": "X"})
        self.assertEqual(r.status_code, 403)
        h = Holiday.objects.create(date=date(2026, 10, 3), name="Y")
        self.assertEqual(self.client.post(reverse("timesheet:holiday_delete", args=[h.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("timesheet:holiday_edit", args=[h.pk]),
                                          {"date": "2026-10-03", "name": "Z"}).status_code, 403)
        self.client.force_login(self.manager)  # a reporting manager no longer manages holidays by default
        r = self.client.post(reverse("timesheet:holidays"), {"date": "2026-10-02", "name": "Gandhi Jayanti"})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Holiday.objects.exists() and Holiday.objects.filter(date=date(2026, 10, 2)).exists())
        self.client.force_login(self.admin)
        r = self.client.post(reverse("timesheet:holidays"), {"date": "2026-10-02", "name": "Gandhi Jayanti"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Holiday.objects.filter(date=date(2026, 10, 2)).exists())

    def test_wd12_who_can_manage_holidays_and_working_days_is_a_permission(self, _today):
        """Set per role on the Roles page, or per person; the page shows the forms only to those who hold it."""
        from apps.accounts.models import UserCapability
        from apps.accounts.permissions import invalidate

        cap = Capability.objects.get(code="timesheet.holidays.manage")
        add = reverse("timesheet:working_day_add")
        data = {"date": "2026-10-31", "name": "Swap"}
        self.client.force_login(self.manager)
        self.assertEqual(self.client.post(add, data).status_code, 403)
        self.assertNotContains(self.client.get(reverse("timesheet:holidays")), "wd-add")
        UserCapability.objects.create(user=self.manager, capability=cap, scope="all")   # a personal grant
        invalidate(self.manager)
        self.assertContains(self.client.get(reverse("timesheet:holidays")), "wd-add")
        self.assertEqual(self.client.post(add, data).status_code, 302)
        self.assertEqual(WorkingDay.objects.count(), 1)
        UserCapability.objects.filter(user=self.manager).delete()
        invalidate(self.manager)
        self.assertEqual(self.client.post(reverse("timesheet:working_day_delete", args=[WorkingDay.objects.get().pk])
                                          ).status_code, 403)
        RoleCapability.objects.create(role=self.member_role, capability=cap, scope="all")  # a role grant
        invalidate()
        self.assertEqual(self.client.post(reverse("timesheet:working_day_delete", args=[WorkingDay.objects.get().pk])
                                          ).status_code, 302)
        self.assertFalse(WorkingDay.objects.exists())

    # ---------------------------------------------------------------- holidays per team
    def _teams(self):
        """emp in Pune, other in Delhi, lead in UI/UX (follows Pune); all three on project P1."""
        pune = Team.objects.create(name="pune team")
        delhi = Team.objects.create(name="delhi team")
        uiux = Team.objects.create(name="UI/UX Designer Team")
        TeamHolidayLink.objects.create(team=uiux, follows=pune)
        for user, team in ((self.emp, pune), (self.other, delhi), (self.lead, uiux)):
            User.objects.filter(pk=user.pk).update(team=team)
            user.refresh_from_db()
        self._assign(self.other, self.p1)
        self._assign(self.lead, self.p1)
        return pune, delhi, uiux

    def test_hd05_team_holiday_only_for_that_team_and_followers(self, _today):
        pune, _delhi, _uiux = self._teams()
        day = date(2026, 9, 28)  # Monday, nobody logged in
        services.save_holiday(self.admin, day, "Ganesh Chaturthi", teams=[pune])
        services.job_mark_absent(day)
        services.job_close_day(day)
        self.assertEqual(Attendance.objects.get(user=self.other, date=day).status, "absent")  # Delhi: workday
        self.assertFalse(Attendance.objects.filter(user=self.emp, date=day).exists())         # Pune: holiday
        self.assertFalse(Attendance.objects.filter(user=self.lead, date=day).exists())        # UI/UX follows Pune
        other_day = date(2026, 9, 29)
        services.save_holiday(self.admin, other_day, "Pune only", teams=[pune])
        for user in (self.emp, self.other, self.lead):
            self._present(user, other_day)
        services.job_close_day(other_day)
        self.assertEqual(self._hours(self.emp, other_day), {})         # Pune: holiday, no hours
        self.assertEqual(self._hours(self.lead, other_day), {})
        self.assertEqual(self._hours(self.other, other_day), {"P1": 8})  # Delhi: normal day
        self.assertEqual(services.holiday_map(day, day, self.emp)[day].label, "Ganesh Chaturthi (pune team)")
        self.assertEqual(services.holiday_map(day, day, self.other), {})

    def test_hd06_same_date_per_team_allowed_overlap_refused(self, _today):
        pune, delhi, _uiux = self._teams()
        day = date(2026, 10, 20)
        services.save_holiday(self.admin, day, "Dussehra (Pune)", teams=[pune])
        services.save_holiday(self.admin, day, "Dussehra (Delhi)", teams=[delhi])
        self.assertEqual(Holiday.objects.filter(date=day).count(), 2)
        with self.assertRaises(ValueError):
            services.save_holiday(self.admin, day, "Again", teams=[pune])  # Pune already has one
        with self.assertRaises(ValueError):
            services.save_holiday(self.admin, day, "Everyone", teams=[])   # clashes with both
        self.assertEqual(services.holiday_map(day, day, self.emp)[day].name, "Dussehra (Pune)")
        self.assertEqual(services.holiday_map(day, day, self.other)[day].name, "Dussehra (Delhi)")

    def test_hd07_everyone_holiday_still_applies_to_all_teams(self, _today):
        self._teams()
        day = date(2026, 10, 2)
        services.save_holiday(self.admin, day, "Gandhi Jayanti")
        for user in (self.emp, self.other, self.lead, self.admin):
            self.assertIn(day, services.holiday_map(day, day, user))

    def test_hd08_people_view_marks_each_person_and_counts_their_workdays(self, _today):
        pune, _delhi, _uiux = self._teams()
        day = date(2026, 9, 28)
        services.save_holiday(self.admin, day, "Ganesh Chaturthi", teams=[pune])
        data = services.people_week([self.emp, self.other], rules.week_start(day))
        col = data["days"][0]
        self.assertEqual(col["kind"], rules.WORKDAY)  # not a holiday for everyone shown
        self.assertEqual(col["holiday_label"], "Ganesh Chaturthi (pune team)")
        rows = {r["user"].pk: r for r in data["rows"]}
        self.assertEqual(rows[self.emp.pk]["cells"][0]["kind"], rules.HOLIDAY)
        self.assertEqual(rows[self.other.pk]["cells"][0]["kind"], rules.WORKDAY)
        # Mon–Fri plus Sat 3 Oct (the month's first Saturday) = 6 workdays; Pune loses Monday
        self.assertEqual((rows[self.emp.pk]["workdays"], rows[self.other.pk]["workdays"]), (5, 6))
        only_pune = services.people_week([self.emp], rules.week_start(day))
        self.assertEqual(only_pune["days"][0]["kind"], rules.HOLIDAY)

    def test_hd09_sub_team_gets_parent_team_holiday(self, _today):
        pune, _delhi, _uiux = self._teams()
        sub = Team.objects.create(name="pune backend", parent=pune)
        User.objects.filter(pk=self.other.pk).update(team=sub)
        self.other.refresh_from_db()
        day = date(2026, 9, 28)
        services.save_holiday(self.admin, day, "Ganesh Chaturthi", teams=[pune])
        self.assertIn(day, services.holiday_map(day, day, self.other))

    def test_hd10_holidays_page_add_with_teams_and_follow_link(self, _today):
        pune, delhi, uiux = self._teams()
        self.client.force_login(self.admin)
        r = self.client.post(reverse("timesheet:holidays"),
                             {"date": "2026-10-20", "name": "Dussehra", "teams": [str(pune.pk)]})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(list(Holiday.objects.get(name="Dussehra").teams.all()), [pune])
        page = self.client.get(reverse("timesheet:holidays"))
        self.assertContains(page, "Dussehra")
        self.assertNotContains(page, "follows another team")  # the follow-links section was taken off the page
        self.assertEqual(self.client.get(reverse("timesheet:holidays"), {"team": delhi.pk}).status_code, 200)
        # UI/UX switched to follow Delhi, then back to its own holidays
        self.client.post(reverse("timesheet:holiday_link"), {"team": uiux.pk, "follows": delhi.pk})
        self.assertEqual(TeamHolidayLink.objects.get(team=uiux).follows, delhi)
        self.client.post(reverse("timesheet:holiday_link"), {"team": uiux.pk})
        self.assertFalse(TeamHolidayLink.objects.filter(team=uiux).exists())
        with self.assertRaises(ValueError):  # no loops
            services.set_team_holiday_link(self.admin, pune, uiux)
            services.set_team_holiday_link(self.admin, uiux, pune)
        self.client.force_login(self.emp)
        self.assertEqual(self.client.post(reverse("timesheet:holiday_link"),
                                          {"team": uiux.pk, "follows": pune.pk}).status_code, 403)

    # ---------------------------------------------------------------- main projects (star)
    def _nine_projects(self):
        """emp gets P4..P9 on top of P1..P3: nine open projects."""
        extra = [self._project(f"P{i}") for i in range(4, 10)]
        for p in extra:
            self._assign(self.emp, p)
        ProjectStatusLog.objects.update(changed_at=aware(date(2026, 1, 1)))
        return [self.p1, self.p2, self.p3] + extra

    def test_mp01_starred_projects_share_the_8_hours(self, _today):
        projects = self._nine_projects()
        self._present(self.emp, TODAY)
        services.ensure_range(self.emp, [TODAY])
        self.assertEqual(self._hours(self.emp, TODAY), {"P1": 8, **{f"P{i}": 0 for i in range(2, 10)}})  # no stars
        grid = services.week_grid(self.emp, self.emp, rules.week_start(TODAY))
        self.assertTrue(grid["too_many"])
        for p in projects[5:]:  # P6..P9
            services.set_main_project(self.emp, self.emp, p, True)
        self.assertEqual(self._hours(self.emp, TODAY), {"P6": 2, "P7": 2, "P8": 2, "P9": 2})
        grid = services.week_grid(self.emp, self.emp, rules.week_start(TODAY))
        self.assertEqual([r["project"].key for r in grid["rows"]][:4], ["P6", "P7", "P8", "P9"])  # starred first
        self.assertEqual((grid["main_count"], grid["too_many"]), (4, False))
        self.assertEqual(TimesheetAudit.objects.filter(entity="settings", target_user=self.emp).count(), 4)

    def test_mp02_at_most_8_main_projects(self, _today):
        projects = self._nine_projects()
        for p in projects[:8]:
            services.set_main_project(self.emp, self.emp, p, True)
        with self.assertRaises(ValueError):
            services.set_main_project(self.emp, self.emp, projects[8], True)
        self.client.force_login(self.emp)
        r = self.client.post(reverse("timesheet:main_project", args=[self.emp.pk]), {"star": f"pin:{projects[8].pk}"})
        self.assertEqual(r.status_code, 302)  # refused with a message, not an error page
        self.assertEqual(TimesheetMainProject.objects.filter(user=self.emp).count(), 8)
        with self.assertRaises(ValueError):  # a project the person isn't on
            services.set_main_project(self.emp, self.emp, self._project("PX"), True)

    def test_mp03_completed_main_project_drops_out_then_falls_back(self, _today):
        self._present(self.emp, TODAY)
        services.set_main_project(self.emp, self.emp, self.p2, True)
        services.set_main_project(self.emp, self.emp, self.p3, True)
        self.assertEqual(self._hours(self.emp, TODAY), {"P2": 4, "P3": 4})
        self._set_status(self.p3, "completed", date(2026, 9, 29))
        services.ensure_range(self.emp, [TODAY])
        self.assertEqual(self._hours(self.emp, TODAY), {"P2": 8})
        self._set_status(self.p2, "completed", date(2026, 9, 29))
        services.ensure_range(self.emp, [TODAY])
        self.assertEqual(self._hours(self.emp, TODAY), {"P1": 8})  # no main project left open: all eligible

    def test_mp04_who_can_star_and_unstar(self, _today):
        url = reverse("timesheet:main_project", args=[self.emp.pk])
        self.client.force_login(self.other)  # not their manager
        self.assertEqual(self.client.post(url, {"star": f"pin:{self.p1.pk}"}).status_code, 403)
        self.client.force_login(self.manager)
        self.client.post(url, {"star": f"pin:{self.p1.pk}"})
        self.assertTrue(TimesheetMainProject.objects.filter(user=self.emp, project=self.p1).exists())
        self.client.force_login(self.emp)
        page = self.client.get(reverse("timesheet:week"))
        self.assertContains(page, "Main projects:")
        self.assertContains(page, f'value="unpin:{self.p1.pk}"')
        self.client.post(url, {"star": f"unpin:{self.p1.pk}"})
        self.assertFalse(TimesheetMainProject.objects.filter(user=self.emp).exists())

    def test_mp05_finished_days_keep_their_split(self, _today):
        day = TODAY - timedelta(days=1)
        self._present(self.emp, day)
        services.ensure_range(self.emp, [day])
        services.set_main_project(self.emp, self.emp, self.p3, True)
        services.ensure_range(self.emp, [day])
        self.assertEqual(self._hours(self.emp, day), {"P1": 4, "P2": 2, "P3": 2})

    # ---------------------------------------------------------------- leave
    def test_lv01_full_day_leave_on_an_automatic_day(self, _today):
        self._present(self.emp, TODAY)
        services.ensure_range(self.emp, [TODAY])
        monday = rules.week_start(TODAY)
        shown = {(TODAY, p.pk): str(h) for p, h in ((self.p1, 4), (self.p2, 2), (self.p3, 2))}  # page not updated
        errors, _ = services.save_week(self.emp, self.emp, monday, shown, leave_raw={TODAY: "8"})
        self.assertEqual(errors, [])
        ts = TimesheetDay.objects.get(user=self.emp, date=TODAY)
        self.assertEqual((ts.leave_hours, ts.edited, self._hours(self.emp, TODAY)), (8, False, {}))
        self.assertEqual(services.attendance_state(self.emp, TODAY, {}, TODAY, services.go_live_date()), "leave")
        grid = services.week_grid(self.emp, self.emp, monday)
        self.assertEqual((grid["leave_total"], [d["total"] for d in grid["days"] if d["date"] == TODAY]), (8, [8]))

    def test_lv02_half_day_leave_resplits_the_rest(self, _today):
        self._present(self.emp, TODAY)
        services.ensure_range(self.emp, [TODAY])
        preview = {(TODAY, self.p1.pk): "2", (TODAY, self.p2.pk): "1", (TODAY, self.p3.pk): "1"}  # the page's preview
        errors, _ = services.save_week(self.emp, self.emp, rules.week_start(TODAY), preview, leave_raw={TODAY: "4"})
        self.assertEqual(errors, [])
        self.assertEqual(self._hours(self.emp, TODAY), {"P1": 2, "P2": 1, "P3": 1})
        self.assertFalse(TimesheetDay.objects.get(user=self.emp, date=TODAY).edited)  # still automatic
        self.assertTrue(TimesheetAudit.objects.filter(ref__endswith="Leave", new_value="4").exists())

    def test_lv03_leave_with_own_hours_counts_toward_8(self, _today):
        self._present(self.emp, TODAY)
        monday = rules.week_start(TODAY)
        errors, _ = services.save_week(self.emp, self.emp, monday, {(TODAY, self.p1.pk): "6"}, leave_raw={TODAY: "4"})
        self.assertTrue(errors)  # 6 + other projects + 4 leave > 8
        errors, _ = services.save_week(self.emp, self.emp, monday,
                                       {(TODAY, self.p1.pk): "4", (TODAY, self.p2.pk): "0", (TODAY, self.p3.pk): "0"},
                                       leave_raw={TODAY: "4"})
        self.assertEqual(errors, [])
        ts = TimesheetDay.objects.get(user=self.emp, date=TODAY)
        self.assertEqual((ts.leave_hours, ts.edited, self._hours(self.emp, TODAY)), (4, True, {"P1": 4}))

    def test_lv04_future_leave_and_off_day_refused(self, _today):
        monday = rules.week_start(TODAY)
        thursday = TODAY + timedelta(days=1)
        errors, _ = services.save_week(self.emp, self.emp, monday, {}, leave_raw={thursday: "8"})
        self.assertEqual(errors, [])
        self.assertEqual(TimesheetDay.objects.get(user=self.emp, date=thursday).leave_hours, 8)
        errors, _ = services.save_week(self.emp, self.emp, monday, {}, leave_raw={date(2026, 10, 4): "8"})  # Sunday
        self.assertTrue(errors)
        self.client.force_login(self.other)  # not their manager
        self.client.post(reverse("timesheet:week") + f"?user={self.emp.pk}", {"week": monday, "l_" + str(thursday): "0"})
        self.assertEqual(TimesheetDay.objects.get(user=self.emp, date=thursday).leave_hours, 8)

    def test_lv05_absent_day_turned_into_leave(self, _today):
        day = date(2026, 9, 28)
        services.job_mark_absent(day)
        self.assertEqual(Attendance.objects.get(user=self.emp, date=day).status, "absent")
        self.client.force_login(self.emp)
        r = self.client.post(reverse("timesheet:week"), {"week": rules.week_start(day), "l_2026-09-28": "8"})
        self.assertEqual(r.status_code, 302)
        row = services.people_week([self.emp], rules.week_start(day))["rows"][0]
        monday = row["cells"][0]  # the leave day; other days of the week depend on the real clock
        self.assertEqual((monday["absent"], monday["leave"], row["leave_total"]), (False, 8, 8))
        report = services.weekly_report(self.admin, rules.week_start(day), user_filter=self.emp.pk)
        self.assertNotIn(day, report["summary"][0]["absent"])
        self.assertEqual(report["summary"][0]["leave_total"], 8)
        page = self.client.get(reverse("timesheet:week") + "?week=2026-09-28")
        self.assertContains(page, "ts-leave-row")
        self.assertContains(page, 'name="l_2026-09-28"')

    def test_lv06_clear_off_day_autofill(self, _today):
        sat, sun = date(2026, 9, 26), date(2026, 9, 27)
        for day, edited in ((sat, False), (sun, True)):
            ts = TimesheetDay.objects.create(user=self.emp, date=day, auto_filled=True, edited=edited)
            TimesheetEntry.objects.create(day=ts, project=self.p1, hours=8)
        workday = TimesheetDay.objects.create(user=self.emp, date=date(2026, 9, 29), auto_filled=True)
        self.assertEqual(services.clear_off_day_autofill(dry_run=True), [("emp", sat)])
        self.assertEqual(services.clear_off_day_autofill(), [("emp", sat)])
        self.assertFalse(TimesheetDay.objects.filter(user=self.emp, date=sat).exists())
        self.assertTrue(TimesheetDay.objects.filter(user=self.emp, date=sun).exists())  # edited: kept
        self.assertTrue(TimesheetDay.objects.filter(pk=workday.pk).exists())

    # ---------------------------------------------------------------- manual edit / lock
    def test_me02_edit_keeps_value_and_blocks_invalid(self, _today):
        self._present(self.emp, TODAY)
        monday = rules.week_start(TODAY)
        errors, _ = services.save_week(self.emp, self.emp, monday, {(TODAY, self.p1.pk): "6", (TODAY, self.p2.pk): "2",
                                                                    (TODAY, self.p3.pk): "0"})
        self.assertEqual(errors, [])
        hours = self._hours(self.emp, TODAY)
        self.assertEqual((hours["P1"], hours["P2"], hours.get("P3", 0)), (6, 2, 0))
        services.ensure_range(self.emp, [TODAY])  # auto-fill never overwrites an edited day
        self.assertEqual(self._hours(self.emp, TODAY)["P1"], 6)
        errors, _ = services.save_week(self.emp, self.emp, monday, {(TODAY, self.p1.pk): "25"})
        self.assertTrue(errors)
        errors, _ = services.save_week(self.emp, self.emp, monday, {(TODAY, self.p1.pk): "2.5"})
        self.assertTrue(errors)

    def test_me05_day_total_capped_at_8_across_projects(self, _today):
        self._present(self.emp, TODAY)
        monday = rules.week_start(TODAY)
        services.ensure_range(self.emp, [TODAY])  # 4 / 2 / 2
        errors, _ = services.save_week(self.emp, self.emp, monday, {(TODAY, self.p1.pk): "8"})  # 8 + 2 + 2 = 12
        self.assertTrue(errors)
        self.assertEqual(self._hours(self.emp, TODAY), {"P1": 4, "P2": 2, "P3": 2})  # nothing saved
        errors, _ = services.save_week(self.emp, self.emp, monday, {(TODAY, self.p1.pk): "8", (TODAY, self.p2.pk): "0",
                                                                    (TODAY, self.p3.pk): "0"})
        self.assertEqual(errors, [])
        errors, _ = services.save_week(self.admin, self.emp, monday, {(TODAY, self.p2.pk): "1"})  # admins too
        self.assertTrue(errors)

    def test_me03_completed_project_cannot_take_hours(self, _today):
        self._set_status(self.p3, "completed", date(2026, 9, 20))
        errors, _ = services.save_week(self.emp, self.emp, rules.week_start(TODAY), {(TODAY, self.p3.pk): "3"})
        self.assertTrue(errors)

    def test_me04_draft_then_submit(self, _today):
        self._present(self.emp, TODAY)
        monday = rules.week_start(TODAY)
        services.save_week(self.emp, self.emp, monday, {(TODAY, self.p1.pk): "5"})
        self.assertEqual(TimesheetDay.objects.get(user=self.emp, date=TODAY).status, "draft")
        services.save_week(self.emp, self.emp, monday, {}, submit=True)
        self.assertEqual(TimesheetDay.objects.get(user=self.emp, date=TODAY).status, "submitted")

    def test_lk02_locked_month_roles(self, _today):
        last_month = date(2026, 8, 31)  # Monday
        self._present(self.emp, last_month)
        monday = rules.week_start(last_month)
        raw = {(last_month, self.p1.pk): "3"}
        self.assertTrue(services.save_week(self.emp, self.emp, monday, raw)[0])       # employee: locked
        self.assertTrue(services.save_week(self.lead, self.emp, monday, raw)[0])      # project lead: no
        self.assertTrue(services.save_week(self.manager, self.emp, monday, raw)[0])       # RM: only with the permission
        self.assertEqual(services.save_week(self.admin, self.emp, monday, {(last_month, self.p1.pk): "2"})[0], [])
        self.assertTrue(TimesheetAudit.objects.filter(entity="hours", after_lock=True).count() >= 1)

    def test_lk03_admin_unlock_lets_employee_edit(self, _today):
        last_month = date(2026, 8, 31)
        services.set_unlock(self.admin, self.emp, date(2026, 8, 1), True)
        self._present(self.emp, last_month)
        errors, _ = services.save_week(self.emp, self.emp, rules.week_start(last_month), {(last_month, self.p1.pk): "8"})
        self.assertEqual(errors, [])
        self.assertTrue(TimesheetAudit.objects.filter(entity="unlock").exists())

    def test_ec02_joining_date_limits_hours(self, _today):
        services.set_employment_dates(self.admin, self.emp, date(2026, 9, 29), None)
        services.job_close_day(date(2026, 9, 26))  # Saturday before joining
        self.assertEqual(self._hours(self.emp, date(2026, 9, 26)), {})

    def test_ec03_last_working_day_limits_hours(self, _today):
        services.set_employment_dates(self.admin, self.emp, None, date(2026, 9, 25))
        services.job_close_day(date(2026, 9, 26))
        self.assertEqual(self._hours(self.emp, date(2026, 9, 26)), {})

    def test_ec06_concurrent_edit_warns_last_save_wins(self, _today):
        self._present(self.emp, TODAY)
        monday = rules.week_start(TODAY)
        opened = timezone.now() - timedelta(minutes=5)
        services.save_week(self.manager, self.emp, monday, {(TODAY, self.p1.pk): "3"})
        _errors, warnings = services.save_week(self.emp, self.emp, monday, {(TODAY, self.p1.pk): "4"}, loaded_at=opened)
        self.assertTrue(warnings)
        self.assertEqual(self._hours(self.emp, TODAY)["P1"], 4)

    # ---------------------------------------------------------------- reports / roles
    def test_rp01_week_spanning_two_months(self, _today):
        monday = date(2026, 9, 28)  # Mon 28 Sep – Sun 4 Oct
        for d in (date(2026, 9, 29), date(2026, 9, 30)):
            self._present(self.emp, d)
        services.ensure_range(self.emp, [date(2026, 9, 29), date(2026, 9, 30)], force=True)
        data = services.weekly_report(self.admin, monday)
        self.assertTrue(data["spans_months"])
        self.assertEqual([m["total"] for m in data["months"]], [16, 0])

    def test_rp02_lead_sees_only_own_projects(self, _today):
        self._present(self.emp, TODAY)
        services.ensure_range(self.emp, [TODAY])
        data = services.weekly_report(self.lead, rules.week_start(TODAY))
        self.assertEqual({r["project"].key for r in data["rows"]}, {"P1"})

    def test_rp03_exports_match_and_download(self, _today):
        self._present(self.emp, TODAY)
        services.ensure_range(self.emp, [TODAY])
        self.client.force_login(self.admin)
        url = reverse("timesheet:reports") + f"?week={rules.week_start(TODAY):%Y-%m-%d}"
        x = self.client.get(url + "&format=xlsx")                 # the weekly Excel download was removed too
        self.assertEqual(x.status_code, 200)
        self.assertNotIn("spreadsheet", x["Content-Type"])
        self.assertNotContains(x, "format=xlsx")
        p = self.client.get(url + "&format=pdf")                  # the PDF download was removed: just the page
        self.assertEqual(p.status_code, 200)
        self.assertNotIn(b"%PDF", p.content[:8])
        self.assertNotContains(p, "format=pdf")

    def test_rp04_weekly_job_notifies_managers_once(self, _today):
        from apps.notify.models import Notification

        monday_now = aware(date(2026, 10, 5), 9)
        with mock.patch("apps.timesheet.services.today", return_value=date(2026, 10, 5)):
            services.run_scheduled_jobs(now=monday_now)
            services.run_scheduled_jobs(now=monday_now)
        self.assertEqual(Notification.objects.filter(user=self.manager, title="Weekly timesheet report").count(), 1)

    def test_ro01_viewing_rights(self, _today):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("timesheet:week") + f"?user={self.emp.pk}").status_code, 403)
        self.assertEqual(self.client.get(reverse("timesheet:attendance")).status_code, 403)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("timesheet:week") + f"?user={self.emp.pk}").status_code, 200)

    def test_ui01_every_page_renders(self, _today):
        for who in (self.admin, self.manager, self.emp):
            self.client.force_login(who)
            for name in ("week", "calendar", "holidays", "reports", "people"):
                self.assertEqual(self.client.get(reverse(f"timesheet:{name}")).status_code, 200, (who, name))
        self.client.force_login(self.admin)
        for name in ("attendance",):
            self.assertEqual(self.client.get(reverse(f"timesheet:{name}")).status_code, 200, name)

    def test_au01_project_status_change_audited(self, _today):
        self.p2.status = "under_amc"
        self.p2.save()
        a = TimesheetAudit.objects.get(entity="project_status")
        self.assertEqual((a.old_value, a.new_value), ("active", "under_amc"))

    def test_pp01_people_view_one_row_per_person(self, _today):
        self._present(self.emp, TODAY)
        services.ensure_range(self.emp, [TODAY])
        data = services.people_week([self.emp, self.other], rules.week_start(TODAY))
        self.assertEqual([r["user"] for r in data["rows"]], [self.emp, self.other])
        self.assertEqual(data["rows"][0]["total"], 8)
        self.assertEqual(data["rows"][1]["total"], 0)
        self.client.force_login(self.manager)
        page = self.client.get(reverse("timesheet:people")).content.decode()
        self.assertIn("emp", page)
        self.assertNotIn(">other<", page)  # not in the manager's team

    def test_pp03_present_and_working_today_lists(self, _today):
        self._present(self.emp, TODAY)
        services.ensure_range(self.emp, [TODAY])
        self.client.force_login(self.manager)
        r = self.client.get(reverse("timesheet:people_today", args=["present"]))
        self.assertEqual([row["user"] for row in r.context["rows"]], [self.emp])
        r = self.client.get(reverse("timesheet:people_today", args=["working"]))
        self.assertEqual(r.context["rows"][0]["hours"], 8)
        with mock.patch("apps.timesheet.services.now_local", return_value=timezone.make_aware(
                timezone.datetime(TODAY.year, TODAY.month, TODAY.day, 19, 0))):
            r = self.client.get(reverse("timesheet:people_today", args=["absent"]))
        self.assertNotIn(self.emp, [row["user"] for row in r.context["rows"]])  # present, so not absent
        self.client.force_login(self.other)  # no team -> not allowed
        self.assertEqual(self.client.get(reverse("timesheet:people_today", args=["present"])).status_code, 403)

    def test_pp04_person_without_role_does_not_break_pages(self, _today):
        User.objects.create_user("norole", password="x", first_name="No", last_name="Role")  # role = None
        self.client.force_login(self.admin)
        with mock.patch("apps.timesheet.services.now_local", return_value=timezone.make_aware(
                timezone.datetime(TODAY.year, TODAY.month, TODAY.day, 19, 0))):
            for url in (reverse("timesheet:people"), reverse("timesheet:attendance"),
                        reverse("timesheet:people_today", args=["absent"]),
                        reverse("timesheet:people_today", args=["present"])):
                self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_pp02_people_filters_and_today_cards(self, _today):
        self._present(self.emp, TODAY)
        services.ensure_range(self.emp, [TODAY])
        self.client.force_login(self.admin)
        url = reverse("timesheet:people")
        self.assertEqual(self.client.get(url + f"?project={self.p1.pk}").status_code, 200)
        r = self.client.get(url + f"?project={self.p1.pk}&status=pending")
        self.assertEqual([row["user"] for row in r.context["page"]], [self.emp])  # only P1's member
        self.assertEqual(r.context["data"]["rows"][0]["total"], 4)  # P1 hours only
        cards = self.client.get(reverse("timesheet:week")).context["admin_cards"]
        self.assertEqual(cards["today"]["working"], 1)
        self.client.force_login(self.manager)
        self.assertIsNone(self.client.get(reverse("timesheet:week")).context["admin_cards"])  # admins only
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(url + "?status=submitted").context["page"].paginator.count, 0)

    # ---------------------------------------------------------------- 10:15 login reminder email
    def test_rm01_login_reminder_rules(self, _today):
        from django.test import override_settings

        self.emp.email = "emp@augtrans.com"; self.emp.save()
        self.other.email = "other@augtrans.com"; self.other.save()
        self.manager.email = ""; self.manager.save()  # no address -> skipped
        self._present(self.other, TODAY)              # already logged in -> no mail
        before_1830 = timezone.make_aware(timezone.datetime(TODAY.year, TODAY.month, TODAY.day, 10, 20))
        with mock.patch("apps.timesheet.services.now_local", return_value=before_1830):
            who = [u.email for u in services.login_reminder_recipients(TODAY)]
        self.assertIn("emp@augtrans.com", who)
        self.assertNotIn("other@augtrans.com", who)
        self.assertNotIn("", who)
        sat = date(2026, 9, 26)
        self.assertEqual(services.login_reminder_recipients(sat), [])  # Saturday: nobody
        Holiday.objects.create(date=TODAY, name="Test holiday")
        with mock.patch("apps.timesheet.services.now_local", return_value=before_1830):
            self.assertEqual(services.login_reminder_recipients(TODAY), [])  # holiday: nobody

    def test_rm02_job_sends_once_per_person_and_only_on_weekdays(self, _today):
        from django.test import override_settings

        self.emp.email = "emp@augtrans.com"; self.emp.save()
        at_1015 = timezone.make_aware(timezone.datetime(TODAY.year, TODAY.month, TODAY.day, 10, 15))
        with override_settings(LOGIN_REMINDER_ENABLED=True, LOGIN_REMINDER_TIME="10:15"), \
                mock.patch("apps.timesheet.services.now_local", return_value=at_1015), \
                mock.patch("apps.timesheet.mailer.send_mail", return_value=(True, "")) as send:
            services.run_scheduled_jobs(now=at_1015)
            services.run_scheduled_jobs(now=at_1015)            # second run: no duplicates
            mailed = [c.args[0] for c in send.call_args_list]
            self.assertEqual(mailed.count("emp@augtrans.com"), 1)
            self.assertIn("Hi ", send.call_args_list[0].args[2])
            sat = timezone.make_aware(timezone.datetime(2026, 10, 3, 10, 30))
            send.reset_mock()
            with mock.patch("apps.timesheet.services.today", return_value=date(2026, 10, 3)):
                services.run_scheduled_jobs(now=sat)
            self.assertFalse(send.called)                       # Saturday: nothing sent

    def test_rm03_only_address_mode_mails_just_that_address(self, _today):
        self.emp.email = "emp@augtrans.com"; self.emp.save()
        with mock.patch("apps.timesheet.mailer.send_mail", return_value=(True, "")) as send:
            r = services.job_login_reminders(TODAY, only_email="tester@augtrans.com")
        self.assertEqual([c.args[0] for c in send.call_args_list], ["tester@augtrans.com"])
        self.assertEqual(r["sent"], ["tester@augtrans.com"])

    # ---------------------------------------------------------------- hours left on a project the person has left
    def test_ls01_hours_on_a_left_project_can_be_lowered_not_raised(self, _today):
        from apps.projects.models import ProjectMembership

        day = TODAY - timedelta(days=1)                                # a finished day keeps its hours
        self._present(self.emp, day)
        services.ensure_range(self.emp, [day], force=True)
        before = self._hours(self.emp, day)
        self.assertGreater(before.get("P3", 0), 0)
        ProjectMembership.objects.filter(user=self.emp, project=self.p3).delete()   # emp leaves P3
        from .models import PastAssignment
        PastAssignment.objects.filter(user=self.emp).delete()   # a removal from before dates were recorded
        monday = rules.week_start(TODAY)
        grid = services.week_grid(self.admin, self.emp, monday)
        p3 = next(r for r in grid["rows"] if r["project"].pk == self.p3.pk)
        cell = next(c for c in p3["cells"] if c["date"] == day)
        self.assertTrue(cell["input"] and cell["reduce_only"])       # visible and editable, downwards only
        errors, _ = services.save_week(self.admin, self.emp, monday, {(day, self.p3.pk): str(before["P3"] + 1)})
        self.assertTrue(errors)                                      # raising is refused
        errors, _ = services.save_week(self.admin, self.emp, monday,
                                       {(day, self.p3.pk): "0", (day, self.p1.pk): str(before["P1"] + before["P3"])})
        self.assertEqual(errors, [])                                 # cleared, and the freed hours moved
        self.assertEqual(self._hours(self.emp, day)["P1"], before["P1"] + before["P3"])

    def test_ls02_removed_from_project_keeps_full_edit_for_days_they_were_on_it(self, _today):
        from apps.projects.models import ProjectMembership
        from .models import PastAssignment

        monday = rules.week_start(TODAY)                                  # Mon 28 Sep
        ProjectMembership.objects.filter(user=self.emp, project=self.p3).delete()   # emp leaves P3
        past = PastAssignment.objects.get(user=self.emp, project=self.p3)
        self.assertEqual(past.started_on, date(2026, 8, 1))
        PastAssignment.objects.filter(pk=past.pk).update(ended_on=date(2026, 9, 29))  # left on Tue 29 Sep
        on_day, after = date(2026, 9, 29), date(2026, 9, 30)
        self._present(self.emp, on_day)
        ctx = services.PersonContext(self.emp)
        self.assertIn(self.p3, ctx.editable_projects(on_day))           # still on it that day: open
        self.assertNotIn(self.p3, ctx.editable_projects(after))         # after leaving: closed
        errors, _ = services.save_week(self.emp, self.emp, monday, {(on_day, self.p3.pk): "3"})
        self.assertEqual(errors, [])                                     # full edit (even raising) works
        errors, _ = services.save_week(self.emp, self.emp, monday, {(after, self.p3.pk): "2"})
        self.assertTrue(errors)                                          # but not after they left

    def test_ls03_deleting_a_user_or_a_project_does_not_record_a_past_assignment(self, _today):
        from .models import PastAssignment

        leaver = User.objects.create_user("leaver", password="x", role=self.member_role)
        self._assign(leaver, self.p2)
        leaver.delete()                                           # used to fail at commit: the row pointed at the deleted user
        self.assertFalse(PastAssignment.objects.filter(user_id=leaver.pk).exists())
        p4 = self._project("P4")
        self._assign(self.other, p4)
        p4.delete()                                               # same for a deleted project
        self.assertFalse(PastAssignment.objects.filter(user=self.other).exists())
        m = ProjectMembership.objects.get(user=self.emp, project=self.p3)
        m.delete()                                                # a real removal from one project is still recorded
        self.assertTrue(PastAssignment.objects.filter(user=self.emp, project=self.p3).exists())
        ProjectMembership.objects.filter(user=self.emp, project=self.p2).delete()
        self.assertTrue(PastAssignment.objects.filter(user=self.emp, project=self.p2).exists())

    def test_ap01_admin_can_enter_hours_for_a_project_in_months_before_it_was_assigned(self, _today):
        p4 = self._project("P4")
        self._assign(self.emp, p4, on=date(2026, 9, 15))       # added in September, worked on in August
        monday, day = date(2026, 8, 10), date(2026, 8, 12)
        grid = services.week_grid(self.emp, self.emp, monday)
        self.assertNotIn(p4.pk, [r["project"].pk for r in grid["rows"]])       # the person: no row, month is before it
        errors, _ = services.save_week(self.emp, self.emp, monday, {(day, p4.pk): "3"})
        self.assertTrue(errors)
        grid = services.week_grid(self.admin, self.emp, monday)
        row = next(r for r in grid["rows"] if r["project"].pk == p4.pk)        # admin: row is there, 0 hours
        cell = next(c for c in row["cells"] if c["date"] == day)
        self.assertTrue(cell["input"])
        self.assertEqual(cell["hours"], 0)
        errors, _ = services.save_week(self.admin, self.emp, monday, {(day, p4.pk): "3"})
        self.assertEqual(errors, [])
        self.assertEqual(self._hours(self.emp, day).get("P4"), 3)
        # a manager gets no such exemption, and a Completed project stays closed even for the admin
        errors, _ = services.save_week(self.manager, self.emp, monday, {(day, p4.pk): "4"})
        self.assertTrue(errors)
        self._set_status(p4, "completed", date(2026, 7, 1))
        errors, _ = services.save_week(self.admin, self.emp, monday, {(date(2026, 8, 13), p4.pk): "2"})
        self.assertTrue(errors)

    def test_ap02_no_login_means_no_hours_except_by_an_admin(self, _today):
        day = date(2026, 9, 22)                                       # a past Tuesday nobody logged in on
        monday = rules.week_start(day)
        self.assertEqual(services.attendance_state(self.emp, day, services.holiday_map(day, day, self.emp), TODAY,
                                                   services.go_live_date()), "absent")
        grid = services.week_grid(self.emp, self.emp, monday)
        col = next(c for c in grid["days"] if c["date"] == day)
        self.assertTrue(col["no_login"])
        self.assertFalse(any(c["input"] for r in grid["rows"] for c in r["cells"] if c["date"] == day))
        for actor in (self.emp, self.manager):
            errors, _ = services.save_week(actor, self.emp, monday, {(day, self.p1.pk): "8"})
            self.assertTrue(errors)
            self.assertIn("no login", errors[0])
        self.assertEqual(self._hours(self.emp, day), {})
        # an admin may still fill it, and once the day is marked Present the person can too
        errors, _ = services.save_week(self.admin, self.emp, monday, {(day, self.p1.pk): "8"})
        self.assertEqual(errors, [])
        services.save_week(self.admin, self.emp, monday, {(day, self.p1.pk): "0"})
        self._present(self.emp, day)
        errors, _ = services.save_week(self.emp, self.emp, monday, {(day, self.p1.pk): "5"})
        self.assertEqual(errors, [])

    def test_ap03_hours_already_on_an_absent_day_can_be_lowered_not_raised(self, _today):
        day = date(2026, 9, 22)
        monday = rules.week_start(day)
        services.save_week(self.admin, self.emp, monday, {(day, self.p1.pk): "8"})   # as the old data was
        grid = services.week_grid(self.emp, self.emp, monday)
        cell = next(c for r in grid["rows"] if r["project"].pk == self.p1.pk for c in r["cells"] if c["date"] == day)
        self.assertTrue(cell["input"] and cell["reduce_only"])
        errors, _ = services.save_week(self.emp, self.emp, monday, {(day, self.p1.pk): "9"})
        self.assertTrue(errors)
        errors, _ = services.save_week(self.emp, self.emp, monday, {(day, self.p1.pk): "0"})
        self.assertEqual(errors, [])

    def test_as03_closed_month_edit_needs_the_permission(self, _today):
        from django.core.cache import cache

        last_month = date(2026, 8, 31)
        self._present(self.emp, last_month)
        monday = rules.week_start(last_month)
        raw = {(last_month, self.p1.pk): "3"}
        self.assertTrue(services.save_week(self.manager, self.emp, monday, raw)[0])   # RM of the team: not any more
        cap, _ = Capability.objects.get_or_create(code="timesheet.edit_closed", defaults={"label": "x", "category": "timesheet"})
        UserCapability.objects.create(user=self.manager, capability=cap, scope="all")
        cache.clear()
        self.assertEqual(services.save_week(self.manager, self.emp, monday, raw)[0], [])   # granted per person: yes
        self.assertTrue(services.save_week(self.other, self.emp, monday, {(last_month, self.p1.pk): "2"})[0])  # not granted
        self.assertEqual(services.save_week(self.admin, self.emp, monday, {(last_month, self.p1.pk): "2"})[0], [])
        # the month still being open: the person and their manager edit as before
        self._present(self.emp, TODAY)
        errors, _ = services.save_week(self.emp, self.emp, rules.week_start(TODAY), {(TODAY, self.p1.pk): "3"})
        self.assertEqual(errors, [])

    # ---------------------------------------------------------------- extra working days
    def test_wd02_working_saturday_is_filled_like_a_workday_and_other_saturdays_stay_off(self, _today):
        sat, other_sat = date(2026, 9, 26), date(2026, 9, 19)   # 4th and 3rd Saturdays of September
        services.save_working_day(self.admin, sat, "Swap for the long weekend")
        for day in (sat, other_sat):
            self._present(self.emp, day)
            services.job_close_day(day)
        self.assertEqual(self._hours(self.emp, sat), {"P1": 4, "P2": 2, "P3": 2})
        self.assertEqual(self._hours(self.emp, other_sat), {})

    def test_wd03_employee_can_enter_hours_on_a_working_sunday_only(self, _today):
        sunday, other_sunday = date(2026, 9, 27), date(2026, 9, 20)
        services.save_working_day(self.admin, sunday, "Release weekend")
        self._present(self.emp, sunday)
        errors, _ = services.save_week(self.emp, self.emp, rules.week_start(sunday), {(sunday, self.p1.pk): "4"})
        self.assertEqual(errors, [])
        self.assertEqual(self._hours(self.emp, sunday).get("P1"), 4)
        errors, _ = services.save_week(self.emp, self.emp, rules.week_start(other_sunday),
                                       {(other_sunday, self.p1.pk): "4"})
        self.assertTrue(errors)
        cols = {c["date"]: c for c in services.week_grid(self.emp, self.emp, rules.week_start(sunday))["days"]}
        self.assertTrue(cols[sunday]["editable"])
        self.assertEqual(cols[sunday]["working_day"].name, "Release weekend")
        self.assertEqual(cols[sunday]["kind"], rules.WORKDAY)

    def test_wd04_working_saturday_marks_absent_when_nobody_logged_in(self, _today):
        sat, other_sat = date(2026, 9, 26), date(2026, 9, 19)
        services.save_working_day(self.admin, sat, "Swap")
        for day in (sat, other_sat):
            services.job_mark_absent(day)
        self.assertEqual(Attendance.objects.get(user=self.emp, date=sat).status, "absent")
        self.assertFalse(Attendance.objects.filter(user=self.emp, date=other_sat).exists())

    def test_wd05_first_saturday_off_and_last_saturday_working(self, _today):
        """The long-weekend swap: the 1st Saturday is a holiday (0 hours), another Saturday takes its place (8)."""
        first_sat, last_sat = date(2026, 9, 5), date(2026, 9, 26)
        services.save_holiday(self.admin, first_sat, "Long weekend off")
        services.save_working_day(self.admin, last_sat, "Long weekend swap")
        for day in (first_sat, last_sat):
            self._present(self.emp, day)
            services.job_close_day(day)
        self.assertEqual(self._hours(self.emp, first_sat), {})
        self.assertEqual(sum(self._hours(self.emp, last_sat).values()), 8)
        errors, _ = services.save_week(self.emp, self.emp, rules.week_start(first_sat), {(first_sat, self.p1.pk): "4"})
        self.assertTrue(errors)                                  # a holiday takes no hours

    def test_wd06_working_day_is_for_every_team(self, _today):
        pune, delhi, _uiux = self._teams()
        day = date(2026, 9, 26)
        services.save_working_day(self.admin, day, "Swap")
        for person in (self.emp, self.lead, self.other):                 # Pune, UI/UX and Delhi alike
            self.assertIn(day, services.holiday_map(day, day, person).working)
            self._present(person, day)
        services.job_close_day(day)
        for person in (self.emp, self.lead, self.other):
            self.assertEqual(sum(self._hours(person, day).values()), 8)

    def test_wd07_holiday_on_a_working_day_still_wins(self, _today):
        day = date(2026, 9, 26)
        services.save_working_day(self.admin, day, "Swap")
        services.save_holiday(self.admin, day, "State holiday")
        self.assertEqual(rules.day_kind(day, services.holiday_map(day, day, self.emp)), rules.HOLIDAY)

    def test_wd08_refused_on_a_normal_workday_and_on_overlap(self, _today):
        with self.assertRaises(ValueError):
            services.save_working_day(self.admin, date(2026, 9, 29), "Tuesday")      # already a workday
        with self.assertRaises(ValueError):
            services.save_working_day(self.admin, date(2026, 9, 5), "1st Saturday")  # already a workday
        services.save_working_day(self.admin, date(2026, 9, 26), "Swap")
        with self.assertRaises(ValueError):
            services.save_working_day(self.admin, date(2026, 9, 26), "Again")
        with self.assertRaises(PermissionDenied):
            services.save_working_day(self.emp, date(2026, 9, 27), "Not allowed")

    def test_wd09_removing_a_working_day_clears_untouched_auto_hours_only(self, _today):
        sat, sun = date(2026, 9, 26), date(2026, 9, 27)
        wd_sat = services.save_working_day(self.admin, sat, "Swap")
        wd_sun = services.save_working_day(self.admin, sun, "Release")
        self._present(self.emp, sat)
        self._present(self.emp, sun)
        services.job_close_day(sat)
        errors, _ = services.save_week(self.emp, self.emp, rules.week_start(sun), {(sun, self.p1.pk): "3"})
        self.assertEqual(errors, [])
        self.assertTrue(self._hours(self.emp, sat))
        services.delete_working_day(self.admin, wd_sat)
        services.delete_working_day(self.admin, wd_sun)
        self.assertEqual(self._hours(self.emp, sat), {})                       # untouched auto-fill goes
        self.assertEqual(self._hours(self.emp, sun).get("P1"), 3)              # what a person typed stays
        self.assertFalse(WorkingDay.objects.exists())

    def test_wd10_people_week_and_report_see_the_working_day(self, _today):
        sat, sun = date(2026, 9, 26), date(2026, 9, 27)
        monday = rules.week_start(sat)
        before = services.people_week([self.emp], monday)
        services.save_working_day(self.admin, sat, "Swap")
        after = services.people_week([self.emp], monday)
        self.assertEqual(after["workdays"], before["workdays"] + 1)
        kinds = {c["date"]: c["kind"] for c in after["days"]}
        self.assertEqual((kinds[sat], kinds[sun]), (rules.WORKDAY, rules.WEEKEND))
        report = services.weekly_report(self.admin, monday)
        self.assertEqual(report["weekends"], [sun])

    def test_wd11_working_days_page_add_edit_delete_and_permission(self, _today):
        self.client.force_login(self.emp)
        r = self.client.post(reverse("timesheet:working_day_add"), {"date": "2026-10-31", "name": "X"})
        self.assertEqual(r.status_code, 403)
        self.client.force_login(self.admin)
        r = self.client.post(reverse("timesheet:working_day_add"), {"date": "2026-10-31", "name": "Month-end Saturday"})
        self.assertEqual(r.status_code, 302)
        wd = WorkingDay.objects.get(name="Month-end Saturday")
        self.assertContains(self.client.get(reverse("timesheet:holidays")), "Month-end Saturday")
        self.client.post(reverse("timesheet:working_day_edit", args=[wd.pk]), {"date": "2026-11-01", "name": "Sunday work"})
        wd.refresh_from_db()
        self.assertEqual((wd.date, wd.name), (date(2026, 11, 1), "Sunday work"))
        self.assertEqual(self.client.post(reverse("timesheet:working_day_add"),
                                          {"date": "2026-11-02", "name": "Monday"}).status_code, 302)
        self.assertFalse(WorkingDay.objects.filter(date=date(2026, 11, 2)).exists())    # refused: a normal workday
        self.client.post(reverse("timesheet:working_day_delete", args=[wd.pk]))
        self.assertFalse(WorkingDay.objects.exists())

    def test_ex01_summary_sheet_columns(self, _today):
        """The Excel download has a Summary (name, days, hours, submitted, Timesheet status, projects) and an
        Attendance sheet; the Timesheet status is Filled / Partially filled / Not filled by submitted days."""
        import io

        from openpyxl import load_workbook

        d2, d3 = date(2026, 9, 2), date(2026, 9, 3)
        for day in (d2, d3):
            self._present(self.emp, day)
            services.job_close_day(day)                      # emp: hours on P1, P2 and P3 on both days
        TimesheetDay.objects.filter(user=self.emp, date=d2).update(status="submitted")     # one of two submitted
        for person in (self.other, self.manager):            # the close-day job marked them absent; make them present
            Attendance.objects.update_or_create(user=person, date=d2,
                                                defaults={"status": "present", "first_login": aware(d2, 11)})
        TimesheetDay.objects.create(user=self.manager, date=d2, status="submitted")       # manager: all submitted
        self.client.force_login(self.admin)
        r = self.client.get(reverse("timesheet:attendance_export"), {"from": "2026-09-01", "to": "2026-09-30"})
        self.assertEqual(r.status_code, 200)
        wb = load_workbook(io.BytesIO(r.content))
        self.assertEqual(wb.sheetnames, ["Summary", "Attendance"])   # no Daily log / Timesheet entries sheets
        ws = wb["Summary"]
        self.assertEqual([c.value for c in ws[3]], ["Employee", "Present days", "Leave days", "Hours logged",
                                                    "Days submitted", "Timesheet", "Projects"])
        self.assertEqual(ws.freeze_panes, "B4")
        status = {row[0].value: row[5].value for row in ws.iter_rows(min_row=4)}
        self.assertEqual(status["emp"], "Partially filled")
        self.assertEqual(status["other"], "Not filled")
        self.assertEqual(status["manager"], "Filled")
        self.assertEqual(status["lead"], "–")                       # nothing was due: never present
        projects = {row[0].value: row[6].value for row in ws.iter_rows(min_row=4)}
        self.assertEqual(projects["emp"], "Project P1, Project P2, Project P3")   # names, comma separated
        self.assertFalse(projects["other"])
        leave = {row[0].value: row[2].value for row in ws.iter_rows(min_row=4)}
        self.assertEqual(leave["emp"], 0)                            # leave shows as days, 8 hours = 1 day
        att = wb["Attendance"]
        self.assertEqual([c.value for c in att[3]][:3], ["Employee", "01 Sep\nTue", "02 Sep\nWed"])  # no Team column
        self.assertEqual([c.value for c in att[3]][-3:], ["Present", "Absent", "Leave"])
        self.assertEqual(att.freeze_panes, "B4")
