"""A project is visible only to people on it (owner or member); administrators see every project.
Role scopes (own / team / all) must never widen that for anyone else."""
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Capability, Role, RoleCapability, User
from apps.accounts.permissions import invalidate
from apps.accounts.testing import skip_password_prompt
from apps.sprints.models import Sprint
from apps.sprints.selectors import visible_sprints
from apps.tickets.models import Ticket
from apps.tickets.selectors import visible_tickets

from .models import Customer, Project, Task
from .selectors import visible_customers, visible_projects, visible_tasks

CAPS = ("project.view", "task.view", "ticket.view", "sprint.view", "customer.view")


def _grant(role, scope, codes=CAPS):
    for code in codes:
        capability, _ = Capability.objects.get_or_create(code=code, defaults={"label": code})
        RoleCapability.objects.update_or_create(role=role, capability=capability, defaults={"scope": scope})
    invalidate()


class ProjectMembershipVisibilityTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        self.addCleanup(cache.clear)  # cached permissions must not leak into other tests
        self.bda = Role.objects.create(name="BDA", slug="bda")
        self.admin_role = Role.objects.create(name="Administrator", slug="administrator")
        _grant(self.admin_role, "all", codes=("role.manage",) + CAPS)
        _grant(self.bda, "own")

        self.renuka = User.objects.create_user("renuka", password="x", role=self.bda)
        self.sashi = User.objects.create_user("sashi", password="x", role=self.bda)
        self.admin = User.objects.create_user("boss", password="x", role=self.admin_role)

        self.acme = Customer.objects.create(name="Acme")
        self.mine = Project.objects.create(name="Mine", key="MINE", customer=self.acme, owner=self.renuka)
        self.mine.members.add(self.renuka)
        self.theirs = Project.objects.create(name="Sashis", key="SASH", customer=self.acme, owner=self.sashi)
        self.theirs.members.add(self.sashi)
        self.internal = Project.objects.create(name="Internal Tools", key="INT", owner=self.sashi)
        self.internal.members.add(self.sashi)

    def _keys(self, user):
        return set(visible_projects(user).values_list("key", flat=True))

    def test_only_own_projects_for_every_scope(self):
        for scope in ("own", "team", "all"):
            with self.subTest(scope=scope):
                _grant(self.bda, scope)
                self.assertEqual(self._keys(self.renuka), {"MINE"})
                self.assertEqual(self._keys(self.sashi), {"SASH", "INT"})

    def test_being_sashis_manager_or_teammate_does_not_reveal_her_projects(self):
        _grant(self.bda, "team")
        self.sashi.manager = self.renuka
        self.sashi.save()
        self.assertEqual(self._keys(self.renuka), {"MINE"})

    def test_creating_a_project_for_someone_else_hides_it_from_the_creator(self):
        _grant(self.bda, "all")
        handed_over = Project.objects.create(name="Handed over", key="HAND", customer=self.acme, owner=self.sashi)
        handed_over.members.add(self.sashi)
        self.assertNotIn("HAND", self._keys(self.renuka))

    def test_member_sees_project_with_or_without_a_customer(self):
        self.internal.members.add(self.renuka)
        self.assertIn("INT", self._keys(self.renuka))
        self.theirs.members.add(self.renuka)
        self.assertIn("SASH", self._keys(self.renuka))

    def test_removing_a_member_hides_the_project_again(self):
        self.theirs.members.add(self.renuka)
        self.assertIn("SASH", self._keys(self.renuka))
        self.theirs.members.remove(self.renuka)
        self.assertNotIn("SASH", self._keys(self.renuka))

    def test_administrators_and_superusers_see_everything(self):
        self.assertEqual(self._keys(self.admin), {"MINE", "SASH", "INT"})
        root = User.objects.create_superuser("root", password="x")
        self.assertEqual(self._keys(root), {"MINE", "SASH", "INT"})

    def test_tasks_follow_project_membership(self):
        _grant(self.bda, "all")
        Task.objects.create(project=self.theirs, title="Sashi only")
        Task.objects.create(project=self.mine, title="Mine")
        titles = set(visible_tasks(self.renuka).values_list("title", flat=True))
        self.assertEqual(titles, {"Mine"})

    def test_tickets_follow_project_membership(self):
        _grant(self.bda, "all")
        Ticket.objects.create(project=self.theirs, title="Hidden", reporter=self.sashi)
        Ticket.objects.create(project=self.mine, title="Shown", reporter=self.renuka)
        titles = set(visible_tickets(self.renuka).values_list("title", flat=True))
        self.assertEqual(titles, {"Shown"})
        self.assertEqual(visible_tickets(self.admin).count(), 2)

    def test_sprints_follow_project_membership(self):
        _grant(self.bda, "all")
        Sprint.objects.create(project=self.theirs, name="Hidden sprint")
        Sprint.objects.create(project=self.mine, name="Shown sprint")
        names = set(visible_sprints(self.renuka).values_list("name", flat=True))
        self.assertEqual(names, {"Shown sprint"})

    def test_customers_only_through_visible_projects(self):
        _grant(self.bda, "all")
        other = Customer.objects.create(name="Hindalco")
        Project.objects.create(name="H1", key="H1", customer=other, owner=self.sashi).members.add(self.sashi)
        names = set(visible_customers(self.renuka).values_list("name", flat=True))
        self.assertEqual(names, {"Acme"})
        self.assertEqual(visible_customers(self.admin).count(), 2)

    def test_project_pages_hide_other_peoples_projects(self):
        _grant(self.bda, "all")
        self.client.force_login(self.renuka)
        self.assertEqual(self.client.get(reverse("projects:detail", args=[self.theirs.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("projects:detail", args=[self.internal.pk])).status_code, 404)
        listing = self.client.get(reverse("projects:list"))
        self.assertEqual(listing.status_code, 200)
        self.assertNotContains(listing, "Sashis")
        self.assertNotContains(listing, "Internal Tools")
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("projects:detail", args=[self.theirs.pk])).status_code, 200)

    def test_customer_page_lists_only_people_on_visible_projects(self):
        _grant(self.bda, "all")
        self.client.force_login(self.renuka)
        resp = self.client.get(reverse("projects:customer_detail", args=[self.acme.pk]))
        self.assertEqual(resp.status_code, 200)
        people = {u.username for u in list(resp.context["our_people"]) + list(resp.context["client_people"])}
        self.assertNotIn("sashi", people)
