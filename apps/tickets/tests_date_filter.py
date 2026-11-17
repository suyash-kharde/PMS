"""The tickets list can be filtered by the date a ticket was raised (From / To, both inclusive)."""
from datetime import datetime

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.testing import skip_password_prompt
from apps.projects.models import Project

from .models import Ticket


def at(day, hour=12):
    return timezone.make_aware(datetime(2026, 10, day, hour, 0))


class TicketDateFilterTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        self.addCleanup(cache.clear)
        root = User.objects.create_superuser("root", password="x")
        project = Project.objects.create(name="Platform", key="PLAT")
        for title, day, hour in [("early", 1, 9), ("mid", 10, 23), ("late", 20, 0), ("last", 31, 23)]:
            ticket = Ticket.objects.create(project=project, title=title, reporter=root)
            Ticket.objects.filter(pk=ticket.pk).update(created_at=at(day, hour))
        self.client.force_login(root)

    def titles(self, **params):
        resp = self.client.get(reverse("tickets:list"), {"status": "all", **params})
        self.assertEqual(resp.status_code, 200)
        return {t.title for t in resp.context["tickets"]}

    def test_no_dates_shows_everything(self):
        self.assertEqual(self.titles(), {"early", "mid", "late", "last"})

    def test_from_and_to_are_inclusive_whole_days(self):
        self.assertEqual(self.titles(date_from="2026-10-10", date_to="2026-10-20"), {"mid", "late"})

    def test_only_from_or_only_to(self):
        self.assertEqual(self.titles(date_from="2026-10-20"), {"late", "last"})
        self.assertEqual(self.titles(date_to="2026-10-10"), {"early", "mid"})

    def test_a_single_day(self):
        self.assertEqual(self.titles(date_from="2026-10-10", date_to="2026-10-10"), {"mid"})

    def test_dates_entered_the_wrong_way_round_still_work(self):
        self.assertEqual(self.titles(date_from="2026-10-20", date_to="2026-10-10"), {"mid", "late"})

    def test_junk_dates_are_ignored_not_an_error(self):
        self.assertEqual(self.titles(date_from="not-a-date", date_to="2026-13-45"), {"early", "mid", "late", "last"})

    def test_filter_bar_has_the_date_boxes_and_keeps_the_values_in_the_board_view(self):
        for view in ("list", "board"):
            with self.subTest(view=view):
                page = self.client.get(reverse("tickets:list"),
                                       {"view": view, "date_from": "2026-10-10", "date_to": "2026-10-20"}).content.decode()
                self.assertIn('name="date_from" value="2026-10-10"', page)
                self.assertIn('name="date_to" value="2026-10-20"', page)


class TicketListStillNeedsLoginTests(TestCase):
    def test_anonymous_visitors_are_sent_to_sign_in(self):
        resp = self.client.get(reverse("tickets:list"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp["Location"])
