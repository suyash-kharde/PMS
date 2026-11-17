"""Deleting a project's BRD: permission-gated, POST-only, refuses a frozen BRD, and cleans up after itself."""
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Capability, Role, RoleCapability, User
from apps.accounts.permissions import invalidate
from apps.accounts.testing import skip_password_prompt
from apps.projects.models import Project
from apps.rag.models import Chunk

from .models import BRD, BRDVersion, TimelineEntry


def _role(name, codes):
    role = Role.objects.create(name=name, slug=name.lower())
    for code in codes:
        cap, _ = Capability.objects.get_or_create(code=code, defaults={"label": code})
        RoleCapability.objects.create(role=role, capability=cap, scope="all")
    invalidate()
    return role


class BrdDeleteTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        self.addCleanup(cache.clear)
        base = ("project.view", "brd.view")
        self.admin = User.objects.create_user("boss", password="x", role=_role("Boss", base + ("brd.delete",)))
        self.viewer = User.objects.create_user("viewer", password="x", role=_role("Viewer", base))
        self.project = Project.objects.create(name="Data Lake", key="DLC", owner=self.admin)
        self.project.members.add(self.admin, self.viewer)
        self.brd = BRD.objects.create(project=self.project, title="DLC BRD")
        self.brd.add_version(markdown="# v1", summary="first")
        self.brd.add_version(markdown="# v2", summary="second")
        self.entry = TimelineEntry.objects.create(project=self.project, title="Kick-off", brd_applied=True)
        Chunk.objects.create(project=self.project, source_type="brd", source_id=self.brd.pk, text="old brd text")
        Chunk.objects.create(project=self.project, source_type="task", source_id=1, text="a task")
        self.url = reverse("intake:brd_delete", args=[self.project.pk])

    def test_permitted_user_deletes_every_version_and_tidies_up(self):
        self.client.force_login(self.admin)
        resp = self.client.post(self.url)
        self.assertRedirects(resp, reverse("intake:brd", args=[self.project.pk]), fetch_redirect_response=False)
        self.assertFalse(BRD.objects.filter(project=self.project).exists())
        self.assertFalse(BRDVersion.objects.filter(brd_id=self.brd.pk).exists())
        self.entry.refresh_from_db()
        self.assertFalse(self.entry.brd_applied)  # a later regenerate rebuilds from every entry
        self.assertFalse(Chunk.objects.filter(project=self.project, source_type="brd").exists())
        self.assertTrue(Chunk.objects.filter(project=self.project, source_type="task").exists())  # others untouched

    def test_without_the_permission_nothing_is_deleted(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.post(self.url).status_code, 403)
        self.assertTrue(BRD.objects.filter(project=self.project).exists())

    def test_get_does_not_delete(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertTrue(BRD.objects.filter(project=self.project).exists())

    def test_frozen_brd_must_be_unfrozen_first(self):
        BRD.objects.filter(pk=self.brd.pk).update(is_frozen=True)
        self.client.force_login(self.admin)
        self.client.post(self.url)
        self.assertTrue(BRD.objects.filter(pk=self.brd.pk).exists())

    def test_button_only_shows_for_people_who_may_delete(self):
        page = reverse("intake:brd", args=[self.project.pk])
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(page), "Delete BRD")
        self.client.force_login(self.viewer)
        self.assertNotContains(self.client.get(page), "Delete BRD")

    def test_deleting_when_there_is_no_brd_is_harmless(self):
        BRD.objects.filter(project=self.project).delete()
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(self.url).status_code, 302)
