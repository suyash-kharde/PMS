"""Read-only GitHub lookups for display only: recent commits + latest release tag.
No SSH, no clone — just the public GitHub REST API, cached briefly to avoid hammering it."""

import re

import httpx
from django.core.cache import cache
from django.utils.dateparse import parse_datetime

CACHE_TTL = 300
API_TIMEOUT = 5
_GITHUB_RE = re.compile(r"github\.com[:/]+([^/]+)/([^/.]+?)(?:\.git)?/?$")


def parse_repo(url):
    """-> (owner, repo) for a github.com URL, or None if it isn't one."""
    if not url:
        return None
    match = _GITHUB_RE.search(url.strip())
    if not match:
        return None
    return match.group(1), match.group(2)


def _get(path):
    cache_key = f"gh:{path}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        resp = httpx.get(
            f"https://api.github.com{path}", timeout=API_TIMEOUT, headers={"Accept": "application/vnd.github+json"}
        )
        data = resp.json() if resp.status_code == 200 else None
    except httpx.HTTPError:
        data = None
    cache.set(cache_key, data, CACHE_TTL)
    return data


def recent_commits(repo_url, branch, limit=3):
    """-> list of {message, author, date}, or [] if unavailable / not a GitHub URL."""
    parsed = parse_repo(repo_url)
    if not parsed:
        return []
    owner, repo = parsed
    data = _get(f"/repos/{owner}/{repo}/commits?sha={branch or 'main'}&per_page={limit}")
    if not isinstance(data, list):
        return []
    out = []
    for item in data[:limit]:
        commit = item.get("commit", {})
        message = (commit.get("message") or "").splitlines()[0][:120]
        author = commit.get("author", {}).get("name") or (item.get("author") or {}).get("login") or "unknown"
        date = parse_datetime(commit.get("author", {}).get("date", "") or "")
        out.append({"message": message, "author": author, "date": date})
    return out


def latest_version(repo_url):
    """-> the latest published release tag, falling back to the newest tag; None if neither exists."""
    parsed = parse_repo(repo_url)
    if not parsed:
        return None
    owner, repo = parsed
    release = _get(f"/repos/{owner}/{repo}/releases/latest")
    if isinstance(release, dict) and release.get("tag_name"):
        return release["tag_name"]
    tags = _get(f"/repos/{owner}/{repo}/tags?per_page=1")
    if isinstance(tags, list) and tags:
        return tags[0].get("name")
    return None
