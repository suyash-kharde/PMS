"""The whole project card is clickable: the title link is stretched over the card, the customer link stays separate."""
from pathlib import Path

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.testing import skip_password_prompt

from .models import Customer, Project


class ProjectCardClickTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        self.addCleanup(cache.clear)
        self.root = User.objects.create_superuser("root", password="x")
        self.project = Project.objects.create(name="AI-HUB", key="A", customer=Customer.objects.create(name="Augmented"))
        self.client.force_login(self.root)

    def test_card_has_one_project_link_and_a_separate_customer_link(self):
        page = self.client.get(reverse("projects:list"), {"view": "grid"}).content.decode()
        card = page.split('<article class="project-card"', 1)[1].split("</article>", 1)[0]
        self.assertEqual(card.count(self.project.get_absolute_url()), 1)   # the title link: the one the card stretches
        self.assertIn('class="link"', card)                                # customer link, kept above the stretched title link

    def test_stylesheet_stretches_the_title_link_over_the_card(self):
        css = (Path(__file__).resolve().parents[2] / "static" / "css" / "main.css").read_text()
        self.assertIn(".project-card h3 a::after", css)
        self.assertIn(".project-card a.link{position:relative;z-index:2}", css)
