"""Project sites through Planwise (/sites/<name>/): link options, the nginx access check, gateway sync."""
from unittest import mock

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.testing import skip_password_prompt
from apps.audit.context import set_current_user

from . import sites
from .models import Project, ProjectLink, ProjectMembership


class FakeResponse:
    def __init__(self, status=200, data=None):
        self.status_code, self._data, self.text = status, data, ""

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeClient:
    """Stands in for the gateway's admin API."""

    def __init__(self, apps=None, fail=False):
        self.apps, self.fail, self.calls = dict(apps or {}), fail, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def _check(self):
        if self.fail:
            raise ConnectionError("gateway down")

    def get(self, path):
        self._check()
        return FakeResponse(200, list(self.apps.values()))

    def post(self, path, json):
        self._check()
        self.calls.append(("post", json["name"]))
        self.apps[json["name"]] = json
        return FakeResponse(200, json)

    def delete(self, path):
        self._check()
        name = path.rsplit("/", 1)[-1]
        self.calls.append(("delete", name))
        return FakeResponse(204 if self.apps.pop(name, None) else 404)


@mock.patch.dict("os.environ", {"GATEWAY_URL": "http://gateway:8080", "GATEWAY_ADMIN_TOKEN": "t"})
class SitesTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        # these tests are about project sites, not the login-code step (accounts.OtpRequiredMiddleware)
        from apps.accounts.middleware import OtpRequiredMiddleware

        otp_off = mock.patch.object(OtpRequiredMiddleware, "__call__", lambda self, request: self.get_response(request))
        otp_off.start()
        self.addCleanup(otp_off.stop)
        self.addCleanup(set_current_user, None)
        penpot_off = mock.patch("apps.penpot.services.enabled", return_value=False)
        penpot_off.start()
        self.addCleanup(penpot_off.stop)
        self.admin = User.objects.create_superuser("boss", password="x")
        self.member = User.objects.create_user("mem", password="x")
        self.outsider = User.objects.create_user("out", password="x")
        self.project = Project.objects.create(key="solar", name="Gepdec solar", status="active", owner=self.admin)
        ProjectMembership.objects.create(project=self.project, user=self.member, side="internal")
        self.gateway = FakeClient()
        patcher = mock.patch.object(sites, "_client", side_effect=lambda: self.gateway)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _save(self, link=None, **fields):
        prefix = f"l{link.pk}" if link else "new"
        data = {"title": "UAT", "url": "http://172.16.15.169:4201/dashboard?x=1", "via_gateway": "on",
                "site_name": "gepdec_solar", "rewrite_html": "on", "rewrite_js": "on", "isolate_storage": "on",
                "mounts": "/_api = http://172.16.15.169:8039", **fields}
        url = (reverse("projects:project_link_save", args=[self.project.pk, link.pk]) if link
               else reverse("projects:project_link_add", args=[self.project.pk]))
        self.client.force_login(self.admin)
        return self.client.post(url, {f"{prefix}-{k}": v for k, v in data.items() if v is not None}, follow=True)

    def test_save_publishes_to_gateway_and_open_site_goes_through_planwise(self):
        r = self._save()
        link = ProjectLink.objects.get()
        self.assertEqual((link.via_gateway, link.site_name, link.gateway_error), (True, "gepdec_solar", ""))
        self.assertEqual(link.open_url, "/sites/gepdec_solar/dashboard?x=1")
        self.assertEqual(self.gateway.apps["gepdec_solar"]["target"], "http://172.16.15.169:4201")
        self.assertEqual(self.gateway.apps["gepdec_solar"]["mounts"],
                         [{"path": "/_api", "target": "http://172.16.15.169:8039"}])
        self.assertContains(r, 'href="/sites/gepdec_solar/dashboard?x=1"')
        self.assertContains(r, "Through Planwise")

    def test_name_rules_and_uniqueness(self):
        self._save(site_name="Bad Name!")
        self.assertFalse(ProjectLink.objects.exists())
        self._save()
        self._save(title="Admin", site_name="gepdec_solar")  # taken
        self.assertEqual(ProjectLink.objects.count(), 1)
        self._save(title="Admin", site_name="solar_admin", mounts="nonsense")
        self.assertEqual(ProjectLink.objects.count(), 1)

    def test_rename_switch_off_and_delete_unpublish(self):
        self._save()
        link = ProjectLink.objects.get()
        self._save(link, site_name="solar_uat")
        self.assertEqual(set(self.gateway.apps), {"solar_uat"})
        self._save(link, via_gateway=None)  # switched off: back to the direct link
        link.refresh_from_db()
        self.assertEqual((link.open_url, self.gateway.apps), ("http://172.16.15.169:4201/dashboard?x=1", {}))
        self._save(link)
        self.client.post(reverse("projects:project_link_delete", args=[self.project.pk, link.pk]))
        self.assertEqual(self.gateway.apps, {})

    def test_gateway_down_save_still_works_and_sync_catches_up(self):
        self.gateway.fail = True
        r = self._save()
        link = ProjectLink.objects.get()
        self.assertIn("will retry", link.gateway_error)
        self.assertContains(r, "Not live yet")
        self.gateway.fail = False
        self.gateway.apps["stale_site"] = {"name": "stale_site", "target": "http://x"}
        self.assertEqual(sites.sync_all(), (1, 1))
        link.refresh_from_db()
        self.assertEqual((link.gateway_error, set(self.gateway.apps)), ("", {"gepdec_solar"}))
        self.assertEqual(sites.sync_all(), (0, 0))  # nothing to do the second time

    def test_access_check_for_nginx(self):
        self._save()
        url = reverse("projects:site_auth")
        self.client.logout()
        self.assertEqual(self.client.get(url, HTTP_X_SITE_NAME="gepdec_solar").status_code, 401)
        for user, code in ((self.member, 204), (self.admin, 204), (self.outsider, 403)):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url, HTTP_X_SITE_NAME="gepdec_solar").status_code, code, user)
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(url, HTTP_X_SITE_NAME="no_such_site").status_code, 403)
        self.assertEqual(self.client.get(url, HTTP_X_SITE_NAME="../etc").status_code, 403)

    def test_planwise_cookie_names(self):
        self.assertEqual((settings.SESSION_COOKIE_NAME, settings.CSRF_COOKIE_NAME),
                         ("planwise_sessionid", "planwise_csrftoken"))
        self.client.force_login(self.member)
        self.assertIn("planwise_sessionid", self.client.cookies)


