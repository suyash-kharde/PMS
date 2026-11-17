"""Token authentication + a small fixed-window rate limit for the MCP endpoint."""

from django.core.cache import cache

from apps.projects.selectors import visible_projects

from .models import ProjectMCPToken, hash_token

RATE_LIMIT_PER_MIN = 120


class AuthError(Exception):
    """Raised with an HTTP status + message when a request cannot be authenticated."""

    def __init__(self, message, status=401):
        super().__init__(message)
        self.message = message
        self.status = status


def _raw_token(request, url_token):
    # a well-formed Authorization header wins, so `/mcp/` + header works and a stray
    # path segment never shadows it; otherwise the token in the URL path is used
    header = request.META.get("HTTP_AUTHORIZATION", "")
    if header[:7].lower() == "bearer ":
        return header[7:].strip()
    return url_token or ""


def authenticate(request, url_token):
    """-> a live ProjectMCPToken, or raises AuthError.

    The token may arrive in the URL path or as `Authorization: Bearer`. The bound
    user must still be active and still able to see the project.
    """
    raw = _raw_token(request, url_token)
    if not raw:
        raise AuthError("Missing MCP token.")

    token = (
        ProjectMCPToken.objects.select_related("project", "created_by")
        .filter(token_hash=hash_token(raw))
        .first()
    )
    if token is None:
        raise AuthError("Unknown MCP token.")
    if not token.is_active:
        raise AuthError(f"This MCP link is {token.status_label}.", status=403)

    user = token.created_by
    if user is None or not user.is_active:
        raise AuthError("The user this MCP link belongs to is no longer active.", status=403)
    if not visible_projects(user).filter(pk=token.project_id).exists():
        raise AuthError("This MCP link's user can no longer access the project.", status=403)

    _enforce_rate_limit(token)
    token.touch()
    return token


def _enforce_rate_limit(token):
    from django.utils import timezone

    window = timezone.now().strftime("%Y%m%d%H%M")
    key = f"mcp:rl:{token.pk}:{window}"
    cache.add(key, 0, 90)
    try:
        count = cache.incr(key)
    except ValueError:  # key expired between add and incr
        cache.set(key, 1, 90)
        count = 1
    if count > RATE_LIMIT_PER_MIN:
        raise AuthError("Rate limit exceeded — slow down (max 120 requests/minute).", status=429)
