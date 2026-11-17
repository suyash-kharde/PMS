"""The VMs tile on the dashboard is only for people holding vm.manage."""
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Capability, Role, RoleCapability, User
from apps.accounts.permissions import invalidate
from apps.accounts.testing import skip_password_prompt
from apps.vms.models import VM


class DashboardVmTileTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        self.addCleanup(cache.clear)
        VM.objects.create(name="web-1", host="10.0.0.1")

    def _page(self, grant):
        role = Role.objects.create(name="R%s" % grant, slug="r%s" % grant)
        if grant:
            cap, _ = Capability.objects.get_or_create(code="vm.manage", defaults={"label": "vm"})
            RoleCapability.objects.create(role=role, capability=cap, scope="all")
        invalidate()
        user = User.objects.create_user("u%s" % grant, password="x", role=role)
        self.client.force_login(user)
        return self.client.get(reverse("core:dashboard")).content.decode()

    def test_tile_hidden_without_the_permission(self):
        self.assertNotIn('<span class="stat-label">VMs</span>', self._page(False))

    def test_tile_shown_with_the_permission(self):
        self.assertIn('<span class="stat-label">VMs</span>', self._page(True))
