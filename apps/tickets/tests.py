import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

_MEDIA = tempfile.mkdtemp(prefix="tickets-test-")

from apps.accounts.models import Capability, Role, RoleCapability, User
from apps.accounts.testing import skip_password_prompt
from apps.notify.models import Notification
from apps.projects.models import Project

from .models import Ticket, TicketAttachment, TicketEvent
from .selectors import visible_tickets


def _cap(code, role, scope):
    capability, _ = Capability.objects.get_or_create(code=code, defaults={"label": code, "category": "tickets"})
    RoleCapability.objects.get_or_create(role=role, capability=capability, defaults={"scope": scope})


class TicketModelTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        self.project = Project.objects.create(name="Platform Rebuild", key="PLAT")

    def test_number_is_per_project_sequence(self):
        a = Ticket.objects.create(project=self.project, title="one")
        b = Ticket.objects.create(project=self.project, title="two")
        other = Project.objects.create(name="Mobile", key="MOB")
        c = Ticket.objects.create(project=other, title="three")
        self.assertEqual((a.number, b.number, c.number), (1, 2, 1))
        self.assertEqual(b.key, "PLAT-2")

    def test_apply_status_tracks_timestamps(self):
        ticket = Ticket.objects.create(project=self.project, title="bug")
        ticket.apply_status("resolved")
        ticket.save()
        self.assertIsNotNone(ticket.resolved_at)
        self.assertIsNone(ticket.closed_at)
        ticket.apply_status("closed")
        ticket.save()
        self.assertIsNotNone(ticket.closed_at)
        ticket.apply_status("new")
        ticket.save()
        self.assertIsNone(ticket.resolved_at)
        self.assertTrue(ticket.is_open)


