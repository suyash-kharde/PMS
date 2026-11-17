"""Calls back into Jenkins to unblock a paused `input()` step, or to abort a build outright.
Both are plain Jenkins REST endpoints, authenticated with the project's stored username + API
token. Never called for a ProjectVM that has no Jenkins URL configured."""

import httpx

TIMEOUT = 15


class JenkinsCallError(Exception):
    """Raised with a clean, user-facing message — Jenkins unreachable, wrong credentials, etc."""


def _auth(project_vm):
    return (project_vm.jenkins_user, project_vm.jenkins_api_token)


def _find_pending_input(project_vm, build_url, input_id):
    """Jenkins doesn't necessarily preserve the exact case of the id given in the Jenkinsfile's
    `input()` step (seen live: 'prod-approval' came back as 'Prod-approval' in Jenkins' own
    pendingInputActions) — so rather than guess a URL from the id we were told about, ask Jenkins
    which input is actually still pending on this build and match case-insensitively."""
    url = f"{build_url.rstrip('/')}/wfapi/pendingInputActions"
    resp = httpx.get(url, auth=_auth(project_vm), timeout=TIMEOUT)
    resp.raise_for_status()
    for pending in resp.json():
        if pending.get("id", "").lower() == input_id.lower():
            return pending
    return None


def proceed_input(project_vm, build_url, input_id):
    """Submits a paused `input()` step so the Jenkins build continues."""
    if not (project_vm.jenkins_url and build_url and input_id):
        return
    try:
        pending = _find_pending_input(project_vm, build_url, input_id)
        if not pending:
            raise JenkinsCallError(
                "No matching pending approval found on Jenkins — it may have already been "
                "resumed or aborted directly there."
            )
        url = f"{build_url.rstrip('/')}/input/{pending['id']}/proceed"
        # Jenkins' classic input-proceed endpoint 400s ("Nothing is submitted") without a
        # (possibly empty) Stapler-style form body, even for an input with no parameters.
        resp = httpx.post(url, auth=_auth(project_vm), data={"json": "{}"}, timeout=TIMEOUT)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise JenkinsCallError(f"Could not resume the Jenkins build: {exc}") from exc


def abort_build(project_vm, build_url, input_id=None):
    """Aborts the paused input step specifically when its id is known (so the Jenkinsfile's
    `aborted` post-block ties back to this rejection); falls back to stopping the whole build
    if that input can no longer be found."""
    if not (project_vm.jenkins_url and build_url):
        return
    try:
        pending = _find_pending_input(project_vm, build_url, input_id) if input_id else None
        if pending:
            url = f"{build_url.rstrip('/')}/input/{pending['id']}/abort"
        else:
            url = f"{build_url.rstrip('/')}/stop"
        resp = httpx.post(url, auth=_auth(project_vm), timeout=TIMEOUT)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise JenkinsCallError(f"Could not stop the Jenkins build: {exc}") from exc
