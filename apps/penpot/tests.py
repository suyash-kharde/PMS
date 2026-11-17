from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.permissions import invalidate
from apps.accounts.testing import skip_password_prompt
from apps.projects.models import Project

from . import services
from .client import PenpotAuthError, PenpotError
from .models import PenpotWorkspace

PENPOT_ON = dict(
    PENPOT_ENABLED=True,
    PENPOT_BASE_URL="http://penpot.test:9001",
    PENPOT_BOT_EMAIL_DOMAIN="planwise.test",
    PENPOT_DEFAULT_INVITES=[],
)


class FakePenpotClient:
    """Records calls and returns plausible Penpot RPC shapes — no network.

    Flags let a test simulate failure modes:
      * login_fails      -> login() raises PenpotAuthError (bot unrecoverable)
      * login_plain_error -> login() raises a plain PenpotError (e.g. a 400 the
        client did not classify as auth) — must still be treated as unrecoverable
      * get_profile_fails -> get_profile() raises PenpotAuthError (stale session)
      * register_fails_for -> set of emails prepare_register/register reject
    """

    login_fails = False
    login_plain_error = False
    get_profile_fails = False
    register_fails_for: set = frozenset()

    def __init__(self, *a, **k):
        self.calls = []
        self._authed = False
        self._counter = 0

    # auth -----------------------------------------------------------------
    @property
    def authenticated(self):
        return self._authed

    @property
    def auth_token(self):
        return "fake-auth-token"

    def set_session(self, t):
        self._authed = bool(t)

    def login(self, email, password):
        self.calls.append(("login", email))
        if self.login_plain_error:
            raise PenpotError("login-with-password", 400, "some-unclassified-error")
        if self.login_fails:
            raise PenpotAuthError("login-with-password", 400, "wrong-credentials")
        self._authed = True
        return {"id": "prof-1", "defaultTeamId": "team-1", "defaultProjectId": "proj-1"}

    def logout(self):
        self._authed = False

    def get_profile(self):
        self.calls.append(("get_profile", None))
        if self.get_profile_fails:
            raise PenpotAuthError("get-profile", 401, "authentication required")
        return {"id": "prof-1", "defaultTeamId": "team-1"}

    def get_teams(self):
        return [{"id": "team-1", "name": "Default", "isDefault": True}]

    # registration -------------------------------------------------------
    def prepare_register(self, fullname, email, password):
        self.calls.append(("prepare_register", email))
        if email in self.register_fails_for:
            raise PenpotError("prepare-register-profile", 400, "email-already-exists")
        return "reg-token"

    def register(self, token, fullname=""):
        self.calls.append(("register", token))
        self._authed = True
        self._counter += 1
        return {"id": f"prof-{self._counter}", "defaultTeamId": f"team-{self._counter}"}

    # tokens / teams / projects / files --------------------------------
    def create_access_token(self, name):
        return {"id": "tok-1", "token": "penpot-access-token-xyz"}

    def delete_access_token(self, token_id):
        self.calls.append(("delete_access_token", token_id))

    def update_team(self, team_id, name):
        self.calls.append(("update_team", name))
        return {"id": team_id, "name": name}

    def create_project(self, team_id, name):
        return {"id": "penproj-1", "name": name}

    def rename_project(self, project_id, name):
        return {"id": project_id, "name": name}

    def create_file(self, project_id, name):
        return {"id": "file-1", "name": name}

    def get_project_files(self, project_id):
        return [{"id": "file-1", "name": "PLAT — UI", "revn": 3}]

    def get_file(self, file_id):
        return {"id": file_id, "data": {"pages": ["page-1"]}}

    def create_share_link(self, file_id, pages, who="all"):
        return {"id": "share-1"}

    def create_team_invitations(self, team_id, emails, role="editor"):
        return {"total": len(emails), "invitations": [f"inv-{e}" for e in emails]}

    def delete_profile(self):
        self.calls.append(("delete_profile", None))


def _patch(fake):
    return mock.patch.object(services, "_raw_client", return_value=fake)