@override_settings(MEDIA_ROOT=_MEDIA)
class TicketFlowTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA, ignore_errors=True)

    def setUp(self):
        skip_password_prompt(self)
        self.role = Role.objects.create(name="Staff", slug="staff")
        for code in ("ticket.view", "ticket.create", "ticket.edit", "ticket.triage", "ticket.comment"):
            _cap(code, self.role, "all")
        self.project = Project.objects.create(name="Platform Rebuild", key="PLAT")
        self.reporter = User.objects.create_user("rep", password="x", role=self.role)
        self.dev = User.objects.create_user("dev", password="x", role=self.role)
        self.project.members.add(self.reporter, self.dev)

    def test_raise_ticket_creates_event_and_notifies(self):
        self.project.owner = self.dev
        self.project.save()
        self.client.force_login(self.reporter)
        resp = self.client.post(
            reverse("tickets:create_for_project", args=[self.project.pk]),
            {"project": self.project.pk, "type": "bug", "title": "Login 500s", "priority": 3},
        )
        self.assertEqual(resp.status_code, 302)
        ticket = Ticket.objects.get(title="Login 500s")
        self.assertEqual(ticket.reporter, self.reporter)
        self.assertIn(self.reporter, ticket.watchers.all())
        self.assertTrue(TicketEvent.objects.filter(ticket=ticket).exists())
        self.assertTrue(Notification.objects.filter(user=self.dev, kind="ticket_new").exists())

    def test_create_with_attachments(self):
        self.client.force_login(self.reporter)
        png = SimpleUploadedFile("shot.png", b"\x89PNG\r\n\x1a\n" + b"0" * 40, content_type="image/png")
        log = SimpleUploadedFile("run.log", b"ERROR boom\n", content_type="text/plain")
        exe = SimpleUploadedFile("bad.exe", b"MZ", content_type="application/octet-stream")
        resp = self.client.post(
            reverse("tickets:create_for_project", args=[self.project.pk]),
            {"project": self.project.pk, "type": "bug", "title": "with files", "priority": 3,
             "attachments": [png, log, exe]},
        )
        self.assertEqual(resp.status_code, 302)
        ticket = Ticket.objects.get(title="with files")
        names = set(ticket.attachments.values_list("original_name", flat=True))
        self.assertEqual(names, {"shot.png", "run.log", "bad.exe"})  # any file type is accepted
        self.assertTrue(TicketAttachment.objects.get(original_name="shot.png").is_image)

    def test_penpot_and_other_unusual_files_can_be_attached(self):
        self.client.force_login(self.reporter)
        design = SimpleUploadedFile("mockup.penpot", b"PK\x03\x04" + b"0" * 64, content_type="application/octet-stream")
        noext = SimpleUploadedFile("README", b"hello", content_type="application/octet-stream")
        big = SimpleUploadedFile("huge.bin", b"0" * (10 * 1024 * 1024 + 1), content_type="application/octet-stream")
        resp = self.client.post(
            reverse("tickets:create_for_project", args=[self.project.pk]),
            {"project": self.project.pk, "type": "bug", "title": "odd files", "priority": 3,
             "attachments": [design, noext, big]},
        )
        self.assertEqual(resp.status_code, 302)
        ticket = Ticket.objects.get(title="odd files")
        names = set(ticket.attachments.values_list("original_name", flat=True))
        self.assertEqual(names, {"mockup.penpot", "README"})  # the size limit still applies

    def test_uploads_are_served_safely(self):
        """Any type can be stored, so risky ones download instead of running in the browser."""
        self.client.force_login(self.reporter)
        page = SimpleUploadedFile("evil.html", b"<script>alert(1)</script>", content_type="text/html")
        shot = SimpleUploadedFile("shot.png", b"\x89PNG\r\n\x1a\n" + b"0" * 40, content_type="image/png")
        self.client.post(reverse("tickets:create_for_project", args=[self.project.pk]),
                         {"project": self.project.pk, "type": "bug", "title": "serve", "priority": 3,
                          "attachments": [page, shot]})
        html_url = TicketAttachment.objects.get(original_name="evil.html").file.url
        png_url = TicketAttachment.objects.get(original_name="shot.png").file.url
        evil = self.client.get(html_url)
        self.assertEqual(evil["X-Content-Type-Options"], "nosniff")
        self.assertIn("attachment", evil["Content-Disposition"])
        picture = self.client.get(png_url)
        self.assertEqual(picture["X-Content-Type-Options"], "nosniff")
        self.assertNotIn("attachment", picture.get("Content-Disposition", ""))

    def test_add_attachment_on_detail(self):
        ticket = Ticket.objects.create(project=self.project, title="x", reporter=self.reporter)
        self.client.force_login(self.dev)
        self.client.post(
            reverse("tickets:attach", args=[ticket.pk]),
            {"files": [SimpleUploadedFile("a.txt", b"hi"), SimpleUploadedFile("b.csv", b"1,2")]},
        )
        self.assertEqual(ticket.attachments.count(), 2)

    def test_transition_records_resolution(self):
        ticket = Ticket.objects.create(project=self.project, title="x", reporter=self.reporter)
        self.client.force_login(self.dev)
        self.client.post(
            reverse("tickets:transition", args=[ticket.pk]),
            {"status": "resolved", "resolution": "Fixed in 1.2.3"},
        )
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, "resolved")
        self.assertEqual(ticket.resolution, "Fixed in 1.2.3")
        self.assertIsNotNone(ticket.resolved_at)

    def test_anyone_internal_can_be_assigned_but_never_client_contacts(self):
        """Raised from Tickets → New ticket: every active internal person is assignable, whether or not they
        are on the project; client contacts never are, and the inline change on the ticket follows the same rule."""
        from apps.projects.models import Customer

        other = Project.objects.create(name="Other", key="OTH")
        outsider = User.objects.create_user("outsider", password="x", role=self.role)
        other.members.add(outsider)
        client_role = Role.objects.create(name="Customer", slug="customer")
        client = User.objects.create_user("client", password="x", role=client_role,
                                          customer=Customer.objects.create(name="Acme"))
        linked_staff = User.objects.create_user("linked", password="x", role=self.role,
                                                customer=Customer.objects.create(name="SDF"))
        self.project.members.add(client)
        self.client.force_login(self.reporter)

        page = self.client.get(reverse("tickets:create"))
        field = page.context["form"].fields["assignee"]
        self.assertIn(outsider, field.queryset)      # not on this project, still assignable
        self.assertNotIn(client, field.queryset)     # Customer-role logins never
        self.assertIn(linked_staff, field.queryset)  # staff merely linked to a customer can be assigned
        self.assertNotIn("data-project-members", field.widget.attrs)

        resp = self.client.post(reverse("tickets:create"), {"project": self.project.pk, "type": "bug",
                                "title": "outside person", "priority": 3, "assignee": outsider.pk})
        self.assertEqual(resp.status_code, 302)
        ticket = Ticket.objects.get(title="outside person")
        self.assertEqual(ticket.assignee, outsider)

        resp = self.client.post(reverse("tickets:create"), {"project": self.project.pk, "type": "bug",
                                "title": "client person", "priority": 3, "assignee": client.pk})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("assignee", resp.context["form"].errors)

        resp = self.client.post(reverse("tickets:api_patch", args=[ticket.pk]),
                                data={"assignee": self.dev.pk}, content_type="application/json")
        self.assertEqual(resp.status_code, 200)       # inline change to someone else on staff
        resp = self.client.post(reverse("tickets:api_patch", args=[ticket.pk]),
                                data={"assignee": client.pk}, content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        ticket.refresh_from_db()
        self.assertEqual(ticket.assignee, self.dev)

    def test_team_only_narrows_the_assignee_list(self):
        """Everyone internal is listed; the Team box is just a filter, and it never excludes anyone server-side."""
        from apps.accounts.models import Team
        from apps.projects.models import Customer

        uiux = Team.objects.create(name="UIUX")
        design = User.objects.create_user("designer", password="x", role=self.role, team=uiux)
        other_team = User.objects.create_user("elsewhere", password="x", role=self.role, team=Team.objects.create(name="Ops"))
        client = User.objects.create_user("client2", password="x", role=Role.objects.create(name="Customer", slug="customer"),
                                          team=uiux, customer=Customer.objects.create(name="Acme2"))
        self.client.force_login(self.reporter)

        url = reverse("tickets:create_for_project", args=[self.project.pk])
        form = self.client.get(url).context["form"]
        self.assertIn(design, form.fields["assignee"].queryset)
        self.assertIn(other_team, form.fields["assignee"].queryset)
        self.assertNotIn(client, form.fields["assignee"].queryset)    # client contacts never
        html = str(form["assignee"])
        self.assertNotIn('data-extra="1"', html)                      # nobody is hidden until a team is picked
        self.assertIn(f'data-team="{uiux.pk}"', html)                 # but the team is there to filter on

        base = {"project": self.project.pk, "type": "bug", "priority": 3}
        resp = self.client.post(url, {**base, "title": "no team", "assignee": design.pk})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Ticket.objects.get(title="no team").assignee, design)
        resp = self.client.post(url, {**base, "title": "other team", "assignee": other_team.pk})
        self.assertEqual(resp.status_code, 302)

    def test_assignee_is_a_searchable_dropdown_labelled_with_full_names(self):
        person = User.objects.create_user("ykul", password="x", role=self.role, first_name="Yashashri", last_name="Kulkarni")
        self.client.force_login(self.reporter)
        form = self.client.get(reverse("tickets:create")).context["form"]
        html = str(form["assignee"])
        self.assertIn('data-search="1"', html)                 # picked up by static/js/search-select.js
        self.assertIn("Yashashri Kulkarni (ykul)", html)       # findable by name or by username
        self.assertEqual(form.fields["assignee"].label_from_instance(self.dev), "dev")  # no name: username only

    def test_visibility_scoping_for_guest(self):
        guest_role = Role.objects.create(name="Guest", slug="guest2")
        _cap("ticket.view", guest_role, "own")
        outsider = User.objects.create_user("out", password="x", role=guest_role)
        mine = Ticket.objects.create(project=self.project, title="mine", reporter=outsider)
        Ticket.objects.create(project=self.project, title="theirs", reporter=self.reporter)
        visible = visible_tickets(outsider)
        self.assertIn(mine, visible)
        self.assertEqual(visible.count(), 1)
