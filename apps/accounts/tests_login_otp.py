"""Emailed one-time login codes (apps/accounts/otp.py)."""
import re
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from .models import User
from .testing import skip_password_prompt

MAIL = "apps.timesheet.mailer.send_mail"


def _sent_code(mocked):
    return re.search(r">(\d{6})<", mocked.call_args[0][2]).group(1)


@override_settings(LOGIN_OTP_ENABLED=True, LOGIN_OTP_USERS=set())
class LoginOtpTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        self.user = User.objects.create_user("pat", password="pw12345", email="pat@example.com")

    def _password_step(self, mocked, **extra):
        return self.client.post(reverse("accounts:login"), {"username": "pat", "password": "pw12345", **extra})

    def test_correct_password_alone_does_not_sign_in(self):
        with mock.patch(MAIL, return_value=(True, "")) as mocked:
            resp = self._password_step(mocked)
        self.assertRedirects(resp, reverse("accounts:login_otp"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(mocked.call_args[0][0], "pat@example.com")

    def test_right_code_signs_in_and_goes_to_next(self):
        with mock.patch(MAIL, return_value=(True, "")) as mocked:
            self._password_step(mocked, next="/projects/")
            code = _sent_code(mocked)
        resp = self.client.post(reverse("accounts:login_otp"), {"code": code})
        self.assertRedirects(resp, "/projects/", fetch_redirect_response=False)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    def test_wrong_code_does_not_sign_in_and_five_wrongs_lock_it(self):
        with mock.patch(MAIL, return_value=(True, "")) as mocked:
            self._password_step(mocked)
            good = _sent_code(mocked)
        bad = "000000" if good != "000000" else "111111"
        for _ in range(4):
            self.assertContains(self.client.post(reverse("accounts:login_otp"), {"code": bad}), "not right")
        resp = self.client.post(reverse("accounts:login_otp"), {"code": bad})
        self.assertRedirects(resp, reverse("accounts:login") + "?otp=locked", fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
        # the real code no longer works either: the sign-in was wiped
        resp = self.client.post(reverse("accounts:login_otp"), {"code": good})
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_code_page_needs_a_correct_password_first(self):
        resp = self.client.get(reverse("accounts:login_otp"))
        self.assertRedirects(resp, reverse("accounts:login") + "?otp=expired", fetch_redirect_response=False)

    def test_wrong_password_sends_no_mail(self):
        with mock.patch(MAIL, return_value=(True, "")) as mocked:
            self.client.post(reverse("accounts:login"), {"username": "pat", "password": "nope"})
        mocked.assert_not_called()

    def test_person_with_no_email_cannot_sign_in(self):
        self.user.email = ""
        self.user.save()
        with mock.patch(MAIL, return_value=(True, "")) as mocked:
            resp = self._password_step(mocked)
        self.assertContains(resp, "no email address")
        self.assertNotIn("_auth_user_id", self.client.session)
        mocked.assert_not_called()

    def test_mail_failure_fails_closed(self):
        with mock.patch(MAIL, return_value=(False, "boom")) as mocked:
            resp = self._password_step(mocked)
        self.assertContains(resp, "couldn")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_a_session_that_skipped_the_code_step_is_signed_out(self):
        self.client.force_login(self.user)  # e.g. a session from before codes were switched on
        resp = self.client.get(reverse("core:dashboard"))
        self.assertRedirects(resp, reverse("accounts:login") + "?otp=required", fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_a_session_that_came_through_the_code_step_stays_signed_in(self):
        with mock.patch(MAIL, return_value=(True, "")) as mocked:
            self._password_step(mocked)
            code = _sent_code(mocked)
        self.client.post(reverse("accounts:login_otp"), {"code": code})
        self.assertEqual(self.client.get(reverse("core:dashboard")).status_code, 200)
        self.assertIn("_auth_user_id", self.client.session)

    def test_django_admin_login_goes_through_our_sign_in(self):
        resp = self.client.get("/django-admin/login/")
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.url.startswith(reverse("accounts:login")))


class LoginOtpOffTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        User.objects.create_user("pat", password="pw12345", email="pat@example.com")

    @override_settings(LOGIN_OTP_ENABLED=False)
    def test_switched_off_logs_in_straight_away(self):
        with mock.patch(MAIL) as mocked:
            self.client.post(reverse("accounts:login"), {"username": "pat", "password": "pw12345"})
        self.assertIn("_auth_user_id", self.client.session)
        mocked.assert_not_called()

    @override_settings(LOGIN_OTP_ENABLED=True, LOGIN_OTP_USERS={"someone-else"})
    def test_pilot_list_leaves_everyone_else_alone(self):
        with mock.patch(MAIL) as mocked:
            self.client.post(reverse("accounts:login"), {"username": "pat", "password": "pw12345"})
        self.assertIn("_auth_user_id", self.client.session)
        mocked.assert_not_called()
