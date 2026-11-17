"""The "Anyone" person filters are searchable dropdowns that keep their "Anyone" choice."""
from pathlib import Path

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.testing import skip_password_prompt
from apps.projects.models import Project


class PeopleFilterDropdownTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        self.addCleanup(cache.clear)
        self.root = User.objects.create_superuser("root", password="x", first_name="Renuka", last_name="Opalkar")
        self.project = Project.objects.create(name="Platform", key="PLAT", owner=self.root)
        self.project.members.add(self.root)
        self.client.force_login(self.root)

    def _select(self, url, name):
        page = self.client.get(url).content.decode()
        return page.split(f'<select name="{name}"', 1)[1].split("</select>", 1)[0]

    def test_ticket_list_assignee_filter(self):
        select = self._select(reverse("tickets:list"), "assignee")
        self.assertIn('data-search="1" data-keep-blank="1"', select)
        self.assertIn("Anyone", select)
        self.assertIn("Unassigned", select)
        self.assertIn("Renuka Opalkar", select)          # people are listed by name, so they can be searched

    def test_project_board_assignee_filter(self):
        select = self._select(reverse("projects:detail", args=[self.project.pk]), "assignee")
        self.assertIn('data-search="1" data-keep-blank="1"', select)
        self.assertIn("Renuka Opalkar", select)

    def test_activity_person_filter(self):
        select = self._select(reverse("core:activity_list"), "actor")
        self.assertIn('data-search="1" data-keep-blank="1"', select)

    def test_script_keeps_the_blank_choice(self):
        js = (Path(__file__).resolve().parents[2] / "static" / "js" / "search-select.js").read_text()
        self.assertIn("keepBlank", js)
