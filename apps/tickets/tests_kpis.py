from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.projects.models import Project

from .models import Ticket


class TicketKpiCardTests(TestCase):
    """The six cards on the tickets page count every visible ticket, whatever the list filters say."""

    def setUp(self):
        self.admin = User.objects.create_superuser("adm", "a@x.com", "pw", must_change_password=False)
        self.project = Project.objects.create(key="KP", name="KPI project")
        mk = lambda **kw: Ticket.objects.create(project=self.project, title="t", reporter=self.admin, **kw)
        old = timezone.now() - timedelta(days=10)
        self.a = mk(status="new")                                   # raised today, unassigned
        self.b = mk(status="in_progress", assignee=self.admin)
        self.c = mk(status="blocked", assignee=self.admin)
        self.d = mk(status="resolved", assignee=self.admin, resolved_at=timezone.now())
        Ticket.objects.filter(pk__in=[self.b.pk, self.c.pk, self.d.pk]).update(created_at=old)
        self.client.force_login(self.admin)

    def test_counts_ignore_the_filters(self):
        for params in ({}, {"status": "blocked"}, {"q": "nothing-matches"}):
            k = self.client.get(reverse("tickets:list"), params, HTTP_HOST="planwise.augtrans.com").context["kpis"]
            self.assertEqual(k, {"raised_today": 1, "resolved_today": 1, "pending": 3,
                                 "unassigned": 1, "in_progress": 1, "blocked": 1})

    def test_resolved_today_card_filters_the_list(self):
        r = self.client.get(reverse("tickets:list"), {"status": "all", "resolved": "today"}, HTTP_HOST="planwise.augtrans.com")
        self.assertEqual([t.pk for t in r.context["tickets"]], [self.d.pk])
        self.assertContains(r, "Resolved today")
        r = self.client.get(reverse("tickets:list"), {"view": "board"}, HTTP_HOST="planwise.augtrans.com")
        self.assertContains(r, "Raised today")