@override_settings(**PENPOT_ON)
class EnqueueTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        self.user = User.objects.create_superuser("po", "po@example.com", "pw")

    def test_signal_enqueues_on_project_create(self):
        project = Project.objects.create(key="PLAT", name="Platform", owner=self.user)
        ws = PenpotWorkspace.objects.filter(project=project).first()
        self.assertIsNotNone(ws)
        self.assertEqual(ws.status, "queued")

    def test_enqueue_is_idempotent(self):
        project = Project.objects.create(key="PLAT", name="Platform", owner=self.user)
        services.enqueue_for_project(project)
        self.assertEqual(PenpotWorkspace.objects.filter(project=project).count(), 1)

    def test_enqueue_requeues_failed_and_disabled(self):
        project = Project.objects.create(key="PLAT", name="Platform", owner=self.user)
        ws = PenpotWorkspace.objects.get(project=project)
        for bad in ("failed", "disabled"):
            ws.status = bad
            ws.save(update_fields=["status"])
            services.enqueue_for_project(project)
            ws.refresh_from_db()
            self.assertEqual(ws.status, "queued")

    @override_settings(PENPOT_ENABLED=False)
    def test_disabled_does_nothing(self):
        project = Project.objects.create(key="PLAT", name="Platform", owner=self.user)
        self.assertFalse(PenpotWorkspace.objects.filter(project=project).exists())


@override_settings(**PENPOT_ON)
class ProvisionTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        self.user = User.objects.create_superuser("po", "po@example.com", "pw")
        self.project = Project.objects.create(key="PLAT", name="Platform", owner=self.user)

    def test_provision_end_to_end(self):
        fake = FakePenpotClient()
        with _patch(fake):
            ws = services.enqueue_for_project(self.project)
            services.provision(ws)
        ws.refresh_from_db()
        self.assertEqual(ws.status, "ready")
        self.assertEqual(ws.penpot_project_id, "penproj-1")
        self.assertEqual(ws.file_id, "file-1")
        self.assertEqual(ws.access_token, "penpot-access-token-xyz")
        self.assertTrue(ws.bot_email.endswith("@planwise.test"))
        self.assertEqual(ws.workspace_url, "http://penpot.test:9001/#/workspace/penproj-1/file-1")

    def test_provision_failure_marks_failed(self):
        fake = FakePenpotClient()
        fake.prepare_register = mock.Mock(side_effect=RuntimeError("boom"))
        with _patch(fake):
            ws = services.enqueue_for_project(self.project)
            services.provision(ws)
        ws.refresh_from_db()
        self.assertEqual(ws.status, "failed")
        self.assertIn("boom", ws.last_error)

    def test_reprovision_when_stored_bot_is_gone(self):
        # first provision succeeds
        with _patch(FakePenpotClient()):
            ws = services.enqueue_for_project(self.project)
            services.provision(ws)
        ws.refresh_from_db()
        first_file = ws.file_id
        self.assertEqual(ws.status, "ready")

        # Penpot instance was reset: the stored session AND a fresh login both fail.
        class Gone(FakePenpotClient):
            login_fails = True
            get_profile_fails = True

        ws.status = "queued"
        ws.save(update_fields=["status"])
        with _patch(Gone()):
            services.provision(ws)
        ws.refresh_from_db()
        # it forgot the dead ids and rebuilt to a working state
        self.assertEqual(ws.status, "ready")
        self.assertTrue(ws.profile_id)
        self.assertEqual(ws.file_id, first_file)  # fake returns the same id, point is it rebuilt

    def test_rebuild_takes_a_fresh_email_when_address_is_stuck(self):
        with _patch(FakePenpotClient()):
            ws = services.enqueue_for_project(self.project)
            services.provision(ws)
        ws.refresh_from_db()
        original_email = ws.bot_email

        # the deterministic address is already registered AND its password is lost
        class Stuck(FakePenpotClient):
            login_fails = True
            register_fails_for = frozenset({original_email})

        with _patch(Stuck()):
            services.rebuild(ws)
        ws.refresh_from_db()
        self.assertEqual(ws.status, "ready")
        self.assertNotEqual(ws.bot_email, original_email)
        self.assertTrue(ws.bot_email.startswith("penpot+plat-"))

    def test_rebuild_clears_remote_fields_first(self):
        with _patch(FakePenpotClient()):
            ws = services.enqueue_for_project(self.project)
            services.provision(ws)
        ws.refresh_from_db()
        ws.share_link_id = "old-share"
        ws.save(update_fields=["share_link_id"])
        with _patch(FakePenpotClient()):
            services.rebuild(ws)
        ws.refresh_from_db()
        self.assertEqual(ws.share_link_id, "")
        self.assertEqual(ws.status, "ready")

    def test_rebuild_keeps_the_bot_password(self):
        # the remote account usually still exists; its password is the only way in
        with _patch(FakePenpotClient()):
            ws = services.enqueue_for_project(self.project)
            services.provision(ws)
        ws.refresh_from_db()
        pw = ws.bot_password
        self.assertTrue(pw)
        with _patch(FakePenpotClient()):
            services.rebuild(ws)
        ws.refresh_from_db()
        self.assertEqual(ws.bot_password, pw)

    def test_recovers_when_login_raises_an_unclassified_error(self):
        # regression: koel wedged because login() on wrong-credentials raised a
        # plain PenpotError (HTTP 400), which the fresh-address fallback did not catch
        with _patch(FakePenpotClient()):
            ws = services.enqueue_for_project(self.project)
            services.provision(ws)
        ws.refresh_from_db()
        original_email = ws.bot_email

        class Stuck(FakePenpotClient):
            login_plain_error = True
            register_fails_for = frozenset({original_email})

        with _patch(Stuck()):
            services.rebuild(ws)
        ws.refresh_from_db()
        self.assertEqual(ws.status, "ready")
        self.assertNotEqual(ws.bot_email, original_email)
        self.assertTrue(ws.bot_email.startswith("penpot+plat-"))


