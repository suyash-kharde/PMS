import html
import logging
import re

from django.conf import settings
from django.contrib.auth import logout
from django.db.models import F
from django.shortcuts import redirect
from django.utils import timezone

from . import site_settings
from .permissions import effective_caps

ACTIVITY_THROTTLE = timezone.timedelta(minutes=1)

# background polls that happen whether or not a person is actually at the keyboard —
# never count these as activity, or an abandoned-but-open tab would stay "active" forever
IDLE_EXCLUDED_PATHS = {"/api/notifications/unread/"}
IDLE_TOUCH_THROTTLE = 60  # seconds; avoid a session write on every single request
# two page loads at most this far apart count as continuous work for "time on Planwise today";
# a longer gap means the person stepped away, so that stretch is not counted
ACTIVE_GAP_SECONDS = 5 * 60

logger = logging.getLogger(__name__)

# page visits: only real pages a person opens, not assets, APIs or background fetches
VISIT_SKIP_PREFIXES = ("/api/", "/static/", "/media/", "/admin/jsi18n/", "/accounts/login", "/login", "/logout")
VISIT_REPEAT_SECONDS = 60  # the same page re-opened (or refreshed) within this is not logged again
VISIT_KEEP_DAYS = 90
_TITLE_RE = re.compile(rb"<title>(.*?)</title>", re.S | re.I)
_PROJECT_PATH_RE = re.compile(r"^/projects/(\d+)/")
# detail pages whose URL has no project id: look the project up from the object itself
_VISIT_OBJECT_MODELS = {
    "tickets:detail": "tickets.Ticket",
    "projects:task_detail": "projects.Task",
    "sprints:sprint_detail": "sprints.Sprint",
    "sprints:requirement_detail": "sprints.Requirement",
    "intake:entry_detail": "intake.TimelineEntry",
}


class CapabilityMiddleware:
    """Attach the resolved capability map to the request user once per request,
    and log out anyone idle past the auto sign-out time an admin set (site_settings.idle_timeout_seconds)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            if self._idle_timed_out(request):
                request.idle_signed_out = True  # not a sign-out the person chose; the timesheet must not log it as one
                logout(request)
                return redirect(f"{settings.LOGIN_URL}?timeout=1")
            if request.path not in IDLE_EXCLUDED_PATHS:
                self._touch_idle_timer(request, user)
            user.caps = effective_caps(user)
            self._touch_last_active(user)
        response = self.get_response(request)
        if user is not None and user.is_authenticated:
            self._record_visit(request, response, user)
        return response

    @staticmethod
    def _idle_timed_out(request):
        last_seen = request.session.get("idle_last_seen")
        if last_seen is None:
            return False
        idle_for = timezone.now().timestamp() - last_seen
        return idle_for > site_settings.idle_timeout_seconds()

    @staticmethod
    def _touch_idle_timer(request, user):
        now = timezone.now().timestamp()
        last_seen = request.session.get("idle_last_seen")
        if last_seen is not None and now - last_seen < IDLE_TOUCH_THROTTLE:
            return
        request.session["idle_last_seen"] = now
        gap = now - last_seen if last_seen is not None else 0
        CapabilityMiddleware._record_active_time(user, int(gap) if gap <= ACTIVE_GAP_SECONDS else 0)

    @staticmethod
    def _record_active_time(user, seconds):
        """Add seconds to the person's time-on-Planwise for today. Never breaks the request."""
        from .models import UserDayActivity

        try:
            today = timezone.localdate()
            row, created = UserDayActivity.objects.get_or_create(
                user_id=user.pk, date=today, defaults={"active_seconds": seconds}
            )
            if not created and seconds:
                UserDayActivity.objects.filter(pk=row.pk).update(active_seconds=F("active_seconds") + seconds)
            if created:  # first activity of the day: a good moment to drop this person's old visits
                from .models import PageVisit

                cutoff = timezone.now() - timezone.timedelta(days=VISIT_KEEP_DAYS)
                PageVisit.objects.filter(user_id=user.pk, visited_at__lt=cutoff).delete()
        except Exception:  # noqa: BLE001 - activity tracking must never block a page load
            logger.exception("recording active time failed for user %s", user.pk)

    @staticmethod
    def _record_visit(request, response, user):
        """Log a full page the person opened. Never breaks the request."""
        try:
            if request.method != "GET" or response.status_code != 200:
                return
            if getattr(response, "streaming", False) or "text/html" not in response.get("Content-Type", ""):
                return
            if request.headers.get("HX-Request") or request.headers.get("X-Requested-With"):
                return  # partial / background fetch, not a page the person opened
            path = request.path
            if path in IDLE_EXCLUDED_PATHS or path.startswith(VISIT_SKIP_PREFIXES):
                return
            full_path = request.get_full_path()[:300]
            now = timezone.now().timestamp()
            last = request.session.get("last_visit")
            if last and last[0] == full_path and now - last[1] < VISIT_REPEAT_SECONDS:
                return
            request.session["last_visit"] = [full_path, now]

            title = ""
            match = _TITLE_RE.search(response.content[:6000])
            if match:
                title = html.unescape(match.group(1).decode("utf-8", "ignore")).strip()
                title = re.sub(r"\s*·\s*[^·]*$", "", title) or title  # drop the " · Planwise" suffix
            tab = request.GET.get("tab")
            if tab:
                title = f"{title} — {tab.replace('_', ' ').title()} tab"
            project_match = _PROJECT_PATH_RE.match(path)
            project_id = int(project_match.group(1)) if project_match else None
            match_info = getattr(request, "resolver_match", None)
            if project_id is None and match_info and match_info.view_name in _VISIT_OBJECT_MODELS:
                from django.apps import apps

                model = apps.get_model(_VISIT_OBJECT_MODELS[match_info.view_name])
                project_id = model.objects.filter(pk=match_info.kwargs.get("pk")).values_list("project_id", flat=True).first()

            from .models import PageVisit

            PageVisit.objects.create(
                user_id=user.pk,
                project_id=project_id,
                path=full_path,
                title=title[:200],
            )
        except Exception:  # noqa: BLE001 - visit logging must never block a page load
            logger.exception("recording page visit failed for user %s", user.pk)

    @staticmethod
    def _touch_last_active(user):
        """Stamp last_active, throttled so we issue at most one UPDATE/minute/user."""
        now = timezone.now()
        if user.last_active and now - user.last_active < ACTIVITY_THROTTLE:
            return
        user.last_active = now
        user.__class__.objects.filter(pk=user.pk).update(last_active=now)


