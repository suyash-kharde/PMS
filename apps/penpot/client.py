"""A thin client for the Penpot RPC API (`/api/rpc/command/<name>`).

Everything speaks JSON (`Accept: application/json`) with camelCase keys — the
Penpot default is a Clojure `transit+json` encoding we explicitly opt out of.

Authentication is by **session cookie**: `login-with-password` sets an
`auth-token` cookie that every later call reuses. We deliberately do *not* rely on
`Authorization: Token` header auth — on a Penpot deployment behind a reverse proxy
that strips `Authorization` (as this one does) header auth silently fails, whereas
the cookie always survives. An access token is still minted during provisioning
and handed to the team, for tooling that talks to Penpot directly.
"""

from __future__ import annotations

import httpx


class PenpotError(RuntimeError):
    """A Penpot RPC call returned an error (non-2xx or an error body)."""

    def __init__(self, command, status, detail):
        self.command = command
        self.status = status
        self.detail = detail
        super().__init__(f"{command} → HTTP {status}: {detail}")


class PenpotAuthError(PenpotError):
    """The session is missing or expired — the caller should re-login and retry."""


_JSON_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}


class PenpotClient:
    def __init__(self, base_url: str, timeout: float = 30.0, verify: bool = True):
        self.base_url = (base_url or "").rstrip("/")
        self._timeout = timeout
        self._verify = verify
        self._cookies: dict[str, str] = {}

    # ------------------------------------------------------------------ transport
    def _rpc(self, command: str, params: dict | None = None, *, auth: bool = True) -> dict | list | None:
        url = f"{self.base_url}/api/rpc/command/{command}"
        try:
            resp = httpx.post(
                url,
                json=params or {},
                headers=_JSON_HEADERS,
                cookies=self._cookies if auth else None,
                timeout=self._timeout,
                verify=self._verify,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:  # network / DNS / TLS
            raise PenpotError(command, 0, str(exc)) from exc

        # capture any refreshed session cookie
        if "auth-token" in resp.cookies:
            self._cookies["auth-token"] = resp.cookies["auth-token"]

        if resp.status_code == 204 or not resp.content:
            return None

        try:
            body = resp.json()
        except ValueError:
            body = {"raw": resp.text[:500]}

        if resp.status_code >= 400:
            detail = _error_detail(body)
            code = body.get("code") if isinstance(body, dict) else None
            # Penpot reports bad login as HTTP 400 {"type":"validation","code":"wrong-credentials"},
            # not a 401 — still an auth failure, so callers can catch it as one.
            if (
                resp.status_code in (401, 403)
                or (isinstance(body, dict) and body.get("type") == "authentication")
                or code in ("wrong-credentials", "authentication-required")
            ):
                raise PenpotAuthError(command, resp.status_code, detail)
            raise PenpotError(command, resp.status_code, detail)
        return body

    # ------------------------------------------------------------------ auth
    @property
    def authenticated(self) -> bool:
        return "auth-token" in self._cookies

    def set_session(self, auth_token: str):
        if auth_token:
            self._cookies["auth-token"] = auth_token

    @property
    def auth_token(self) -> str:
        return self._cookies.get("auth-token", "")

    def login(self, email: str, password: str) -> dict:
        profile = self._rpc("login-with-password", {"email": email, "password": password}, auth=False)
        if not self.authenticated:
            raise PenpotAuthError("login-with-password", 200, "no auth-token cookie in response")
        return profile or {}

    def logout(self):
        try:
            self._rpc("logout", {})
        except PenpotError:
            pass
        self._cookies.clear()

    # ------------------------------------------------------------------ registration
    def prepare_register(self, fullname: str, email: str, password: str) -> str:
        out = self._rpc(
            "prepare-register-profile",
            {"fullname": fullname, "email": email, "password": password},
            auth=False,
        )
        token = (out or {}).get("token")
        if not token:
            raise PenpotError("prepare-register-profile", 200, f"no token in response: {out}")
        return token

    def register(self, register_token: str, fullname: str = "") -> dict:
        params = {"token": register_token}
        if fullname:
            params["fullname"] = fullname
        profile = self._rpc("register-profile", params, auth=False)
        # register-profile logs the new profile in and sets the auth-token cookie
        return profile or {}

    # ------------------------------------------------------------------ profile
    def get_profile(self) -> dict:
        return self._rpc("get-profile", {}) or {}

    def update_profile(self, **fields) -> dict:
        return self._rpc("update-profile", fields) or {}

    def delete_profile(self) -> None:
        self._rpc("delete-profile", {})

    # ------------------------------------------------------------------ access tokens
    def create_access_token(self, name: str) -> dict:
        return self._rpc("create-access-token", {"name": name[:250]}) or {}

    def delete_access_token(self, token_id: str) -> None:
        self._rpc("delete-access-token", {"id": token_id})

    # ------------------------------------------------------------------ teams
    def get_teams(self) -> list:
        return self._rpc("get-teams", {}) or []

    def create_team(self, name: str) -> dict:
        return self._rpc("create-team", {"name": name[:250]}) or {}

    def update_team(self, team_id: str, name: str) -> dict:
        return self._rpc("update-team", {"id": team_id, "name": name[:250]}) or {}

    def delete_team(self, team_id: str) -> None:
        self._rpc("delete-team", {"id": team_id})

    def create_team_invitations(self, team_id: str, emails: list[str], role: str = "editor") -> dict:
        return self._rpc(
            "create-team-invitations",
            {"teamId": team_id, "emails": list(emails), "role": role},
        ) or {}

    def delete_team_invitation(self, team_id: str, email: str) -> None:
        self._rpc("delete-team-invitation", {"teamId": team_id, "email": email})

    # ------------------------------------------------------------------ projects & files
    def create_project(self, team_id: str, name: str) -> dict:
        return self._rpc("create-project", {"teamId": team_id, "name": name[:250]}) or {}

    def rename_project(self, project_id: str, name: str) -> dict:
        return self._rpc("rename-project", {"id": project_id, "name": name[:250]}) or {}

    def delete_project(self, project_id: str) -> None:
        self._rpc("delete-project", {"id": project_id})

    def get_projects(self, team_id: str) -> list:
        return self._rpc("get-projects", {"teamId": team_id}) or []

    def create_file(self, project_id: str, name: str) -> dict:
        return self._rpc("create-file", {"projectId": project_id, "name": name[:250]}) or {}

    def get_project_files(self, project_id: str) -> list:
        return self._rpc("get-project-files", {"projectId": project_id}) or []

    def get_file(self, file_id: str) -> dict:
        return self._rpc("get-file", {"id": file_id}) or {}

    # ------------------------------------------------------------------ share links
    def create_share_link(self, file_id: str, pages: list[str], who: str = "all") -> dict:
        return self._rpc(
            "create-share-link",
            {"fileId": file_id, "pages": list(pages), "whoComment": who, "whoInspect": who},
        ) or {}

    def delete_share_link(self, share_id: str) -> None:
        self._rpc("delete-share-link", {"id": share_id})


def _error_detail(body) -> str:
    if isinstance(body, dict):
        for key in ("hint", "explain", "code", "message", "error"):
            if body.get(key):
                return str(body[key])
        return str(body)[:400]
    return str(body)[:400]