@override_settings(**PENPOT_ON)
class ViewTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        self.admin = User.objects.create_superuser("admin1", "a@example.com", "pw")
        self.member = User.objects.create_user("bob", "bob@example.com", "pw")
        self.project = Project.objects.create(key="PLAT", name="Platform", owner=self.admin)
        self.project.members.add(self.member)
        invalidate()

    def test_manage_requires_capability(self):
        self.client.login(username="bob", password="pw")
        resp = self.client.get(reverse("penpot:manage", args=[self.project.pk]), HTTP_HOST="localhost")
        self.assertIn(resp.status_code, (302, 403))

    def test_manage_ok_for_superuser(self):
        self.client.login(username="admin1", password="pw")
        resp = self.client.get(reverse("penpot:manage", args=[self.project.pk]), HTTP_HOST="localhost")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Design workspace")

    def test_manage_makes_no_penpot_call(self):
        """A GET on the Design tab must render even if Penpot is unreachable."""
        with _patch(FakePenpotClient()):
            services.provision(services.enqueue_for_project(self.project))
        self.client.login(username="admin1", password="pw")
        with mock.patch.object(services, "_raw_client", side_effect=AssertionError("network on GET")):
            resp = self.client.get(reverse("penpot:manage", args=[self.project.pk]), HTTP_HOST="localhost")
        self.assertEqual(resp.status_code, 200)

    def test_rebuild_view(self):
        with _patch(FakePenpotClient()):
            services.provision(services.enqueue_for_project(self.project))
        self.client.login(username="admin1", password="pw")
        with _patch(FakePenpotClient()):
            resp = self.client.post(reverse("penpot:rebuild", args=[self.project.pk]), HTTP_HOST="localhost")
        self.assertEqual(resp.status_code, 302)
        self.project.penpot_workspace.refresh_from_db()
        self.assertEqual(self.project.penpot_workspace.status, "ready")


class PenpotClientErrorClassificationTests(TestCase):
    """_rpc() must surface Penpot's HTTP-400 wrong-credentials as PenpotAuthError."""

    def _post(self, status, body):
        from .client import PenpotClient

        resp = mock.Mock(status_code=status, content=b"x", cookies={})
        resp.json.return_value = body
        with mock.patch("apps.penpot.client.httpx.post", return_value=resp):
            return PenpotClient("http://penpot.test")._rpc("login-with-password", {}, auth=False)

    def test_wrong_credentials_is_auth_error(self):
        with self.assertRaises(PenpotAuthError):
            self._post(400, {"type": "validation", "code": "wrong-credentials"})

    def test_other_400_stays_plain_error(self):
        with self.assertRaises(PenpotError) as cm:
            self._post(400, {"type": "validation", "code": "email-already-exists"})
        self.assertNotIsInstance(cm.exception, PenpotAuthError)