class SitesDisabledTests(TestCase):
    @mock.patch.dict("os.environ", {"GATEWAY_URL": "", "GATEWAY_ADMIN_TOKEN": ""})
    def test_without_gateway_link_is_saved_but_marked(self):
        admin = User.objects.create_superuser("boss2", password="x")
        project = Project.objects.create(key="P9", name="P9", status="active", owner=admin)
        link = ProjectLink.objects.create(project=project, title="UAT", url="http://10.0.0.1:80/",
                                          via_gateway=True, site_name="p9_uat")
        self.assertFalse(sites.push(link))
        self.assertIn("not configured", link.gateway_error)
        self.assertEqual(sites.sync_all(), (0, 0))


class SitesReplaceTests(SitesTests):
    """The 'Replace text' option: for addresses an app builds at run time (FV: `${hostname}:8000`)."""

    def test_replace_text_saved_and_published(self):
        rule = "`${window.location.protocol}//${window.location.hostname}:8000/api/v1` ==> " \
               "`${window.location.origin}/sites/gepdec_solar/_api/api/v1`"
        self._save(replace=rule)
        link = ProjectLink.objects.get()
        self.assertEqual(link.gateway_options["replace"], [{
            "from": "`${window.location.protocol}//${window.location.hostname}:8000/api/v1`",
            "to": "`${window.location.origin}/sites/gepdec_solar/_api/api/v1`"}])
        self.assertEqual(self.gateway.apps["gepdec_solar"]["replace"], link.gateway_options["replace"])
        self.assertEqual(sites.sync_all(), (0, 0))  # in sync: nothing re-sent

    def test_bad_replace_rule_refused(self):
        self._save(replace="no arrow here")
        self.assertFalse(ProjectLink.objects.exists())


class SitesFilesOnlyTests(SitesTests):
    def test_files_only_option_published(self):
        self._save(js_files_only="on")
        link = ProjectLink.objects.get()
        self.assertTrue(link.gateway_options["js_files_only"])
        self.assertEqual(self.gateway.apps["gepdec_solar"]["js_paths"], "files")
        self._save(link, js_files_only=None)
        self.assertNotIn("js_paths", self.gateway.apps["gepdec_solar"])


class SitesScriptNameTests(SitesTests):
    def test_script_name_turns_off_body_rewrites(self):
        self._save(script_name="on", js_files_only="on")
        app = self.gateway.apps["gepdec_solar"]
        self.assertEqual((app["script_name"], app["rewrite_html"], app["rewrite_js"], app["isolate_storage"]),
                         (True, False, False, False))
        self.assertNotIn("js_paths", app)
