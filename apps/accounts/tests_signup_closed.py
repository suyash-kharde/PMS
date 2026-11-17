"""Self sign-up is closed unless ALLOW_SIGNUP=1."""
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import User


class SignupClosedTests(TestCase):
    def test_signup_page_is_not_available(self):
        self.assertEqual(self.client.get(reverse("accounts:signup")).status_code, 404)

    def test_posting_to_signup_creates_nothing(self):
        data = {"username": "sneaky", "password1": "Zq9!longpass", "password2": "Zq9!longpass", "email": "s@example.com"}
        self.assertEqual(self.client.post(reverse("accounts:signup"), data).status_code, 404)
        self.assertFalse(User.objects.filter(username="sneaky").exists())

    def test_login_page_has_no_create_account_link(self):
        self.assertNotContains(self.client.get(reverse("accounts:login")), "Create one")

    @override_settings(ALLOW_SIGNUP=True)
    def test_the_switch_can_reopen_it(self):
        self.assertEqual(self.client.get(reverse("accounts:signup")).status_code, 200)
