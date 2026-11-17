import json

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Capability, User, UserCapability
from apps.accounts.permissions import invalidate
from apps.accounts.testing import skip_password_prompt
from apps.mcp.models import ProjectMCPToken
from apps.projects.models import Project
from apps.sprints.models import Requirement, Sprint

from .models import DevUpdate
from .selectors import milestone_progress, visible_dev_updates


class MilestoneProgressTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        self.owner = User.objects.create_superuser("dl_owner", "o@example.com", "pw")
        self.project = Project.objects.create(key="ALPHA", name="Alpha", owner=self.owner)

    def add(self, milestone, done=False, status="in_progress"):
        return DevUpdate.objects.create(
            project=self.project, created_by=self.owner, headline="x",
            milestone=milestone, milestone_done=done, status=status,
        )

    def test_first_seen_ordering_and_stickiness(self):
        self.add("M1")
        self.add("M2")
        self.add("M1", done=True, status="completed")
        self.add("M2", status="blocked")
        prog = milestone_progress(self.project.dev_updates.all())
        self.assertEqual([m["label"] for m in prog["milestones"]], ["M1", "M2"])
        m1, m2 = prog["milestones"]
        self.assertTrue(m1["done"])          # sticky once flagged
        self.assertEqual(m1["status"], "completed")
        self.assertFalse(m2["done"])
        self.assertEqual(m2["status"], "blocked")  # latest status wins
        self.assertEqual((prog["done"], prog["total"]), (1, 2))

    def test_blank_milestones_ignored(self):
        self.add("")
        self.add("   ")
        self.assertEqual(milestone_progress(self.project.dev_updates.all())["total"], 0)


class VisibilityTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        self.super = User.objects.create_superuser("dl_su", "s@example.com", "pw")
        self.a = Project.objects.create(key="AAA", name="A", owner=self.super)
        self.b = Project.objects.create(key="BBB", name="B", owner=self.super)
        self.ua = DevUpdate.objects.create(project=self.a, headline="in A", created_by=self.super)
        self.ub = DevUpdate.objects.create(project=self.b, headline="in B", created_by=self.super)

    def test_superuser_sees_all(self):
        self.assertEqual(visible_dev_updates(self.super).count(), 2)

    def test_no_capability_sees_only_own(self):
        plain = User.objects.create_user("dl_plain", "p@example.com", "pw")
        self.assertEqual(list(visible_dev_updates(plain)), [])
        mine = DevUpdate.objects.create(project=self.a, headline="mine", created_by=plain)
        self.assertEqual(list(visible_dev_updates(plain)), [mine])

    def test_view_capability_scopes_to_visible_projects(self):
        u = User.objects.create_user("dl_viewer", "v@example.com", "pw")
        self.a.members.add(u)
        cap = Capability.objects.create(code="devlog.view", label="view")
        UserCapability.objects.create(user=u, capability=cap, scope="team")
        invalidate(u)
        seen = set(visible_dev_updates(u).values_list("headline", flat=True))
        self.assertIn("in A", seen)
        self.assertNotIn("in B", seen)


class ViewTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        self.owner = User.objects.create_superuser("dl_v_owner", "o@example.com", "pw")
        self.project = Project.objects.create(key="ALPHA", name="Alpha", owner=self.owner)
        self.update = DevUpdate.objects.create(project=self.project, headline="thing", created_by=self.owner)

    def test_project_log_renders_with_tab(self):
        self.client.force_login(self.owner)
        r = self.client.get(reverse("devlog:project_log", args=[self.project.pk]), HTTP_HOST="localhost")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b">Dev Log<", r.content)
        self.assertIn(b"thing", r.content)

    def test_log_denied_without_view_cap(self):
        u = User.objects.create_user("dl_nobody", "n@example.com", "pw")
        self.client.force_login(u)
        r = self.client.get(reverse("devlog:project_log", args=[self.project.pk]), HTTP_HOST="localhost")
        self.assertIn(r.status_code, (403, 404))

    def test_create_edit_delete(self):
        self.client.force_login(self.owner)
        r = self.client.post(
            reverse("devlog:update_create", args=[self.project.pk]),
            {"headline": "new one", "kind": "feature", "status": "in_progress",
             "milestone": "M1", "files_changed": "a.py\nb.py"},
            HTTP_HOST="localhost",
        )
        self.assertEqual(r.status_code, 302)
        made = DevUpdate.objects.get(headline="new one")
        self.assertEqual(made.files_changed, ["a.py", "b.py"])
        self.assertEqual(made.source, "web")

        r = self.client.post(
            reverse("devlog:update_edit", args=[made.pk]),
            {"headline": "new one", "kind": "feature", "status": "completed",
             "milestone": "M1", "milestone_done": "on", "files_changed": ""},
            HTTP_HOST="localhost",
        )
        made.refresh_from_db()
        self.assertEqual(made.status, "completed")
        self.assertTrue(made.milestone_done)

        r = self.client.post(reverse("devlog:update_delete", args=[made.pk]), HTTP_HOST="localhost")
        self.assertFalse(DevUpdate.objects.filter(pk=made.pk).exists())


class McpDevlogTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        self.owner = User.objects.create_superuser("dl_mcp", "m@example.com", "pw")
        self.project = Project.objects.create(key="ALPHA", name="Alpha", owner=self.owner)
        self.other = Project.objects.create(key="BETA", name="Beta", owner=self.owner)
        self.sprint = Sprint.objects.create(project=self.project, name="S1")
        self.req = Requirement.objects.create(project=self.project, title="req")
        self.other_sprint = Sprint.objects.create(project=self.other, name="S1")
        raw, h, p = ProjectMCPToken.generate()
        ProjectMCPToken.objects.create(project=self.project, created_by=self.owner, token_hash=h, token_prefix=p)
        self.url = reverse("mcp:endpoint", args=[raw])

    def call(self, name, arguments):
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": name, "arguments": arguments}}
        resp = self.client.post(self.url, data=json.dumps(body), content_type="application/json", HTTP_HOST="localhost")
        self.assertEqual(resp.status_code, 200, resp.content)
        result = resp.json()["result"]
        return result.get("isError", False), result["content"][0]["text"]

    def test_push_creates_scoped_update(self):
        is_error, text = self.call("push_dev_update", {
            "headline": "Auth skeleton", "kind": "feature", "status": "in_progress",
            "milestone": "M1 Auth", "sprint": "ALPHA-S1", "requirement": str(self.req.number),
            "files_changed": ["backend/auth/views.py"],
        })
        self.assertFalse(is_error, text)
        u = DevUpdate.objects.get(project=self.project)
        self.assertEqual(u.source, "mcp")
        self.assertIsNotNone(u.via_token)
        self.assertEqual(u.sprint, self.sprint)
        self.assertEqual(u.requirement, self.req)

    def test_push_link_is_project_scoped(self):
        # "BETA-S1" resolves to sprint number 1 *within ALPHA* — it can never reach BETA's sprint
        is_error, text = self.call("push_dev_update", {"headline": "x", "sprint": self.other_sprint.key})
        self.assertFalse(is_error, text)
        u = DevUpdate.objects.latest("id")
        self.assertEqual(u.sprint_id, self.sprint.id)
        self.assertNotEqual(u.sprint_id, self.other_sprint.id)

    def test_push_rejects_unknown_ref(self):
        is_error, text = self.call("push_dev_update", {"headline": "x", "sprint": "ALPHA-S99"})
        self.assertTrue(is_error)
        self.assertIn("not found", text)
        self.assertEqual(DevUpdate.objects.count(), 0)

    def test_list_and_milestones_and_update(self):
        self.call("push_dev_update", {"headline": "step 1", "milestone": "M1"})
        _, text = self.call("list_dev_updates", {})
        data = json.loads(text)
        self.assertEqual(data["count"], 1)
        uid = data["updates"][0]["id"]

        is_error, text = self.call("update_dev_update", {"id": uid, "status": "completed", "milestone_done": True})
        self.assertFalse(is_error, text)

        _, text = self.call("list_dev_milestones", {})
        prog = json.loads(text)
        self.assertEqual((prog["done"], prog["total"]), (1, 1))
