"""The ticket key has an "i" button that reveals the project's full name (hover: tooltip; click: popover)."""
from pathlib import Path

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.testing import skip_password_prompt
from apps.projects.models import Project

from .models import Ticket


class TicketProjectInfoTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        self.addCleanup(cache.clear)
        root = User.objects.create_superuser("root", password="x")
        project = Project.objects.create(name='Mod "Pen" <Pot> Portal', key="MPP")
        Ticket.objects.create(project=project, title="Login 500s", reporter=root)
        self.client.force_login(root)

    def test_list_and_my_tickets_pages_show_the_info_button_and_load_its_script(self):
        for name in ("tickets:list", "tickets:mine"):
            with self.subTest(page=name):
                page = self.client.get(reverse(name)).content.decode()
                self.assertIn('class="info-btn"', page)
                self.assertIn("js/info-pop.js", page)

    def test_project_name_is_in_the_tooltip_and_escaped(self):
        page = self.client.get(reverse("tickets:list")).content.decode()
        self.assertIn('title="Mod &quot;Pen&quot; &lt;Pot&gt; Portal"', page)   # hover shows the full name, safely escaped
        self.assertNotIn("<Pot>", page)

    def test_script_and_styles_are_present(self):
        root = Path(__file__).resolve().parents[2] / "static"
        self.assertIn("info-pop", (root / "js" / "info-pop.js").read_text())
        self.assertIn(".info-pop{", (root / "css" / "main.css").read_text())

    def test_popup_is_attached_to_the_page_so_a_scrolling_table_cannot_clip_it(self):
        root = Path(__file__).resolve().parents[2] / "static"
        js = (root / "js" / "info-pop.js").read_text()
        css = (root / "css" / "main.css").read_text()
        self.assertIn("document.body.appendChild(pop)", js)      # not inside the table cell
        self.assertIn("window.innerHeight", js)                   # flips above the button near the bottom edge
        self.assertIn(".info-pop{position:fixed;z-index:1000;", css)

    def test_project_popup_carries_only_the_project_name(self):
        page = self.client.get(reverse("tickets:list")).content.decode()
        button = page.split('class="info-btn"', 1)[1].split("</button>", 1)[0]
        self.assertNotIn("data-info-url", button)      # no "Open project" link
        self.assertNotIn("data-info-label", button)    # no "PROJECT" heading: just the name
        js = (Path(__file__).resolve().parents[2] / "static" / "js" / "info-pop.js").read_text()
        self.assertIn("if (btn.dataset.infoLabel)", js)
