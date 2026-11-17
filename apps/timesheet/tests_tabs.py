"""Each Timesheet tab is a permission (timesheet.tab.*) that can be switched on or off per role."""
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Capability, Role, RoleCapability, User
from apps.accounts.permissions import invalidate
from apps.accounts.testing import skip_password_prompt

TABS = ("week", "people", "calendar", "holidays", "attendance", "reports")


def _role(name, tabs):
    role = Role.objects.create(name=name, slug=name.lower())
    for tab in tabs:
        cap, _ = Capability.objects.get_or_create(code=f"timesheet.tab.{tab}", defaults={"label": tab})
        RoleCapability.objects.create(role=role, capability=cap, scope="all")
    invalidate()
    return role


class TimesheetTabPermissionTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        self.addCleanup(cache.clear)

    def _login(self, name, tabs, **extra):
        user = User.objects.create_user(name, password="x", role=_role(name, tabs), **extra)
        self.client.force_login(user)
        return user

    def test_page_opens_on_the_first_allowed_tab(self):
        self._login("reportsonly", ["reports"])
        resp = self.client.get(reverse("timesheet:week"))
        self.assertRedirects(resp, reverse("timesheet:reports"), fetch_redirect_response=False)

    def test_tab_bar_shows_only_the_allowed_tabs(self):
        self._login("limited", ["week", "calendar"])
        resp = self.client.get(reverse("timesheet:week"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse("timesheet:calendar"))
        self.assertNotContains(resp, 'href="%s"' % reverse("timesheet:holidays"))
        self.assertNotContains(resp, 'href="%s"' % reverse("timesheet:reports"))

    def test_a_hidden_tab_is_blocked_by_its_address_too(self):
        self._login("limited", ["week"])
        for name in ("calendar", "holidays", "reports"):
            with self.subTest(tab=name):
                self.assertEqual(self.client.get(reverse(f"timesheet:{name}")).status_code, 403)

    def test_people_and_attendance_need_the_permission_even_for_a_manager(self):
        boss = User.objects.create_user("boss", password="x", role=_role("noteam", ["week"]))
        User.objects.create_user("report", password="x", manager=boss)  # makes boss a reporting manager
        self.client.force_login(boss)
        self.assertEqual(self.client.get(reverse("timesheet:people")).status_code, 403)
        self.assertEqual(self.client.get(reverse("timesheet:attendance")).status_code, 403)
        RoleCapability.objects.create(
            role=boss.role, capability=Capability.objects.get_or_create(
                code="timesheet.tab.people", defaults={"label": "people"})[0], scope="all")
        invalidate()
        self.assertEqual(self.client.get(reverse("timesheet:people")).status_code, 200)

    def test_no_tab_permission_means_no_timesheet_and_no_menu_entry(self):
        self._login("none", [])
        self.assertEqual(self.client.get(reverse("timesheet:week")).status_code, 403)
        dashboard = self.client.get(reverse("core:dashboard"))
        self.assertNotContains(dashboard, 'href="%s"' % reverse("timesheet:week"))

    def test_superuser_keeps_every_tab(self):
        root = User.objects.create_superuser("root", password="x")
        self.client.force_login(root)
        for name in ("week", "people", "calendar", "holidays", "attendance", "reports"):
            with self.subTest(tab=name):
                self.assertEqual(self.client.get(reverse(f"timesheet:{name}")).status_code, 200)
