"""The Solved status: its handoff is worked out (waiting for whoever raised the ticket to test it), and an (i) icon
shows who assigned the ticket and that final status. Nobody types the note."""
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.testing import skip_password_prompt
from apps.projects.models import Project

from .models import CLOSED_STATUSES, OPEN_STATUSES, TICKET_STATUS, Ticket, TicketEvent


class SolvedStatusTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        self.addCleanup(cache.clear)
        self.root = User.objects.create_superuser("root", password="x", first_name="Renuka", last_name="Opalkar")
        self.dev = User.objects.create_user("dev", password="x", first_name="Dev", last_name="Eloper")
        self.project = Project.objects.create(name="Platform", key="PLAT")
        self.ticket = Ticket.objects.create(project=self.project, title="Login 500s", reporter=self.root, assignee=self.dev)
        TicketEvent.log(self.ticket, self.root, "assigned to Dev Eloper")
        self.client.force_login(self.root)

    def solve(self):
        return self.client.post(reverse("tickets:transition", args=[self.ticket.pk]), {"status": "solved"})

    def test_solved_is_a_status_choice_and_still_counts_as_open(self):
        self.assertIn(("solved", "Solved"), TICKET_STATUS)
        self.assertIn("solved", OPEN_STATUSES)
        self.assertNotIn("solved", CLOSED_STATUSES)

    def test_marking_solved_records_a_handoff_to_whoever_raised_the_ticket(self):
        self.assertEqual(self.solve().status_code, 302)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, "solved")
        self.assertEqual(self.ticket.handoff_status, "Waiting for Renuka Opalkar Testing/approval")
        self.assertTrue(self.ticket.events.filter(verb__contains="Waiting for Renuka Opalkar Testing/approval").exists())

    def test_the_handoff_follows_the_creator_not_a_typed_note(self):
        tester = User.objects.create_user("sadhna", password="x", first_name="Sadhna", last_name="Rao")
        Ticket.objects.filter(pk=self.ticket.pk).update(reporter=tester)
        self.client.post(reverse("tickets:transition", args=[self.ticket.pk]),
                         {"status": "solved", "handoff_status": "ignored typed text"})
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.handoff_status, "Waiting for Sadhna Rao Testing/approval")

    def test_tooltip_shows_only_the_final_status(self):
        self.solve()
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.solved_tooltip, "Final status: Waiting for Renuka Opalkar Testing/approval")

    def test_without_an_assignment_record_the_reporter_is_shown_as_assigner(self):
        TicketEvent.objects.filter(ticket=self.ticket).delete()
        self.solve()
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.assigned_by, "Renuka Opalkar")

    def test_the_icon_shows_only_for_solved_tickets(self):
        url = reverse("tickets:detail", args=[self.ticket.pk])
        self.assertNotContains(self.client.get(url), 'data-info-label="Solved"')
        self.solve()
        page = self.client.get(url)
        self.assertContains(page, 'data-info-label="Solved"')
        self.assertContains(page, "Waiting for Renuka Opalkar Testing/approval")
        listing = self.client.get(reverse("tickets:list"), {"status": "all"})
        self.assertContains(listing, 'data-info-label="Solved"')

    def test_the_handoff_note_is_cleared_when_it_leaves_solved(self):
        self.solve()
        self.client.post(reverse("tickets:transition", args=[self.ticket.pk]), {"status": "resolved"})
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.handoff_status, "")
        self.assertIsNotNone(self.ticket.resolved_at)

    def test_inline_dropdown_sets_solved_with_the_automatic_handoff(self):
        import json
        resp = self.client.post(reverse("tickets:api_patch", args=[self.ticket.pk]),
                                data=json.dumps({"status": "solved"}), content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        self.ticket.refresh_from_db()
        self.assertEqual((self.ticket.status, self.ticket.handoff_status),
                         ("solved", "Waiting for Renuka Opalkar Testing/approval"))

    def test_no_typed_note_box_and_no_prompt(self):
        from pathlib import Path
        page = self.client.get(reverse("tickets:detail", args=[self.ticket.pk])).content.decode()
        self.assertNotIn('name="handoff_status"', page)
        js = (Path(__file__).resolve().parents[2] / "static" / "js" / "tickets.js").read_text()
        self.assertNotIn("window.prompt", js)

    def test_solved_appears_in_the_open_filter(self):
        self.solve()
        resp = self.client.get(reverse("tickets:list"))   # default filter = open
        self.assertIn(self.ticket, list(resp.context["tickets"]))

    def test_tooltip_is_escaped(self):
        tricky = User.objects.create_user("tricky", password="x", first_name='<script>alert(1)</script>', last_name='"x"')
        Ticket.objects.filter(pk=self.ticket.pk).update(reporter=tricky)
        self.solve()
        page = self.client.get(reverse("tickets:detail", args=[self.ticket.pk])).content.decode()
        self.assertNotIn("<script>alert(1)</script>", page)

    def test_resolve_form_applies_nothing_until_an_action_is_chosen(self):
        page = self.client.get(reverse("tickets:detail", args=[self.ticket.pk])).content.decode()
        form = page.split('action="%s"' % reverse("tickets:transition", args=[self.ticket.pk]), 1)[1]
        select = form.split("<select ", 1)[1].split("</select>", 1)[0]
        self.assertIn("required", select.split(">", 1)[0])                       # the browser blocks Apply with no choice
        self.assertIn('<option value="" selected disabled>Choose an action', select)
        self.assertLess(select.index('value=""'), select.index('value="resolved"'))   # the empty choice is the default

    def test_posting_the_form_without_a_choice_changes_nothing(self):
        self.client.post(reverse("tickets:transition", args=[self.ticket.pk]), {"status": "", "handoff_status": ""})
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, "new")

    def test_a_solved_ticket_can_be_set_back_to_new_from_the_dropdown(self):
        import json
        self.solve()
        resp = self.client.post(reverse("tickets:api_patch", args=[self.ticket.pk]),
                                data=json.dumps({"status": "new"}), content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        self.ticket.refresh_from_db()
        self.assertEqual((self.ticket.status, self.ticket.handoff_status), ("new", ""))
        page = self.client.get(reverse("tickets:detail", args=[self.ticket.pk]))
        self.assertNotContains(page, 'data-info-label="Solved"')
