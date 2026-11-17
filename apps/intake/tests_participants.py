"""The People tab no longer shows or asks for Allocation; adding people still works."""
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Capability, Role, RoleCapability, User
from apps.accounts.permissions import invalidate
from apps.accounts.testing import skip_password_prompt
from apps.projects.models import Project, ProjectMembership


class ParticipantsAllocationRemovedTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        self.addCleanup(cache.clear)
        role = Role.objects.create(name="Lead", slug="lead")
        for code in ("participant.view", "project.edit", "project.view", "user.create"):
            cap, _ = Capability.objects.get_or_create(code=code, defaults={"label": code})
            RoleCapability.objects.create(role=role, capability=cap, scope="all")
        invalidate()
        self.lead = User.objects.create_user("lead", password="x", role=role)
        self.dev = User.objects.create_user("dev", password="x", role=role)
        self.project = Project.objects.create(name="Data Lake", key="DLC", owner=self.lead)
        self.project.members.add(self.lead)
        self.client.force_login(self.lead)

    def test_people_tab_shows_no_allocation(self):
        resp = self.client.get(reverse("intake:participants", args=[self.project.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "llocation")
        self.assertNotContains(resp, "100%")

    def test_adding_an_existing_user_still_works(self):
        resp = self.client.post(
            reverse("intake:participant_create", args=[self.project.pk]),
            {"user": self.dev.pk, "side": "internal", "position": "Developer"},
        )
        self.assertEqual(resp.status_code, 302)
        member = ProjectMembership.objects.get(project=self.project, user=self.dev)
        self.assertEqual(member.position, "Developer")
        self.assertEqual(member.allocation, 100)  # column kept, default applies
