"""Runtime permission resolution.

effective_caps(user) -> {capability_code: scope}

Resolution order (later wins, but a wider scope never gets narrowed by inheritance):
  1. role ancestors, most senior first
  2. the user's own role
  3. per-user overrides (deny removes the capability outright)
Superusers get every capability at scope "all".
"""

from django.core.cache import cache

from .models import SCOPE_ALL, SCOPE_OWN, SCOPE_RANK, SCOPE_TEAM, Capability

CACHE_TTL = 120


def _cache_key(user):
    return f"caps:u{user.pk}:r{user.role_id or 0}"


def role_scopes(user):
    """{capability_code: scope} that this person's role tree grants on its own, before any personal
    override. The permissions page shows it next to what was given to them personally."""
    caps = {}
    if user.role_id:
        # ancestors() returns self-first; walk most senior -> self so the closest role wins
        for role in reversed(user.role.ancestors()):
            for rc in role.role_caps.select_related("capability"):
                code, scope = rc.capability.code, rc.scope
                if code not in caps or SCOPE_RANK[scope] > SCOPE_RANK[caps[code]]:
                    caps[code] = scope
    return caps


def effective_caps(user, use_cache=True):
    if not user or not user.is_authenticated:
        return {}
    if user.is_superuser:
        return {c: SCOPE_ALL for c in Capability.objects.values_list("code", flat=True)}

    key = _cache_key(user)
    if use_cache:
        cached = cache.get(key)
        if cached is not None:
            return cached

    caps = role_scopes(user)

    for uc in user.cap_overrides.select_related("capability"):
        if uc.deny:
            caps.pop(uc.capability.code, None)
        else:
            caps[uc.capability.code] = uc.scope

    if use_cache:
        cache.set(key, caps, CACHE_TTL)
    return caps


def invalidate(user=None):
    if user is not None:
        cache.delete(_cache_key(user))
    else:
        cache.clear()


def scope_of(user, code):
    if not user or not user.is_authenticated:
        return None
    if user.is_superuser:
        return SCOPE_ALL
    caps = getattr(user, "caps", None)
    if caps is None:
        caps = effective_caps(user)
    return caps.get(code)


def has_cap(user, code, obj=None):
    """True when `user` holds `code`; when `obj` is given the scope is enforced against it."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    caps = getattr(user, "caps", None)
    if caps is None:
        caps = effective_caps(user)
    scope = caps.get(code)
    if scope is None:
        return False
    if obj is None or scope == SCOPE_ALL:
        return True
    if scope == SCOPE_OWN:
        return _owns(user, obj)
    if scope == SCOPE_TEAM:
        return _in_team_reach(user, obj)
    return False


def _people_of(obj):
    ids = set()
    for attr in ("owner_id", "assignee_id", "reporter_id", "author_id", "user_id", "lead_id", "manager_id"):
        val = getattr(obj, attr, None)
        if val:
            ids.add(val)
    if hasattr(obj, "pk") and obj.__class__.__name__ == "User":
        ids.add(obj.pk)
    return ids


def _owns(user, obj):
    if user.pk in _people_of(obj):
        return True
    members = getattr(obj, "members", None)
    if members is not None and hasattr(members, "filter"):
        try:
            return members.filter(pk=user.pk).exists()
        except Exception:
            return False
    return False


def _in_team_reach(user, obj):
    if _owns(user, obj):
        return True
    reach = user.subordinate_ids()
    if _people_of(obj) & reach:
        return True
    team_ids = user.team.descendant_ids() if user.team_id else set()
    obj_team = getattr(obj, "team_id", None)
    if obj_team and obj_team in team_ids:
        return True
    project = getattr(obj, "project", None)
    if project is not None:
        return _in_team_reach(user, project)
    return False


def require(user, code, obj=None):
    from django.core.exceptions import PermissionDenied

    if not has_cap(user, code, obj):
        raise PermissionDenied(f"Missing capability: {code}")
    return True
