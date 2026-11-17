"""One-time login codes: after the password is right, a 6-digit code is emailed and must be typed in before the
person is signed in. Every sign-in, every time — nothing is remembered.

The half-finished sign-in lives in the server-side session (never in a cookie the browser can edit):
the user id, only a keyed hash of the code (never the code), when it expires, and counters for wrong guesses
and re-sends. Off unless LOGIN_OTP_ENABLED=1; LOGIN_OTP_USERS (comma-separated usernames) limits it to a pilot."""
import hashlib
import hmac
import logging
import secrets
import time

from django.conf import settings
from django.utils.html import escape

logger = logging.getLogger(__name__)

PENDING_KEY = "login_otp_pending"
VERIFIED_KEY = "login_otp_verified"  # set on the session only after the right code was typed
SUBJECT = "Your Planwise login code"

NO_EMAIL = "Your account has no email address, so a login code can't be sent. Please ask an administrator to add it."
SEND_FAILED = "We couldn't email your login code. Please try again in a minute, or ask an administrator."


def enabled():
    return bool(settings.LOGIN_OTP_ENABLED)


def applies_to(user):
    """Whether this person must enter a code: the feature is on, and (if a pilot list is set) they are on it."""
    if not enabled():
        return False
    pilot = settings.LOGIN_OTP_USERS
    return not pilot or user.get_username().lower() in pilot


def mask(email):
    name, _, domain = (email or "").partition("@")
    return f"{name[:1]}{'*' * max(len(name) - 1, 2)}@{domain}" if domain else ""


def _digest(user_id, code):
    return hmac.new(settings.SECRET_KEY.encode(), f"{user_id}:{code}".encode(), hashlib.sha256).hexdigest()


def _email_html(name, code):
    minutes = settings.LOGIN_OTP_TTL_SECONDS // 60
    return (
        '<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;line-height:1.6;color:#101828">'
        f"<p>Hi {escape(name)},</p>"
        f"<p>Your Planwise login code is:</p>"
        f'<p style="font-size:28px;font-weight:700;letter-spacing:6px;margin:8px 0">{escape(code)}</p>'
        f"<p>It works once and expires in {minutes} minutes. If you didn't try to sign in, ignore this email "
        "and tell an administrator.</p>"
        "<p>Thanks,<br>Planwise · Augtrans</p></div>"
    )


def _issue(request, pending, user):
    """Make a new code, store its hash in `pending`, mail it. -> (ok, error text)."""
    from apps.timesheet import mailer

    code = f"{secrets.randbelow(1_000_000):06d}"
    ok, error = mailer.send_mail(user.email.strip(), SUBJECT, _email_html(user.get_full_name() or user.username, code))
    if not ok:
        logger.warning("login code mail to user %s failed: %s", user.pk, error[:200])
        return False, SEND_FAILED
    now = time.time()
    pending.update(digest=_digest(user.pk, code), issued=now, expires=now + settings.LOGIN_OTP_TTL_SECONDS, attempts=0)
    request.session[PENDING_KEY] = pending
    return True, ""


def begin(request, user, next_url="", backend=""):
    """Password was correct: start the code step instead of signing in. -> (ok, error text)."""
    request.session.pop(PENDING_KEY, None)
    if not (user.email or "").strip():
        return False, NO_EMAIL
    pending = {"uid": user.pk, "backend": backend or getattr(user, "backend", ""), "next": next_url, "resends": 0}
    return _issue(request, pending, user)


def pending(request):
    """The half-finished sign-in, or None if there isn't one or it has expired."""
    data = request.session.get(PENDING_KEY)
    if not data or data.get("expires", 0) < time.time():
        request.session.pop(PENDING_KEY, None)
        return None
    return data


def clear(request):
    request.session.pop(PENDING_KEY, None)


def check(request, data, code):
    """-> "ok", "wrong" or "locked". A wrong guess is counted; too many wipes the sign-in."""
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if hmac.compare_digest(_digest(data["uid"], code), data["digest"]):
        return "ok"
    data["attempts"] = data.get("attempts", 0) + 1
    if data["attempts"] >= settings.LOGIN_OTP_MAX_ATTEMPTS:
        clear(request)
        return "locked"
    request.session[PENDING_KEY] = data
    return "wrong"


def resend(request, data, user):
    """-> (ok, error text). At most one every LOGIN_OTP_RESEND_SECONDS and a few per sign-in."""
    wait = settings.LOGIN_OTP_RESEND_SECONDS - (time.time() - data.get("issued", 0))
    if wait > 0:
        return False, f"Please wait {int(wait) + 1} seconds before asking for another code."
    if data.get("resends", 0) >= settings.LOGIN_OTP_MAX_RESENDS:
        clear(request)
        return False, "Too many codes requested. Please sign in again."
    data["resends"] = data.get("resends", 0) + 1
    return _issue(request, data, user)