class PasswordChangeRequiredMiddleware:
    """Anyone whose account is flagged (User.must_change_password) is sent to Change password until
    they set their own. Sign-out, static files and token-based endpoints (MCP) are left alone;
    background JSON calls get a 403 instead of a redirect."""

    ALLOWED_PREFIXES = ("/password/", "/logout/", "/login/", "/mfa/", "/static/", "/media/", "/sw.js", "/mcp/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (user is not None and user.is_authenticated and getattr(user, "must_change_password", False)
                and not request.path.startswith(self.ALLOWED_PREFIXES)):
            from django.http import JsonResponse
            from django.urls import reverse
            from django.utils.http import urlencode

            if request.path.startswith("/api/") or request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"error": "password_change_required"}, status=403)
            target = reverse("accounts:password_change")
            if request.method == "GET" and request.path != "/":
                target += "?" + urlencode({"next": request.get_full_path()})
            return redirect(target)
        return self.get_response(request)


class CustomerLinksOnlyMiddleware:
    """A client-side login (the Customer role) sees three things in Planwise: their Dashboard, the
    Projects assigned to them, and each project's Project Links page, where they open the project's sites and
    sign in with the sample logins. Every other page is closed to them — they are sent back to Projects (a
    project's own page goes straight to its links), and background calls get a 403. Staff accounts and
    superusers are not affected."""

    ALLOWED_PREFIXES = ("/password/", "/logout/", "/login/", "/mfa/", "/static/", "/sw.js", "/api/sites/auth/")
    ALLOWED_EXACT = {"/", "/projects/", "/api/notifications/unread/"}
    LINKS_RE = re.compile(r"^/projects/\d+/links/(\d+/password/)?$")
    PROJECT_HOME_RE = re.compile(r"^/projects/(\d+)/$")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated or not user.is_client_login:
            return self.get_response(request)
        path = request.path
        if path in self.ALLOWED_EXACT or path.startswith(self.ALLOWED_PREFIXES) or self.LINKS_RE.match(path):
            return self.get_response(request)
        home = self.PROJECT_HOME_RE.match(path)
        if home and request.method == "GET":
            return redirect("projects:project_links", home.group(1))
        if path.startswith("/api/") or request.headers.get("x-requested-with") == "XMLHttpRequest":
            from django.http import JsonResponse

            return JsonResponse({"error": "not_available"}, status=403)
        return redirect("projects:list")


class OtpRequiredMiddleware:
    """When login codes apply to a person, a session that did not come through the code step is not allowed
    to stay signed in. This closes every other way in — a session created before codes were switched on, or
    any sign-in path that forgot the code step: such a session is signed out and sent to the sign-in page."""

    ALLOWED_PREFIXES = ("/login/", "/logout/", "/static/", "/sw.js")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from . import mfa, otp

        user = getattr(request, "user", None)
        if (user is not None and user.is_authenticated and (otp.applies_to(user) or mfa.required_for(user))
                and not request.session.get(otp.VERIFIED_KEY)
                and not request.path.startswith(self.ALLOWED_PREFIXES)):
            request.idle_signed_out = True  # not a sign-out the person chose; the timesheet must not log it as one
            logout(request)
            if request.path.startswith("/api/") or request.headers.get("x-requested-with") == "XMLHttpRequest":
                from django.http import JsonResponse

                return JsonResponse({"error": "login_code_required"}, status=401)
            return redirect(f"{settings.LOGIN_URL}?otp=required")
        return self.get_response(request)
