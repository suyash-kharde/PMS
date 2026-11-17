"""Dashboard graphs (apps/core/analytics.py)."""
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Capability, Role, RoleCapability, User
from apps.accounts.testing import skip_password_prompt
from apps.projects.models import Project
from apps.tickets.models import Ticket


class DashboardGraphTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        self.admin = User.objects.create_superuser("boss", password="x")
        self.project = Project.objects.create(key="P1", name="One", status="active", owner=self.admin)
        Ticket.objects.create(project=self.project, number=1, title="Open one", reporter=self.admin, status="new")
        Ticket.objects.create(project=self.project, number=2, title="Done one", reporter=self.admin, status="resolved")

    def test_dashboard_shows_the_graph_sections_and_data(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("core:dashboard"))
        self.assertEqual(resp.status_code, 200)
        for text in ("Tickets by Status", "Tickets per project", "Projects by status",
                     "Present, absent and on leave", 'id="dash-data"'):
            self.assertContains(resp, text)
        self.assertNotContains(resp, "Solved this week")  # the ticket summary cards were removed
        self.assertNotContains(resp, "Hours per week, by project")  # the Hours charts were removed
        self.assertNotContains(resp, "Requirements by status")  # the Sprints charts were removed

    def test_filters_are_accepted_and_bad_ones_ignored(self):
        self.client.force_login(self.admin)
        for query in ("?range=7", "?range=custom&from=2026-01-01&to=2026-02-01", f"?project={self.project.pk}",
                      "?range=nonsense&project=abc&customer=zzz"):
            self.assertEqual(self.client.get(reverse("core:dashboard") + query).status_code, 200)

    def test_ticket_graphs_need_the_ticket_permission(self):
        member = User.objects.create_user("pat", password="x")
        self.client.force_login(member)
        resp = self.client.get(reverse("core:dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Tickets by Status")
        self.assertContains(resp, "Projects by status")

    def test_person_only_sees_tickets_they_are_allowed_to(self):
        cap, _ = Capability.objects.get_or_create(code="ticket.view", defaults={"label": "View tickets", "category": "tickets"})
        role = Role.objects.create(name="Viewer", slug="viewer", rank=50)
        RoleCapability.objects.create(role=role, capability=cap, scope="own")
        other = User.objects.create_user("lee", password="x", role=role)  # on no project, raised nothing
        self.client.force_login(other)
        resp = self.client.get(reverse("core:dashboard"))
        self.assertContains(resp, "Tickets by Status")
        self.assertContains(resp, '"empty": true')  # nothing visible to them -> empty graphs, not someone else's data

    def test_a_failure_in_the_graphs_never_breaks_the_dashboard(self):
        from unittest import mock

        self.client.force_login(self.admin)
        with mock.patch("apps.core.analytics.build", side_effect=RuntimeError("boom")):
            resp = self.client.get(reverse("core:dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'id="dash-data"')


class TicketsByStatusCardTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        self.admin = User.objects.create_superuser("boss", password="x")
        self.project = Project.objects.create(key="P1", name="One", status="active", owner=self.admin)
        for n, status in enumerate(["resolved", "resolved", "resolved", "new", "in_progress"], start=1):
            Ticket.objects.create(project=self.project, number=n, title=f"T{n}", reporter=self.admin, status=status)

    def _get(self, **params):
        from django.utils import timezone

        today = timezone.localdate()
        query = {"from": (today.replace(year=today.year - 1)).isoformat(), "to": today.isoformat(), **params}
        return self.client.get(reverse("core:tickets_by_status"), query)

    def test_statuses_come_from_the_data_biggest_first_with_a_total(self):
        self.client.force_login(self.admin)
        data = self._get().json()
        self.assertEqual(data["total"], 5)
        self.assertEqual([i["key"] for i in data["items"]][0], "resolved")
        self.assertEqual({i["key"]: i["value"] for i in data["items"]}, {"resolved": 3, "new": 1, "in_progress": 1})
        self.assertTrue(all(i["color"].startswith("#") for i in data["items"]))

    def test_a_single_day_is_a_valid_range(self):
        from django.utils import timezone

        self.client.force_login(self.admin)
        today = timezone.localdate().isoformat()
        data = self._get(**{"from": today, "to": today}).json()
        self.assertEqual(data["total"], 5)  # the five tickets in setUp were raised today
        self.assertEqual((data["from"], data["to"]), (today, today))

    def test_a_range_with_no_tickets_is_empty_not_an_error(self):
        self.client.force_login(self.admin)
        data = self._get(**{"from": "2001-01-01", "to": "2001-01-31"}).json()
        self.assertEqual((data["total"], data["items"]), (0, []))

    def test_bad_dates_are_refused(self):
        self.client.force_login(self.admin)
        for params in ({"from": "nope", "to": "2026-01-01"}, {"from": "2026-02-01", "to": "2026-01-01"}, {"from": "2000-01-01", "to": "2026-01-01"}):
            self.assertEqual(self._get(**params).status_code, 400)

    def test_needs_the_ticket_permission(self):
        member = User.objects.create_user("pat", password="x")
        self.client.force_login(member)
        self.assertEqual(self._get().status_code, 403)

    def test_the_dashboard_card_has_the_date_range_and_no_other_filter_bar(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("core:dashboard"))
        self.assertContains(page, "Tickets by Status")
        self.assertContains(page, 'id="dg-range-t-status"')
        self.assertNotContains(page, "All customers")


class TicketsPerProjectCardTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        self.admin = User.objects.create_superuser("boss", password="x")
        self.alpha = Project.objects.create(key="ALP", name="Alpha", status="active", owner=self.admin)
        self.beta = Project.objects.create(key="BET", name="Beta", status="active", owner=self.admin)
        self.idle = Project.objects.create(key="IDL", name="Idle", status="active", owner=self.admin)
        for n in range(1, 4):
            Ticket.objects.create(project=self.alpha, number=n, title=f"A{n}", reporter=self.admin)
        Ticket.objects.create(project=self.beta, number=1, title="B1", reporter=self.admin)

    def _get(self, **params):
        from django.utils import timezone

        today = timezone.localdate()
        query = {"from": today.replace(year=today.year - 1).isoformat(), "to": today.isoformat(), **params}
        return self.client.get(reverse("core:tickets_per_project"), query)

    def test_busiest_projects_first_and_only_projects_with_tickets(self):
        self.client.force_login(self.admin)
        data = self._get().json()
        self.assertEqual([(i["label"], i["value"]) for i in data["items"]], [("ALP", 3), ("BET", 1)])
        self.assertEqual(data["total"], 4)
        self.assertIn(f"project={self.alpha.pk}", data["items"][0]["href"])

    def test_only_ten_projects_are_returned(self):
        self.client.force_login(self.admin)
        for n in range(12):
            project = Project.objects.create(key=f"X{n}", name=f"X{n}", status="active", owner=self.admin)
            Ticket.objects.create(project=project, number=1, title="t", reporter=self.admin)
        self.assertEqual(len(self._get().json()["items"]), 10)

    def test_bad_dates_and_missing_permission(self):
        self.client.force_login(self.admin)
        self.assertEqual(self._get(**{"from": "x"}).status_code, 400)
        self.client.force_login(User.objects.create_user("pat", password="x"))
        self.assertEqual(self._get().status_code, 403)

    def test_dashboard_has_the_card(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("core:dashboard"))
        self.assertContains(page, "Tickets per project")
        self.assertContains(page, 'id="dg-range-t-project"')
