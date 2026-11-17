"""The tickets list has no Project or Type column (the ticket key already starts with the project key)."""
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.testing import skip_password_prompt
from apps.projects.models import Project

from .models import Ticket


class TicketListColumnsTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        self.addCleanup(cache.clear)
        self.root = User.objects.create_superuser("root", password="x")
        self.project = Project.objects.create(name="Platform", key="PLAT")
        self.ticket = Ticket.objects.create(project=self.project, title="Login 500s", reporter=self.root)
        self.client.force_login(self.root)

    def test_list_has_no_project_column(self):
        page = self.client.get(reverse("tickets:list")).content.decode()
        head = page.split("<thead>", 1)[1].split("</thead>", 1)[0]
        self.assertNotIn("<th>Project</th>", head)
        self.assertNotIn("<th>Type</th>", head)
        self.assertIn("<th>Key</th><th>Title</th>", head)
        self.assertIn("<th>Assignee</th><th>Raised by</th><th>Date</th>", head)
        row = page.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
        self.assertEqual(row.count("<td"), 7)                       # key, title, status, priority, assignee, raised by, date
        self.assertIn(self.ticket.reporter.display_name, row)       # who raised it
        self.assertNotIn("tkt-type", row)
        self.assertIn("PLAT-1", row)                                # the project is still visible in the key

    def test_my_tickets_page_keeps_its_columns(self):
        page = self.client.get(reverse("tickets:mine")).content.decode()
        self.assertIn("PLAT-1", page)
        self.assertIn("tkt-type", page)

    def test_long_titles_are_cut_short_but_keep_the_full_text_on_hover(self):
        from pathlib import Path

        long_title = "A very long ticket title that would otherwise stretch the Title column across the whole page"
        Ticket.objects.filter(pk=self.ticket.pk).update(title=long_title)
        page = self.client.get(reverse("tickets:list")).content.decode()
        self.assertIn('class="grow tkt-title ', page)
        self.assertIn(f'title="{long_title}"', page)                       # hover shows everything
        css = (Path(__file__).resolve().parents[2] / "static" / "css" / "main.css").read_text()
        self.assertIn(".tkt-title{display:inline-block;max-width:260px;overflow:hidden;text-overflow:ellipsis", css)
