"""Admin-editable settings kept in the database (accounts.SiteSetting), read through a short cache so the
per-request check in the middleware costs nothing. Saving clears the cache, so a change applies straight away."""
from django.conf import settings
from django.core.cache import cache

IDLE_KEY = "idle_timeout_seconds"
CACHE_KEY = "site-setting:idle-timeout"
MIN_SECONDS = 5 * 60            # 5 minutes
MAX_SECONDS = 7 * 24 * 60 * 60  # 7 days


def _stored_idle_seconds():
    from .models import SiteSetting

    row = SiteSetting.objects.filter(key=IDLE_KEY).first()
    try:
        value = int(row.value) if row else 0
    except ValueError:
        value = 0
    return value if MIN_SECONDS <= value <= MAX_SECONDS else 0  # 0 = nothing (valid) saved


def idle_timeout_seconds():
    """How long someone can sit idle before being signed out. The admin's saved choice, else the built-in default."""
    try:
        cached = cache.get(CACHE_KEY)
        if cached is None:
            cached = _stored_idle_seconds()
            cache.set(CACHE_KEY, cached, 300)
    except Exception:  # noqa: BLE001 - a settings lookup must never take the site down
        cached = 0
    return cached or settings.SESSION_IDLE_TIMEOUT_SECONDS


def set_idle_timeout_seconds(seconds, user=None):
    from .models import SiteSetting

    SiteSetting.objects.update_or_create(key=IDLE_KEY, defaults={"value": str(int(seconds)), "updated_by": user})
    cache.delete(CACHE_KEY)


def describe(seconds):
    """3600 -> "1 hour", 5400 -> "1 hour 30 minutes", 1800 -> "30 minutes"."""
    hours, minutes = divmod(int(seconds) // 60, 60)
    parts = []
    if hours >= 24 and hours % 24 == 0:
        days = hours // 24
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    elif hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    return " ".join(parts) or "0 minutes"


MFA_KEY = "mfa_mode"
MFA_CACHE_KEY = "site-setting:mfa-mode"
MFA_MODES = ("off", "selected", "everyone")


def mfa_mode():
    """"off" (default), "selected" (people with the checkbox) or "everyone"."""
    from .models import SiteSetting

    try:
        value = cache.get(MFA_CACHE_KEY)
        if value is None:
            row = SiteSetting.objects.filter(key=MFA_KEY).first()
            value = row.value if row and row.value in MFA_MODES else "off"
            cache.set(MFA_CACHE_KEY, value, 300)
    except Exception:  # noqa: BLE001 - never take the site down over a settings lookup
        value = "off"
    return value


def set_mfa_mode(value, user=None):
    from .models import SiteSetting

    if value not in MFA_MODES:
        raise ValueError(value)
    SiteSetting.objects.update_or_create(key=MFA_KEY, defaults={"value": value, "updated_by": user})
    cache.delete(MFA_CACHE_KEY)
