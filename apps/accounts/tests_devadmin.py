"""The Devadmin role is treated as admin everywhere the code asks "is this an admin?"."""
from django.test import TestCase
from django.urls import reverse

from apps.jobs import views as jobs_views
from apps.timesheet import permissions as ts_perms

from .models import Role, User
from .testing import skip_password_prompt


class DevadminIsAdminTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        self.devadmin = User.objects.create_user("dev", password="x", role=Role.objects.create(name="Devadmin", slug="devadmin", rank=1))
        self.admin = User.objects.create_user("adm", password="x", role=Role.objects.create(name="Administrator", slug="admin", rank=10))
        self.member = User.objects.create_user("mem", password="x", role=Role.objects.create(name="Member", slug="member", rank=400))

    def test_property(self):
        self.assertTrue(self.devadmin.is_site_admin)
        self.assertTrue(self.admin.is_site_admin)
        self.assertFalse(self.member.is_site_admin)
        self.assertTrue(User.objects.create_superuser("su", password="x").is_site_admin)

    def test_timesheet_and_jobs_treat_devadmin_as_admin(self):
        self.assertTrue(ts_perms.is_admin(self.devadmin))
        self.assertTrue(jobs_views.is_admin(self.devadmin))
        self.assertFalse(ts_perms.is_admin(self.member))
        self.assertTrue(ts_perms.can_edit_day(self.devadmin, self.member, __import__("datetime").date(2020, 1, 6), None))

    def test_dashboard_shows_the_admin_cards_to_devadmin_but_not_to_members(self):
        self.client.force_login(self.devadmin)
        self.assertContains(self.client.get(reverse("core:dashboard")), "Logged in today")
        self.client.force_login(self.member)
        self.assertNotContains(self.client.get(reverse("core:dashboard")), "Logged in today")
