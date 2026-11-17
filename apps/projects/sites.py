"""Project sites through Planwise: https://<planwise>/sites/<name>/ -> the project's VM.

The traffic itself never touches Django. nginx sends /sites/... to the separate gateway container
(gateway/, Go), after asking `site_allowed` (via the /api/sites/auth/ view) whether the signed-in
person may see that project. Planwise is the source of truth for which sites exist: saving a
Project Link pushes it to the gateway's admin API, and the worker re-pushes anything not synced
(e.g. the gateway was down, or restarted with an empty volume).

If the gateway is down, only /sites/ is affected; Planwise keeps working and saving links still works.
"""
import logging
import os
import re

from django.utils import timezone

logger = logging.getLogger(__name__)

SITE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
MOUNT_PATH_RE = re.compile(r"^/[a-z0-9_-]{1,32}$")
DEFAULT_OPTIONS = {"rewrite_html": True, "rewrite_js": True, "isolate_storage": True, "insecure": False,
                   "sync_tokens": "", "mounts": [], "replace": [], "js_files_only": False, "script_name": False}
REPLACE_SEP = "==>"
TIMEOUT = 5


def gateway_url():
    return os.environ.get("GATEWAY_URL", "").rstrip("/")


def enabled():
    return bool(gateway_url() and os.environ.get("GATEWAY_ADMIN_TOKEN"))


def suggest_name(project, title):
    from django.utils.text import slugify

    base = slugify(f"{project.key}-{title}").replace("-", "_")[:32].strip("_-") or "site"
    return base if SITE_NAME_RE.match(base) else "site"


def options_for(link):
    return {**DEFAULT_OPTIONS, **(link.gateway_options or {})}


def app_payload(link):
    """The gateway's App JSON for one link."""
    opts = options_for(link)
    payload = {
        "name": link.site_name,
        "target": link.target_origin,
        "insecure": bool(opts["insecure"]),
        "rewrite_html": bool(opts["rewrite_html"]),
        "rewrite_js": bool(opts["rewrite_js"]),
        "isolate_storage": bool(opts["isolate_storage"]) and bool(opts["rewrite_js"]),
        "mounts": [{"path": m["path"], "target": m["target"]} for m in opts.get("mounts") or []],
    }
    if opts.get("js_files_only"):
        payload["js_paths"] = "files"
    if opts.get("script_name"):
        # the app builds every URL with the prefix itself (pgAdmin, Flask/WSGI apps): no body rewrites
        payload.update(script_name=True, rewrite_html=False, rewrite_js=False, isolate_storage=False)
        payload.pop("js_paths", None)
    if opts.get("replace"):
        payload["replace"] = [{"from": r["from"], "to": r["to"]} for r in opts["replace"]]
    if opts.get("sync_tokens"):
        payload["sync_tokens"] = opts["sync_tokens"]
    return payload


def _client():
    import httpx

    return httpx.Client(base_url=gateway_url(), timeout=TIMEOUT, trust_env=False,
                        headers={"Authorization": f"Bearer {os.environ.get('GATEWAY_ADMIN_TOKEN', '')}"})


def push(link):
    """Register / update one link on the gateway. Records the outcome on the link; never raises."""
    from apps.projects.models import ProjectLink

    if not (link.via_gateway and link.site_name):
        return False
    if not enabled():
        _mark(link, error="The site gateway is not configured on this server.")
        return False
    try:
        with _client() as client:
            r = client.post("/_admin/api/apps", json=app_payload(link))
        if r.status_code != 200:
            raise RuntimeError(f"gateway answered {r.status_code}: {r.text[:200]}")
    except Exception as exc:  # noqa: BLE001 - the gateway being down must not break a save
        logger.warning("gateway push failed for %s: %s", link.site_name, exc)
        _mark(link, error=f"Not published yet (will retry): {exc}"[:300])
        return False
    ProjectLink.objects.filter(pk=link.pk).update(gateway_synced_at=timezone.now(), gateway_error="")
    link.gateway_synced_at, link.gateway_error = timezone.now(), ""
    return True


def remove(site_name):
    """Unregister a site (link deleted, renamed or switched off). Never raises."""
    if not (site_name and enabled()):
        return False
    try:
        with _client() as client:
            r = client.delete(f"/_admin/api/apps/{site_name}")
        return r.status_code in (204, 404)
    except Exception as exc:  # noqa: BLE001
        logger.warning("gateway remove failed for %s: %s", site_name, exc)
        return False


def _mark(link, error):
    from apps.projects.models import ProjectLink

    ProjectLink.objects.filter(pk=link.pk).update(gateway_synced_at=None, gateway_error=error)
    link.gateway_synced_at, link.gateway_error = None, error


def sync_all():
    """Make the gateway match Planwise: push links it lacks or has with different settings, remove
    sites Planwise no longer has. Run by the worker every few minutes. -> (pushed, removed)."""
    from apps.projects.models import ProjectLink

    if not enabled():
        return 0, 0
    try:
        with _client() as client:
            r = client.get("/_admin/api/apps")
        r.raise_for_status()
        current = {a["name"]: a for a in r.json()}
    except Exception as exc:  # noqa: BLE001 - gateway down: try again next time
        logger.info("gateway sync skipped: %s", exc)
        return 0, 0
    wanted = {link.site_name: link for link in ProjectLink.objects.filter(via_gateway=True).exclude(site_name="")}
    pushed = removed = 0
    for name, link in wanted.items():
        have = current.get(name)
        want = app_payload(link)
        if have is None or any(have.get(k) != want.get(k) for k in want if k not in ("mounts", "replace")) \
                or (have.get("mounts") or []) != want["mounts"] \
                or (have.get("replace") or []) != want.get("replace", []) or link.gateway_error:
            pushed += push(link)
    for name in current:
        if name not in wanted and remove(name):
            removed += 1
    return pushed, removed


def site_allowed(user, site_name):
    """May this person open /sites/<site_name>/? Signed in, and able to see the link's project."""
    from apps.projects.models import ProjectLink
    from apps.projects.selectors import visible_projects

    if not (user and user.is_authenticated and SITE_NAME_RE.match(site_name or "")):
        return False
    link = ProjectLink.objects.filter(site_name=site_name, via_gateway=True).only("project_id").first()
    return bool(link and visible_projects(user).filter(pk=link.project_id).exists())


def parse_mounts(text):
    """'/_api = http://172.16.15.169:8039' per line -> [{"path", "target"}]. Raises ValueError."""
    from urllib.parse import urlsplit

    mounts, seen = [], set()
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        path, sep, target = (p.strip() for p in line.partition("="))
        parts = urlsplit(target)
        if not sep or not MOUNT_PATH_RE.match(path) or path in seen or parts.scheme not in ("http", "https") \
                or not parts.netloc:
            raise ValueError(f"“{line}” — write each as /_api = http://host:port")
        seen.add(path)
        mounts.append({"path": path, "target": target.rstrip("/")})
    return mounts


def parse_replacements(text):
    """'old text ==> new text' per line -> [{"from", "to"}]. For addresses an app builds at run time
    (the gateway swaps the exact text in the app's code as it passes through). Raises ValueError."""
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        old, sep, new = line.partition(REPLACE_SEP)
        old, new = old.strip(), new.strip()
        if not sep or not old or len(old) > 500 or len(new) > 500:
            raise ValueError(f"“{line[:60]}” — write each as: old text {REPLACE_SEP} new text")
        out.append({"from": old, "to": new})
    return out
