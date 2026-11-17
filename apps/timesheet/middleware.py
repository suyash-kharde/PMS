"""Two small per-request jobs for the timesheet; neither changes the response:
  * remembers who is making the request, so a project status change saved deep inside another app
    can be recorded with the person who made it;
  * saves each person's first visit of the day as their first login time (people usually stay
    signed in across days, so a real sign-in does not happen every morning)."""
import logging
from contextvars import ContextVar

from django.utils import timezone

logger = logging.getLogger(__name__)

# background calls that happen with nobody at the keyboard, and files: not a visit
_SKIP_PREFIXES = ("/static/", "/media/", "/api/", "/favicon")

_current_user = ContextVar("timesheet_current_user", default=None)


def current_user():
    return _current_user.get()


class CurrentUserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        token = _current_user.set(user)
        try:
            if user is not None and user.is_authenticated and not request.path.startswith(_SKIP_PREFIXES):
                self._note_first_visit(request, user)
            return self.get_response(request)
        finally:
            _current_user.reset(token)

    @staticmethod
    def _note_first_visit(request, user):
        """At most twice a day per session: the first visit of the day, and the first visit inside
        the attendance window, any time before 18:30 (which makes the day Present)."""
        try:
            from . import services

            now = timezone.now()
            today = timezone.localdate().isoformat()
            start, end = services.work_window(timezone.localdate())
            in_window = start <= now < end
            first = request.session.get("ts_seen") != today
            window = in_window and request.session.get("ts_seen_window") != today
            if not (first or window):
                return
            services.record_login(user, now)
            request.session["ts_seen"] = today
            if in_window:
                request.session["ts_seen_window"] = today
        except Exception:  # noqa: BLE001 - attendance tracking must never block a page
            logger.exception("timesheet: recording first visit failed for %s", getattr(user, "pk", None))
