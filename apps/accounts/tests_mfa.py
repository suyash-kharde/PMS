"""Authenticator-app MFA (apps/accounts/mfa.py)."""
import re
import time

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from . import mfa, site_settings
from .forms import UserForm
from .models import Capability, Role, RoleCapability, User
from .testing import skip_password_prompt


def _now_code(secret, offset=0):
    return mfa._code_for(secret, int(time.time() // mfa.STEP) + offset)


class MfaTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        cache.clear()
        self.user = User.objects.create_user("pat", password="pw12345", email="pat@example.com", mfa_required=True)

    def _login(self, **extra):
        return self.client.post(reverse("accounts:login"), {"username": "pat", "password": "pw12345", **extra})

    def _setup_secret(self):
        return self.client.session[mfa.PENDING_KEY]["setup_secret"]

    def _enrol(self):
        self._login()
        secret = self._setup_secret()
        resp = self.client.post(reverse("accounts:login_mfa"), {"code": _now_code(secret)})
        return secret, resp

    def test_off_means_password_only_even_if_ticked(self):
        site_settings.set_mfa_mode("off")
        self._login()
        self.assertIn("_auth_user_id", self.client.session)

    def test_ticked_person_must_set_up_first_time(self):
        site_settings.set_mfa_mode("selected")
        resp = self._login()
        self.assertRedirects(resp, reverse("accounts:login_mfa"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
        page = self.client.get(reverse("accounts:login_mfa"))
        self.assertContains(page, "Set up your authenticator")
        self.assertContains(page, "otpauth://totp/")
        wrong = self.client.post(reverse("accounts:login_mfa"), {"code": "000000"})
        self.assertContains(wrong, "not right")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_setup_signs_in_stores_encrypted_secret_and_shows_backup_codes_once(self):
        site_settings.set_mfa_mode("selected")
        secret, resp = self._enrol()
        self.assertRedirects(resp, reverse("accounts:mfa_backup_codes"), fetch_redirect_response=False)
        self.assertIn("_auth_user_id", self.client.session)
        self.user.refresh_from_db()
        self.assertTrue(self.user.mfa_secret)
        self.assertNotIn(secret, self.user.mfa_secret)
        page = self.client.get(reverse("accounts:mfa_backup_codes"))
        self.assertEqual(len(re.findall(r"<li>[0-9a-f]{4}-[0-9a-f]{4}</li>", page.content.decode())), 8)
        again = self.client.get(reverse("accounts:mfa_backup_codes"))  # the codes are gone after the first view
        self.assertEqual(again.status_code, 302)

    def test_next_sign_in_needs_a_fresh_code_and_refuses_a_replay(self):
        site_settings.set_mfa_mode("selected")
        secret, _ = self._enrol()
        self.client.post(reverse("accounts:logout"))
        self._login()
        self.assertNotIn("setup_secret", self.client.session[mfa.PENDING_KEY])
        replay = self.client.post(reverse("accounts:login_mfa"), {"code": _now_code(secret)})  # same step as setup
        self.assertContains(replay, "not right")
        ok = self.client.post(reverse("accounts:login_mfa"), {"code": _now_code(secret, 1)})
        self.assertEqual(ok.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)

    def test_a_backup_code_works_once(self):
        site_settings.set_mfa_mode("selected")
        self._enrol()
        codes = self.client.session["mfa_backup_codes"]
        self.client.post(reverse("accounts:logout"))
        self._login()
        self.client.post(reverse("accounts:login_mfa"), {"code": codes[0]})
        self.assertIn("_auth_user_id", self.client.session)
        self.client.post(reverse("accounts:logout"))
        self._login()
        self.client.post(reverse("accounts:login_mfa"), {"code": codes[0]})
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_five_wrong_codes_cancel_the_sign_in(self):
        site_settings.set_mfa_mode("selected")
        self._login()
        for _ in range(4):
            self.client.post(reverse("accounts:login_mfa"), {"code": "000000"})
        resp = self.client.post(reverse("accounts:login_mfa"), {"code": "000000"})
        self.assertRedirects(resp, reverse("accounts:login") + "?otp=mfa_locked", fetch_redirect_response=False)

    def test_selected_mode_leaves_unticked_people_alone_and_everyone_mode_does_not(self):
        User.objects.create_user("lee", password="pw12345", email="lee@example.com")
        site_settings.set_mfa_mode("selected")
        self.client.post(reverse("accounts:login"), {"username": "lee", "password": "pw12345"})
        self.assertIn("_auth_user_id", self.client.session)
        self.client.post(reverse("accounts:logout"))
        site_settings.set_mfa_mode("everyone")
        resp = self.client.post(reverse("accounts:login"), {"username": "lee", "password": "pw12345"})
        self.assertRedirects(resp, reverse("accounts:login_mfa"), fetch_redirect_response=False)

    @override_settings(MFA_FORCE_OFF=True)
    def test_emergency_brake_switches_it_off_for_everyone(self):
        site_settings.set_mfa_mode("everyone")
        self._login()
        self.assertIn("_auth_user_id", self.client.session)

    def test_a_session_that_skipped_the_code_step_is_signed_out(self):
        site_settings.set_mfa_mode("selected")
        self.client.force_login(self.user)
        resp = self.client.get(reverse("core:dashboard"))
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_reset_needs_the_security_permission_and_clears_the_setup(self):
        site_settings.set_mfa_mode("selected")
        self._enrol()
        self.client.post(reverse("accounts:logout"))
        boss = User.objects.create_superuser("boss", password="x")
        member = User.objects.create_user("mem", password="x")
        self.client.force_login(member)
        self.assertEqual(self.client.post(reverse("accounts:mfa_reset", args=[self.user.pk])).status_code, 403)
        self.client.force_login(boss)
        self.client.post(reverse("accounts:mfa_reset", args=[self.user.pk]))
        self.user.refresh_from_db()
        self.assertEqual(self.user.mfa_secret, "")
        self.assertIsNone(self.user.mfa_confirmed_at)

    def test_the_checkbox_is_only_offered_to_people_with_the_security_permission(self):
        cap, _ = Capability.objects.get_or_create(code="security.manage", defaults={"label": "Security", "category": "admin"})
        role = Role.objects.create(name="Sec", slug="sec", rank=50)
        RoleCapability.objects.create(role=role, capability=cap, scope="all")
        allowed = User.objects.create_user("sec", password="x", role=role)
        plain = User.objects.create_user("plain", password="x")
        self.assertIn("mfa_required", UserForm(instance=self.user, editor=allowed).fields)
        self.assertNotIn("mfa_required", UserForm(instance=self.user, editor=plain).fields)

    def test_security_page_saves_the_company_choice(self):
        boss = User.objects.create_superuser("boss", password="x")
        self.client.force_login(boss)
        self.client.post(reverse("accounts:security_settings"), {"action": "mfa", "mode": "everyone"})
        self.assertEqual(site_settings.mfa_mode(), "everyone")
        self.client.post(reverse("accounts:security_settings"), {"action": "mfa", "mode": "nonsense"})
        self.assertEqual(site_settings.mfa_mode(), "everyone")
