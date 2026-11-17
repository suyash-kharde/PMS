from django.test import TestCase
from django.urls import reverse

from apps.audit.context import set_current_user

from .models import User


class FirstLoginPasswordChangeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("pat", password="Initial-pass-1")
        self.addCleanup(set_current_user, None)
        self.client.force_login(self.user)

    def test_new_account_is_flagged(self):
        self.assertTrue(self.user.must_change_password)

    def test_every_page_goes_to_change_password(self):
        r = self.client.get("/timesheet/")
        self.assertRedirects(r, reverse("accounts:password_change") + "?next=%2Ftimesheet%2F", fetch_redirect_response=False)
        page = self.client.get(reverse("accounts:password_change"))
        self.assertContains(page, "Please set a new password")

    def test_background_json_gets_403_and_mcp_is_left_alone(self):
        r = self.client.get("/api/notifications/unread/")
        self.assertEqual((r.status_code, r.json()), (403, {"error": "password_change_required"}))
        self.assertNotEqual(self.client.get("/mcp/").status_code, 302)

    def test_sign_out_still_works(self):
        self.client.post(reverse("accounts:logout"))
        self.assertEqual(self.client.get("/timesheet/").url.split("?")[0], "/login/")

    def test_wrong_current_password_or_weak_new_one_is_refused(self):
        url = reverse("accounts:password_change")
        self.client.post(url, {"old_password": "nope", "new_password1": "Brand-new-7", "new_password2": "Brand-new-7"})
        self.client.post(url, {"old_password": "Initial-pass-1", "new_password1": "123456", "new_password2": "123456"})
        self.user.refresh_from_db()
        self.assertTrue(self.user.must_change_password)
        self.assertTrue(self.user.check_password("Initial-pass-1"))

    def test_change_clears_flag_keeps_session_and_goes_to_next(self):
        r = self.client.post(reverse("accounts:password_change"), {
            "old_password": "Initial-pass-1", "new_password1": "Brand-new-7", "new_password2": "Brand-new-7",
            "next": "/timesheet/",
        })
        self.assertRedirects(r, "/timesheet/", fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertFalse(self.user.must_change_password)
        self.assertTrue(self.user.check_password("Brand-new-7"))
        self.assertEqual(self.client.get("/api/notifications/unread/").status_code, 200)  # still signed in

    def test_only_once_an_admin_reset_does_not_ask_again(self):
        self.client.post(reverse("accounts:password_change"), {
            "old_password": "Initial-pass-1", "new_password1": "Brand-new-7", "new_password2": "Brand-new-7"})
        self.user.refresh_from_db()
        self.user.set_password("Admin-set-9")  # what People -> Edit does
        self.user.save()
        self.user.refresh_from_db()
        self.assertFalse(self.user.must_change_password)

    def test_outside_links_are_not_followed(self):
        r = self.client.post(reverse("accounts:password_change"), {
            "old_password": "Initial-pass-1", "new_password1": "Brand-new-7", "new_password2": "Brand-new-7",
            "next": "https://evil.example/",
        })
        self.assertEqual(r.url, reverse("core:dashboard"))


class WorkedTodayAndActionsTests(TestCase):
    def setUp(self):
        from apps.accounts.testing import skip_password_prompt
        from apps.projects.models import Project

        skip_password_prompt(self)
        self.addCleanup(set_current_user, None)
        self.user = User.objects.create_user("pat", password="x")
        self.other = User.objects.create_user("sam", password="x")
        self.project = Project.objects.create(key="P1", name="Project 1", status="active", owner=self.user)

    def test_time_format(self):
        from apps.accounts.templatetags.people import hours_minutes

        cases = {0: "0 min", 240: "4 min", 3600: "1 hr", 3900: "1 hr 5 min", 8100: "2 hr 15 min", None: "0 min"}
        for seconds, text in cases.items():
            self.assertEqual(hours_minutes(seconds), text)

    def test_actions_counted_by_kind_and_person(self):
        from datetime import timedelta

        from django.utils import timezone

        from apps.projects.models import Activity, Task
        from apps.tickets.models import Ticket, TicketEvent
        from apps.timesheet.models import TimesheetAudit

        from .actions import action_counts

        task = Task.objects.create(project=self.project, title="T")
        Activity.log(self.user, "updated task “T”", task=task)
        Activity.log(self.user, "regenerated the BRD (v3)", project=self.project)
        Activity.log(self.user, "added a acceptance criterion to P1-R1", project=self.project)
        Activity.log(self.user, "updated the Tech Stack", project=self.project)
        Activity.log(self.other, "updated task “T”", task=task)
        ticket = Ticket.objects.create(project=self.project, title="Bug", reporter=self.user)
        TicketEvent.log(ticket, self.user, "raised this bug")
        TicketEvent.log(ticket, self.user, "commented")
        for ref in ("2026-10-05 · P1", "2026-10-05 · P2", "2026-10-06 · P1"):  # one save, two days
            TimesheetAudit.objects.create(actor=self.user, entity="hours", ref=ref)
        start = timezone.now() - timedelta(hours=1)
        counts = action_counts([self.user.pk, self.other.pk], start, start + timedelta(hours=2))
        self.assertEqual(dict(counts[self.user.pk]["kinds"]), {
            "Tasks": 1, "Tickets": 2, "BRD & timeline": 1, "Sprints & requirements": 1, "Timesheet": 2,
            "Projects & docs": 1,
        })
        self.assertEqual((counts[self.user.pk]["total"], counts[self.other.pk]["total"]), (8, 1))

    def test_person_page_shows_actions(self):
        from apps.accounts.models import Capability, Role, RoleCapability
        from apps.projects.models import Activity

        role = Role.objects.create(name="Viewer", slug="viewer")
        RoleCapability.objects.create(role=role, capability=Capability.objects.create(code="user.view", label="v"),
                                      scope="all")
        self.user.role = role
        self.user.save()
        Activity.log(self.user, "updated the Tech Stack", project=self.project)
        self.client.force_login(self.user)
        detail = self.client.get(reverse("accounts:user_detail", args=[self.user.pk]))
        self.assertContains(detail, "Projects &amp; docs <strong>1</strong>")
