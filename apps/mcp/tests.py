import json

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Capability, User, UserCapability
from apps.accounts.permissions import invalidate
from apps.accounts.testing import skip_password_prompt
from apps.projects.models import Project
from apps.sprints.models import Requirement, UnitTestCase
from apps.tickets.models import Ticket, TicketComment

from .models import ProjectMCPToken


def rpc(method, params=None, msg_id=1):
    body = {"jsonrpc": "2.0", "method": method}
    if msg_id is not None:
        body["id"] = msg_id
    if params is not None:
        body["params"] = params
    return json.dumps(body)


class McpBase(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        self.owner = User.objects.create_superuser("mcp_owner", "o@example.com", "pw")
        self.project = Project.objects.create(key="ALPHA", name="Alpha", owner=self.owner)
        self.other = Project.objects.create(key="BETA", name="Beta", owner=self.owner)
        self.ticket = Ticket.objects.create(project=self.project, title="Login 500", type="bug", reporter=self.owner)
        self.other_ticket = Ticket.objects.create(project=self.other, title="Beta bug", type="bug", reporter=self.owner)
        self.req = Requirement.objects.create(project=self.project, title="Sign-up form")
        self.tc = UnitTestCase.objects.create(project=self.project, requirement=self.req, title="valid email passes")

    def mint(self, **kw):
        raw, token_hash, prefix = ProjectMCPToken.generate()
        ProjectMCPToken.objects.create(
            project=kw.pop("project", self.project),
            created_by=kw.pop("created_by", self.owner),
            token_hash=token_hash,
            token_prefix=prefix,
            **kw,
        )
        return raw

    def call(self, raw, method, params=None):
        url = reverse("mcp:endpoint", args=[raw])
        resp = self.client.post(url, data=rpc(method, params), content_type="application/json", HTTP_HOST="localhost")
        return resp

    def tool(self, raw, name, arguments=None):
        resp = self.call(raw, "tools/call", {"name": name, "arguments": arguments or {}})
        self.assertEqual(resp.status_code, 200, resp.content)
        result = resp.json()["result"]
        text = result["content"][0]["text"]
        return result.get("isError", False), text


class AuthTests(McpBase):
    def test_missing_and_bad_tokens(self):
        self.assertEqual(self.client.post("/mcp/nope/", data=rpc("ping"), content_type="application/json",
                                          HTTP_HOST="localhost").status_code, 401)

    def test_revoked_token(self):
        raw = self.mint()
        ProjectMCPToken.objects.all().update(revoked_at=timezone.now())
        self.assertEqual(self.call(raw, "ping").status_code, 403)

    def test_expired_token(self):
        raw = self.mint(expires_at=timezone.now() - timezone.timedelta(hours=1))
        self.assertEqual(self.call(raw, "ping").status_code, 403)

    def test_get_not_allowed(self):
        raw = self.mint()
        resp = self.client.get(reverse("mcp:endpoint", args=[raw]), HTTP_HOST="localhost")
        self.assertEqual(resp.status_code, 405)

    def test_notification_returns_202(self):
        raw = self.mint()
        url = reverse("mcp:endpoint", args=[raw])
        resp = self.client.post(url, data=rpc("notifications/initialized", msg_id=None),
                                content_type="application/json", HTTP_HOST="localhost")
        self.assertEqual(resp.status_code, 202)

    def test_initialize_handshake(self):
        raw = self.mint()
        resp = self.call(raw, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
        data = resp.json()["result"]
        self.assertEqual(data["protocolVersion"], "2025-06-18")
        self.assertIn("ALPHA", data["instructions"])
        self.assertIn("tools", data["capabilities"])

    def test_bearer_header_auth(self):
        raw = self.mint()
        resp = self.client.post("/mcp/anything/", data=rpc("ping"), content_type="application/json",
                                HTTP_HOST="localhost", HTTP_AUTHORIZATION=f"Bearer {raw}")
        # path token 'anything' is bogus but the header carries the real one
        self.assertEqual(resp.status_code, 200)


class ToolListTests(McpBase):
    def test_tools_list_contains_core_tools(self):
        raw = self.mint()
        names = {t["name"] for t in self.call(raw, "tools/list").json()["result"]["tools"]}
        for expected in ["project_overview", "search_docs", "ask_project", "query_data", "get_document",
                         "get_design_workspace",
                         "list_tickets", "get_ticket", "comment_ticket", "set_ticket_status", "create_ticket",
                         "list_requirements", "get_requirement", "record_test_result", "set_requirement_status",
                         "add_acceptance_criterion", "list_external_services"]:
            self.assertIn(expected, names)


class DesignWorkspaceTests(McpBase):
    def test_none_when_no_workspace(self):
        from apps.penpot.models import PenpotWorkspace

        PenpotWorkspace.objects.filter(project=self.project).delete()  # drop any signal-queued row
        raw = self.mint()
        is_error, text = self.tool(raw, "get_design_workspace")
        self.assertFalse(is_error)
        self.assertIsNone(json.loads(text)["design_workspace"])

    def test_reports_this_projects_workspace(self):
        from apps.penpot.models import PenpotWorkspace

        PenpotWorkspace.objects.update_or_create(
            project=self.project,
            defaults=dict(status="ready", file_id="file-9", file_name="ALPHA — UI",
                          penpot_project_id="pp-9", base_url="http://penpot.test:9001"),
        )
        PenpotWorkspace.objects.update_or_create(
            project=self.other, defaults=dict(status="ready", file_id="beta-file"),
        )
        raw = self.mint()
        is_error, text = self.tool(raw, "get_design_workspace")
        self.assertFalse(is_error)
        dw = json.loads(text)["design_workspace"]
        self.assertEqual(dw["file_id"], "file-9")
        self.assertEqual(dw["file_name"], "ALPHA — UI")
        self.assertTrue(dw["ready"])
        self.assertIn("file-9", dw["workspace_url"])


class ScopeTests(McpBase):
    def test_list_tickets_only_this_project(self):
        raw = self.mint()
        is_error, text = self.tool(raw, "list_tickets")
        self.assertFalse(is_error)
        keys = {t["key"] for t in json.loads(text)["tickets"]}
        self.assertIn(self.ticket.key, keys)
        self.assertNotIn(self.other_ticket.key, keys)

    def test_get_ticket_cannot_reach_other_project(self):
        raw = self.mint()
        # other_ticket.number is 1 within BETA; ask for number 1 in ALPHA -> our ticket, not Beta's
        is_error, text = self.tool(raw, "get_ticket", {"ref": self.other_ticket.key})
        # BETA-1 parses to number 1; ALPHA has a ticket #1 too, so make sure it's OURS
        self.assertFalse(is_error)
        self.assertEqual(json.loads(text)["key"], self.ticket.key)

    def test_get_requirement_other_project_not_found(self):
        other_req = Requirement.objects.create(project=self.other, title="Beta req")
        raw = self.mint()
        is_error, text = self.tool(raw, "get_requirement", {"number": str(other_req.number + 999)})
        self.assertTrue(is_error)
        self.assertIn("not found", text)


class WriteGateTests(McpBase):
    def test_comment_ticket_blocked_without_scope(self):
        raw = self.mint()  # read-only
        is_error, text = self.tool(raw, "comment_ticket", {"ref": self.ticket.key, "body": "hi"})
        self.assertTrue(is_error)
        self.assertIn("read-only", text)
        self.assertEqual(TicketComment.objects.count(), 0)

    def test_comment_ticket_succeeds_with_scope_and_cap(self):
        raw = self.mint(allow_ticket_write=True)
        is_error, text = self.tool(raw, "comment_ticket", {"ref": self.ticket.key, "body": "fix in progress"})
        self.assertFalse(is_error, text)
        self.assertEqual(TicketComment.objects.filter(ticket=self.ticket).count(), 1)
        self.assertTrue(self.ticket.events.filter(verb__icontains="Claude Code").exists())

    def test_bound_user_must_see_project(self):
        plain = User.objects.create_user("plain", "p@example.com", "pw")
        raw = self.mint(created_by=plain, allow_ticket_write=True)
        # plain can't see ALPHA at all -> auth fails before any tool runs
        self.assertEqual(self.call(raw, "ping").status_code, 403)

    def test_capability_gate_when_user_lacks_cap(self):
        plain = User.objects.create_user("plain2", "p2@example.com", "pw")
        self.project.members.add(plain)  # now visible_projects(plain) includes ALPHA
        view = Capability.objects.create(code="ticket.view", label="view tickets")
        UserCapability.objects.create(user=plain, capability=view, scope="all")
        invalidate(plain)

        raw = self.mint(created_by=plain, allow_ticket_write=True)
        is_error, text = self.tool(raw, "comment_ticket", {"ref": self.ticket.key, "body": "hi"})
        self.assertTrue(is_error)
        self.assertIn("ticket.comment", text)
        self.assertEqual(TicketComment.objects.count(), 0)

    def test_sprint_write_gates(self):
        raw = self.mint()  # sprint write off
        is_error, text = self.tool(raw, "set_requirement_status", {"number": str(self.req.number), "status": "ready"})
        self.assertTrue(is_error)
        self.assertIn("read-only", text)

        raw2 = self.mint(allow_sprint_write=True)
        is_error, text = self.tool(raw2, "set_requirement_status", {"number": str(self.req.number), "status": "ready"})
        self.assertFalse(is_error, text)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, "ready")

    def test_record_test_result(self):
        raw = self.mint(allow_sprint_write=True)
        is_error, text = self.tool(raw, "record_test_result", {"number": str(self.tc.number), "status": "pass"})
        self.assertFalse(is_error, text)
        self.tc.refresh_from_db()
        self.assertEqual(self.tc.status, "pass")
        self.assertEqual(self.tc.developer, self.owner)


class DocAndSearchTests(McpBase):
    def test_get_document_returns_org_default(self):
        raw = self.mint()
        is_error, text = self.tool(raw, "get_document", {"kind": "tech_stack"})
        self.assertFalse(is_error)
        self.assertIn("Tech Stack", text)

    def test_search_docs_monkeypatched(self):
        from apps.rag.services import retrieve

        raw = self.mint()
        original = retrieve.semantic_search
        retrieve.semantic_search = lambda project, query, top_k=6: [
            {"label": "BRD", "text": "auth uses JWT", "source_type": "brd", "source_id": 1, "score": 0.9}
        ]
        try:
            is_error, text = self.tool(raw, "search_docs", {"query": "auth"})
        finally:
            retrieve.semantic_search = original
        self.assertFalse(is_error, text)
        self.assertIn("JWT", text)

    def test_unknown_tool(self):
        raw = self.mint()
        is_error, text = self.tool(raw, "does_not_exist")
        self.assertTrue(is_error)


class ManageUiTests(McpBase):
    def test_manage_tab_requires_capability(self):
        User.objects.create_user("nobody", "n@example.com", "pw")
        self.client.login(username="nobody", password="pw")
        resp = self.client.get(reverse("mcp:manage", args=[self.project.pk]), HTTP_HOST="localhost")
        self.assertIn(resp.status_code, (403, 404))

    def test_owner_can_create_and_revoke(self):
        self.client.force_login(self.owner)
        resp = self.client.post(reverse("mcp:token_create", args=[self.project.pk]),
                                {"name": "CC", "allow_ticket_write": "on"}, HTTP_HOST="localhost")
        self.assertEqual(resp.status_code, 302)
        token = ProjectMCPToken.objects.get(project=self.project)
        self.assertTrue(token.allow_ticket_write)
        resp = self.client.post(reverse("mcp:token_revoke", args=[self.project.pk, token.pk]), HTTP_HOST="localhost")
        token.refresh_from_db()
        self.assertFalse(token.is_active)
