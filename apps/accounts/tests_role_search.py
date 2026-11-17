"""The role page has a search box over its permission rows."""
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Capability, Role, RoleCapability, User
from apps.accounts.testing import skip_password_prompt


class RolePageSearchTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        self.addCleanup(cache.clear)
        Capability.objects.create(code="vm.manage", label="Manage VMs", category="admin")
        Capability.objects.create(code="ticket.view", label="View tickets", category="tickets")
        self.role = Role.objects.create(name="BDA", slug="bda")
        self.client.force_login(User.objects.create_superuser("root", password="x"))

    def test_page_has_the_search_box_and_its_script(self):
        resp = self.client.get(reverse("accounts:role_detail", args=[self.role.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="capSearch"')
        self.assertContains(resp, "js/roles.js")

    def test_every_permission_row_is_still_listed_for_the_search_to_filter(self):
        resp = self.client.get(reverse("accounts:role_detail", args=[self.role.pk]))
        self.assertContains(resp, "Manage VMs")
        self.assertContains(resp, "ticket.view")
