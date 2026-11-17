from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Capability, Role, RoleCapability, User


class ProjectExcelAdminOnlyTests(TestCase):
    """The Projects page's Download Project List button is for admins only (Administrator role or superuser)."""

    def setUp(self):
        cache.clear()  # permissions are cached per user + role
        view = Capability.objects.create(code="project.view", label="View projects", category="projects")
        member = Role.objects.create(name="Member", slug="member")
        admin = Role.objects.create(name="Administrator", slug="admin")
        manager = Role.objects.create(name="Project Manager", slug="manager")
        for role in (member, admin, manager):
            RoleCapability.objects.create(role=role, capability=view, scope="all")
        self.member = User.objects.create_user("m", password="x", role=member)
        self.manager = User.objects.create_user("pm", password="x", role=manager)
        self.admin = User.objects.create_user("a", password="x", role=admin)
        self.root = User.objects.create_superuser("root", "r@x.com", "x")
        User.objects.update(must_change_password=False)

    def test_non_admins_see_no_button_and_are_refused(self):
        for user in (self.member, self.manager):
            self.client.force_login(user)
            self.assertNotContains(self.client.get(reverse("projects:list")), "Download Project List")
            self.assertEqual(self.client.get(reverse("projects:export")).status_code, 403)

    def test_admins_see_the_button_and_can_download(self):
        for user in (self.admin, self.root):
            self.client.force_login(user)
            self.assertContains(self.client.get(reverse("projects:list")), "Download Project List")
            r = self.client.get(reverse("projects:export"))
            self.assertEqual(r.status_code, 200)
            self.assertIn("spreadsheet", r["Content-Type"])
