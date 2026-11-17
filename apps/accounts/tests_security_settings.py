"""The admin-set auto sign-out time (site_settings.py and the Security page)."""
import time

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from . import site_settings
from .middleware import CapabilityMiddleware
from .models import Capability, Role, RoleCapability, SiteSetting, User
from .testing import skip_password_prompt


class SecuritySettingsTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        cache.clear()
        self.admin = User.objects.create_superuser("boss", password="x")
        self.member = User.objects.create_user("pat", password="x")

    def test_default_is_the_built_in_two_hours(self):
        self.assertEqual(site_settings.idle_timeout_seconds(), 2 * 60 * 60)

    def test_admin_can_set_any_time_and_it_applies_at_once(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("accounts:security_settings"), {"amount": "5", "unit": "hours"})
        self.assertEqual(site_settings.idle_timeout_seconds(), 5 * 3600)
        self.client.post(reverse("accounts:security_settings"), {"amount": "90", "unit": "minutes"})
        self.assertEqual(site_settings.idle_timeout_seconds(), 90 * 60)
        self.client.post(reverse("accounts:security_settings"), {"preset": "1800"})
        self.assertEqual(site_settings.idle_timeout_seconds(), 1800)

    def test_too_small_too_big_or_nonsense_is_refused(self):
        self.client.force_login(self.admin)
        for data in ({"amount": "1", "unit": "minutes"}, {"amount": "30", "unit": "days"},
                     {"amount": "abc", "unit": "hours"}, {"preset": "12345"}):
            self.client.post(reverse("accounts:security_settings"), data)
        self.assertEqual(site_settings.idle_timeout_seconds(), 2 * 60 * 60)

    def test_only_roles_holding_the_permission_can_open_or_change_it(self):
        cap, _ = Capability.objects.get_or_create(
            code="security.manage", defaults={"label": "Security settings", "category": "admin"})
        with_perm = Role.objects.create(name="Security team", slug="security-team", rank=50)
        without = Role.objects.create(name="Administrator", slug="admin", rank=10)
        RoleCapability.objects.create(role=with_perm, capability=cap, scope="all")
        refused = User.objects.create_user("adm", password="x", role=without)
        for person in (self.member, refused):  # no permission: not even the Administrator role
            self.client.force_login(person)
            self.assertEqual(self.client.get(reverse("accounts:security_settings")).status_code, 403)
            self.client.post(reverse("accounts:security_settings"), {"amount": "9", "unit": "hours"})
            self.assertFalse(SiteSetting.objects.exists())
        allowed = User.objects.create_user("sec", password="x", role=with_perm)
        self.client.force_login(allowed)
        self.assertEqual(self.client.get(reverse("accounts:security_settings")).status_code, 200)
        self.client.post(reverse("accounts:security_settings"), {"amount": "3", "unit": "hours"})
        self.assertEqual(site_settings.idle_timeout_seconds(), 3 * 3600)

    def test_the_middleware_uses_the_saved_time(self):
        site_settings.set_idle_timeout_seconds(10 * 60, self.admin)

        class Req:
            session = {"idle_last_seen": time.time() - 11 * 60}
        self.assertTrue(CapabilityMiddleware._idle_timed_out(Req()))
        Req.session = {"idle_last_seen": time.time() - 9 * 60}
        self.assertFalse(CapabilityMiddleware._idle_timed_out(Req()))

    def test_describe(self):
        self.assertEqual(site_settings.describe(3600), "1 hour")
        self.assertEqual(site_settings.describe(5400), "1 hour 30 minutes")
        self.assertEqual(site_settings.describe(1800), "30 minutes")
        self.assertEqual(site_settings.describe(2 * 86400), "2 days")
