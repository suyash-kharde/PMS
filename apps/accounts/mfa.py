"""Multi-factor authentication with an authenticator app (standard TOTP, RFC 6238): Microsoft Authenticator, Google
Authenticator, Authy and the rest all work. No outside service is involved — Planwise makes each person's secret,
shows it once as a QR code, and later checks the 6-digit code itself against the clock.

Who needs it is decided by a company-wide choice (Security page: off / only people ticked / everyone) plus a
checkbox on each person. MFA_FORCE_OFF=1 in .env switches the whole thing off, for emergencies."""
import base64
import hashlib
import hmac
import json
import secrets
import struct
import time
from urllib.parse import quote

from django.conf import settings
from django.utils import timezone

PENDING_KEY = "login_mfa_pending"
STEP = 30
WINDOW = 1  # also accept the code from one step before / after, for slightly wrong phone clocks
BACKUP_COUNT = 8
ISSUER = "Planwise"


def mode():
    """"off", "selected" (only people with the checkbox) or "everyone"."""
    if settings.MFA_FORCE_OFF:
        return "off"
    from . import site_settings

    return site_settings.mfa_mode()


def required_for(user):
    m = mode()
    return m == "everyone" or (m == "selected" and bool(user.mfa_required))


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def provisioning_uri(secret, username):
    label = quote(f"{ISSUER}:{username}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(ISSUER)}&algorithm=SHA1&digits=6&period={STEP}"


def _code_for(secret, step):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 15
    number = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{number:06d}"


def verify(secret, code, last_step=0, now=None):
    """-> the time step the code belongs to (> 0) when it is right and not used before, else 0."""
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(code) != 6 or not secret:
        return 0
    current = int((time.time() if now is None else now) // STEP)
    for step in range(current - WINDOW, current + WINDOW + 1):
        if step > last_step and hmac.compare_digest(_code_for(secret, step), code):
            return step
    return 0


# ---- backup codes: shown once, kept only as hashes, each works once
def _hash(code):
    clean = "".join(ch for ch in (code or "").lower() if ch.isalnum())
    return hmac.new(settings.SECRET_KEY.encode(), f"backup:{clean}".encode(), hashlib.sha256).hexdigest()


def make_backup_codes():
    return [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(BACKUP_COUNT)]


def store_backup_codes(user, codes):
    user.mfa_backup_codes = json.dumps([_hash(c) for c in codes])


def use_backup_code(user, code):
    """-> True and the code is spent when it matches one of the person's unused backup codes."""
    try:
        hashes = json.loads(user.mfa_backup_codes or "[]")
    except ValueError:
        hashes = []
    wanted = _hash(code)
    for stored in hashes:
        if hmac.compare_digest(stored, wanted):
            hashes.remove(stored)
            user.mfa_backup_codes = json.dumps(hashes)
            user.save(update_fields=["mfa_backup_codes"])
            return True
    return False


# ---- the half-finished sign-in (kept in the server-side session, like the email code step)
def begin(request, user, next_url="", backend=""):
    from . import otp

    otp.clear(request)
    data = {"uid": user.pk, "backend": backend or getattr(user, "backend", ""), "next": next_url, "attempts": 0,
            "expires": time.time() + 15 * 60}
    if not user.mfa_secret:
        data["setup_secret"] = new_secret()  # saved to the person only once they prove the app works
    request.session[PENDING_KEY] = data


def pending(request):
    data = request.session.get(PENDING_KEY)
    if not data or data.get("expires", 0) < time.time():
        request.session.pop(PENDING_KEY, None)
        return None
    return data


def clear(request):
    request.session.pop(PENDING_KEY, None)


def fail(request, data):
    """Count a wrong code. -> True when it was the last allowed try (the sign-in is wiped)."""
    data["attempts"] = data.get("attempts", 0) + 1
    if data["attempts"] >= settings.LOGIN_OTP_MAX_ATTEMPTS:
        clear(request)
        return True
    request.session[PENDING_KEY] = data
    return False


def confirm_setup(user, secret, step):
    """The first code worked: keep the secret (encrypted), remember the step, make backup codes. -> the codes."""
    from apps.vms import crypto

    codes = make_backup_codes()
    user.mfa_secret = crypto.encrypt(secret)
    user.mfa_confirmed_at = timezone.now()
    user.mfa_last_step = step
    store_backup_codes(user, codes)
    user.save(update_fields=["mfa_secret", "mfa_confirmed_at", "mfa_last_step", "mfa_backup_codes"])
    return codes


def reset(user):
    user.mfa_secret = ""
    user.mfa_confirmed_at = None
    user.mfa_backup_codes = ""
    user.mfa_last_step = 0
    user.save(update_fields=["mfa_secret", "mfa_confirmed_at", "mfa_backup_codes", "mfa_last_step"])
