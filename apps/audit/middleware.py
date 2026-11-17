import json
import logging
import re
from urllib.parse import unquote

from .context import rows_this_request, set_current_user, start_request
from .generic import MODULE_LABELS
from .services import log

logger = logging.getLogger(__name__)

# not worth a row, or already logged by the sign-in signals, or carrying secrets
SKIP_PREFIXES = ("/static/", "/media/", "/login/", "/logout/", "/signup/", "/password/", "/mfa/", "/notifications/")
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")
_SECRET = re.compile(r"pass|secret|token|otp|api[_-]?key|private|credential|totp|verification|ssh|authoriz|cookie", re.I)
_MAX_VALUE = 80
_MAX_DETAILS = 1500


class CurrentUserMiddleware:
    """Stashes the request's user so the generic audit signal handlers can attribute a save/delete to someone,
    without Django's signals ever seeing the request. After the response it also records what the person did
    that no saved record shows: file downloads, and form submissions that changed nothing, were refused or failed
    (a submission that did save something is already listed by its own create/edit/delete rows)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        set_current_user(user if user is not None and user.is_authenticated else None)
        start_request()
        try:
            response = self.get_response(request)
            try:
                _record(request, response)
            except Exception:  # noqa: BLE001 - recording must never break the page
                logger.exception("activity recording failed for %s", getattr(request, "path", "?"))
            return response
        finally:
            set_current_user(None)  # the person must not stay "current" once their request is over


def _short(value):
    text = " ".join(str(value).split())
    return text if len(text) <= _MAX_VALUE else text[: _MAX_VALUE - 1] + "…"


def _pairs(items):
    """"name=value, other=value" with secrets masked and long values cut."""
    out = []
    for key, value in items:
        if key == "csrfmiddlewaretoken":
            continue
        out.append(f"{key}=•••" if _SECRET.search(key) else f"{key}={_short(value)}")
    return ", ".join(out)


def _submitted(request):
    """What the person sent: form fields, a JSON body's top-level fields, or uploaded file names."""
    if request.method == "GET":
        return _pairs((k, ", ".join(request.GET.getlist(k))) for k in request.GET)
    parts = []
    content_type = request.META.get("CONTENT_TYPE", "")
    if content_type.startswith("application/json"):
        try:
            body = json.loads(request.body.decode("utf-8") or "{}")
            if isinstance(body, dict):
                parts.append(_pairs((k, v if not isinstance(v, (dict, list)) else "…") for k, v in body.items()))
        except Exception:  # noqa: BLE001 - body already read, or not JSON
            pass
    else:
        parts.append(_pairs((k, ", ".join(request.POST.getlist(k))) for k in request.POST))
    if request.FILES:
        parts.append("files: " + ", ".join(f"{f.name} ({f.size} bytes)" for f in request.FILES.values()))
    return "; ".join(p for p in parts if p)


def _error_messages(request):
    """Error texts the page queued for the person (e.g. "There is already a holiday on 02 Oct 2026"), without
    marking them as shown."""
    from django.contrib import messages

    try:
        storage = messages.get_messages(request)
        queued = list(getattr(storage, "_queued_messages", []))
    except Exception:  # noqa: BLE001
        return []
    return [str(m.message) for m in queued if m.level >= messages.ERROR]


def _module(request):
    match = request.resolver_match
    app = (match.namespace.split(":")[0] if match and match.namespace else "") or request.path.strip("/").split("/")[0]
    return MODULE_LABELS.get(app, app or "org")[:40]


def _record(request, response):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or request.path.startswith(SKIP_PREFIXES):
        return
    status = response.status_code
    disposition = response.get("Content-Disposition", "") if hasattr(response, "get") else ""
    if disposition.lower().startswith("attachment") and status < 400:
        found = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', disposition)
        filename = unquote(found.group(1)) if found else "file"
        filters = _submitted(request)
        log(user, "download", _module(request), target=filename, details=f"Filters: {filters}" if filters else "")
        return
    if request.method not in WRITE_METHODS:
        return
    errors = _error_messages(request)
    failed = status >= 400 or bool(errors)
    if rows_this_request() and not failed:
        return  # its saved records are listed already, with what changed
    match = request.resolver_match
    label = (match.url_name if match and match.url_name else "form submission").replace("_", " ").replace("-", " ")
    details = []
    if status >= 400:
        details.append(f"HTTP {status}")
    details += errors
    sent = _submitted(request)
    if sent:
        details.append(f"Entered: {sent}")
    log(user, "action", _module(request), target=label.capitalize(), status="failed" if failed else "success",
        details=" · ".join(details)[:_MAX_DETAILS])
