"""SSH into a VM to pull a project's git repo into its already-created folder — cloning it the
first time, or fetching+merging the latest commits if it's already checked out there. The
folder itself is never created here (see ssh_fs.create_directory) — pull_code() only writes
into a folder that's already there.

Credentials for a private HTTPS repo are passed to git as a one-off `http.extraHeader` on the
specific command that needs them — never embedded in the repo URL itself. Embedding them in the
URL is what git clone/pull/push would otherwise persist verbatim into the VM's .git/config as
`origin`'s stored url, leaving the raw token sitting in plaintext on disk (and liable to leak
into any later error message that quotes `origin` back to the user)."""

import base64
import re
import shlex

import paramiko

CONNECT_TIMEOUT = 10
COMMAND_TIMEOUT = 20
INSTALL_TIMEOUT = 120

_CREDENTIALED_HTTP_URL = re.compile(r"^(https?://)[^/@\s]+@(.+)$")


class VMGitError(Exception):
    """Base for every error this module raises — always a clean, user-facing message."""


class SSHConnectionError(VMGitError):
    pass


class GitInstallError(VMGitError):
    pass


class RepoUnreachableError(VMGitError):
    pass


class BranchNotFoundError(VMGitError):
    pass


class GitCloneError(VMGitError):
    pass


def connect(vm):
    """-> a connected paramiko.SSHClient, or raises SSHConnectionError."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=vm.host,
            username=vm.username or None,
            password=vm.password or None,
            timeout=CONNECT_TIMEOUT,
            banner_timeout=CONNECT_TIMEOUT,
            auth_timeout=CONNECT_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001 - surface a clean message, not a paramiko traceback
        raise SSHConnectionError(f"Could not SSH into {vm.host}: {exc}") from exc
    return client


def _run(client, command, timeout=COMMAND_TIMEOUT):
    """-> (exit_status, stdout, stderr).

    Every command runs with GIT_SSH_COMMAND set so a git-over-SSH remote (git@host:org/repo.git)
    trusts a host it's never seen before instead of hanging on the interactive "are you sure you
    want to continue connecting?" prompt, which has nowhere to go over a scripted exec_command.
    This mirrors connect()'s own AutoAddPolicy() for the VM connection itself — same trust-on-
    first-use tradeoff, just applied to the VM's outbound git/SSH connections instead of ours to
    the VM. It's a no-op for any command that isn't talking to a git-over-SSH remote.
    """
    full_command = f"GIT_SSH_COMMAND='ssh -o StrictHostKeyChecking=accept-new' {command}"
    _, stdout, stderr = client.exec_command(full_command, timeout=timeout)
    exit_status = stdout.channel.recv_exit_status()
    return exit_status, stdout.read().decode(errors="replace"), stderr.read().decode(errors="replace")


def _run_sudo(client, command, password, timeout=COMMAND_TIMEOUT):
    """Runs `command` as root via `sudo -S`, feeding `password` on stdin (empty prompt so
    it never lands in stdout/stderr). -> (exit_status, stdout, stderr)."""
    stdin, stdout, stderr = client.exec_command(f"sudo -S -p '' {command}", timeout=timeout)
    stdin.write(f"{password}\n")
    stdin.flush()
    exit_status = stdout.channel.recv_exit_status()
    return exit_status, stdout.read().decode(errors="replace"), stderr.read().decode(errors="replace")


def ensure_git_installed(client, password=None):
    """Installs git via apt if `git --version` fails. Raises GitInstallError on failure.
    Uses the VM's own login password to authorize the install when one is on file;
    falls back to non-interactive sudo (works only with a NOPASSWD sudoers rule) otherwise."""
    status, _, _ = _run(client, "git --version")
    if status == 0:
        return
    install_cmd = "sh -c 'apt-get update -y && apt-get install -y git'"
    if password:
        status, out, err = _run_sudo(client, install_cmd, password, timeout=INSTALL_TIMEOUT)
    else:
        status, out, err = _run(client, f"sudo -n {install_cmd}", timeout=INSTALL_TIMEOUT)
    if status != 0:
        raise GitInstallError(f"Git is not installed and automatic install failed: {(err or out).strip()[:300]}")


def _strip_credentials(url):
    """-> `url` with any embedded user:token@ removed — safe to show in a message or compare
    against a credential-less URL. A no-op for anything that isn't a credentialed http(s) URL."""
    if not url:
        return url
    match = _CREDENTIALED_HTTP_URL.match(url)
    return f"{match.group(1)}{match.group(2)}" if match else url


def _auth_config_arg(username, token):
    """-> a `-c http.extraHeader=...` argument that authenticates one git command over HTTPS,
    without the credential ever being written into the VM's .git/config. Harmless to include
    for an ssh-form remote — git's http transport is simply never invoked, so the header is
    unused. Still visible for a moment in that command's process list on the VM (same as any
    exec_command argument, e.g. the repo URL itself) — just never persisted to disk."""
    if not token:
        return ""
    basic = base64.b64encode(f"{username or 'x-access-token'}:{token}".encode()).decode()
    return f"-c {shlex.quote('http.extraHeader=Authorization: basic ' + basic)}"


def _git_cmd(auth_arg, *args):
    parts = ["git"]
    if auth_arg:
        parts.append(auth_arg)
    parts.extend(args)
    return " ".join(parts)


def check_repo_reachable(client, repo_url, auth_arg=""):
    """Confirms the repo can be reached WITHOUT cloning it."""
    status, out, err = _run(client, _git_cmd(auth_arg, "ls-remote", shlex.quote(repo_url)))
    if status != 0:
        raise RepoUnreachableError(f"Repository is not reachable from the VM: {(err or out).strip()[:300]}")
    return out


def check_branch_exists(client, repo_url, branch, auth_arg=""):
    status, out, err = _run(
        client, _git_cmd(auth_arg, "ls-remote", "--heads", shlex.quote(repo_url), shlex.quote(branch))
    )
    if status != 0 or not out.strip():
        raise BranchNotFoundError(f"Branch '{branch}' was not found in {repo_url}")


def _repo_state(client, folder_path):
    """-> ('missing' | 'empty' | 'nonempty' | 'git', origin_url_or_None) for `folder_path` on the VM."""
    quoted = shlex.quote(folder_path)
    script = (
        f"sh -c '"
        f"if [ ! -d {quoted} ]; then echo STATUS=missing; "
        f"elif [ -d {quoted}/.git ]; then "
        f"echo STATUS=git; "
        f"git -c safe.directory={quoted} -C {quoted} remote get-url origin 2>/dev/null | sed \"s/^/ORIGIN=/\"; "
        f"elif [ -z \"$(ls -A {quoted} 2>/dev/null)\" ]; then echo STATUS=empty; "
        f"else echo STATUS=nonempty; fi'"
    )
    _, out, _ = _run(client, script)
    info = dict(line.partition("=")[0::2] for line in out.strip().splitlines() if "=" in line)
    return info.get("STATUS", "missing"), info.get("ORIGIN")


def _heal_origin_if_credentialed(client, folder_path, origin, repo_url):
    """If this folder's stored origin still has credentials embedded in it — leftover from
    before this module stopped writing them into the URL — quietly rewrites it back to the
    plain repo_url, so the token stops sitting in the VM's .git/config in plaintext."""
    if origin and origin != _strip_credentials(origin):
        _run(client, f"git -C {shlex.quote(folder_path)} remote set-url origin {shlex.quote(repo_url)}")


def _same_repo(origin, repo_url):
    """Compares two repo URLs loosely: strips any embedded credentials, ignores a trailing
    '.git'/slash, and treats the SSH form (git@host:org/repo) and the HTTPS form
    (https://host/org/repo) as identical — a VM clone typically ends up with an SSH origin
    even when the assignment form has an HTTPS URL."""

    def norm(url):
        url = _strip_credentials((url or "").strip().rstrip("/"))
        if url.lower().endswith(".git"):
            url = url[:-4]
        if "@" in url and ":" in url and "://" not in url:
            _, _, rest = url.partition("@")
            host, _, path = rest.partition(":")
            url = f"{host}/{path}"
        else:
            for prefix in ("https://", "http://", "ssh://git@", "git://"):
                if url.lower().startswith(prefix):
                    url = url[len(prefix):]
                    break
        return url.lower()

    return norm(origin) == norm(repo_url)


def _restore_working_tree(client, folder_path):
    """After a pull, the folder should actually contain what its own HEAD says it does — not
    just have the right refs. If git shows only tracked files missing from disk (no other
    uncommitted changes), restores them from the index/HEAD: this never touches history, it
    just re-materializes files git already considers part of this commit. Raises GitCloneError
    instead of guessing when there's other uncommitted work mixed in, so a genuine work-in-
    progress edit on the VM is never silently discarded."""
    quoted = shlex.quote(folder_path)
    status, out, _ = _run(client, f"git -C {quoted} status --porcelain")
    if status != 0:
        return  # can't tell — leave it alone rather than fail the pull over a diagnostic step
    lines = [line for line in out.splitlines() if line.strip()]
    if not lines:
        return
    if all(len(line) > 1 and line[1] == "D" for line in lines):
        _run(client, f"git -C {quoted} checkout -- .")
        return
    raise GitCloneError(
        f"{folder_path}'s working tree has uncommitted changes beyond just missing files — "
        "resolve them directly on the VM before pulling again."
    )


def _check_no_unexpected_deletions(client, folder_path):
    """Refuses to stage+push when the working tree has tracked files that are locally missing.
    That's exactly the corruption pattern this module has actually hit (a botched manual merge
    silently wiping files while HEAD still lists them) — `git add -A` would otherwise stage
    those deletions right alongside anything legitimate, silently pushing them to the real
    remote. If the deletions are intentional, push that directly on the VM instead."""
    quoted = shlex.quote(folder_path)
    status, out, _ = _run(client, f"git -C {quoted} status --porcelain")
    if status != 0:
        return
    deleted = [line[3:] for line in out.splitlines() if len(line) > 1 and line[1] == "D"]
    if deleted:
        names = ", ".join(deleted[:5]) + (f", and {len(deleted) - 5} more" if len(deleted) > 5 else "")
        raise GitCloneError(
            f"{folder_path} is missing tracked file(s) that git still has committed ({names}) — "
            "pushing now would stage and push their deletion. If that's unintentional, use Pull "
            "code first to restore them; if you really do want them removed, do that push "
            "directly on the VM."
        )


def _sync_status(client, folder_path):
    """After a `git fetch` has set FETCH_HEAD, compares it to local HEAD.
    -> ('even' | 'behind' | 'ahead' | 'diverged', behind_count, ahead_count)."""
    quoted = shlex.quote(folder_path)
    _, behind_out, _ = _run(client, f"git -C {quoted} rev-list --count HEAD..FETCH_HEAD")
    _, ahead_out, _ = _run(client, f"git -C {quoted} rev-list --count FETCH_HEAD..HEAD")
    behind = int(behind_out.strip() or 0)
    ahead = int(ahead_out.strip() or 0)
    if behind and ahead:
        return "diverged", behind, ahead
    if behind:
        return "behind", behind, 0
    if ahead:
        return "ahead", 0, ahead
    return "even", 0, 0


# Paths a first push must never sweep up by accident: dependency folders, env files holding
# secrets, private keys. (.env.example / .sample / .template files are fine.)
_SECRET_PATH_RE = r"(^|/)node_modules/|(^|/)\.env(\.[^/]+)?$|(^|/)id_(rsa|dsa|ecdsa|ed25519)$|\.(pem|key|p12|pfx)$"
_SAFE_SUFFIX_RE = r"\.(example|sample|template|dist)$"
_MAX_FIRST_PUSH_FILE_MB = 50  # GitHub rejects files over 100 MB; stop well before that


def _risky_files_for_first_push(client, folder_path):
    """-> short labels of what `git add -A` would sweep into the very first commit that almost
    certainly shouldn't be pushed (dependency folders, env files, private keys, huge files),
    honouring any .gitignore. Run after `git init`, before anything is staged."""
    quoted = shlex.quote(folder_path)
    listing = f"git -C {quoted} ls-files -z --cached --others --exclude-standard"
    secret_cmd = (
        f"{listing} | tr '\\0' '\\n' | grep -E -i {shlex.quote(_SECRET_PATH_RE)} "
        f"| grep -v -E -i {shlex.quote(_SAFE_SUFFIX_RE)} | head -n 300"
    )
    _, out, _ = _run(client, f"sh -c {shlex.quote(secret_cmd)}")
    labels = []
    for path in out.splitlines():
        path = path.strip()
        if not path:
            continue
        marker = "node_modules/"
        label = path[: path.index(marker) + len(marker)] if marker in path else path
        if label not in labels:
            labels.append(label)
    limit = _MAX_FIRST_PUSH_FILE_MB * 1024 * 1024
    big_cmd = (
        f"cd {quoted} && {listing} | xargs -0 -r stat -c '%s %n' 2>/dev/null "
        f"| awk '$1 > {limit}' | head -n 5"
    )
    _, big, _ = _run(client, f"sh -c {shlex.quote(big_cmd)}")
    for line in big.splitlines():
        size, _, name = line.strip().partition(" ")
        if name and size.isdigit():
            label = f"{name} ({int(size) // (1024 * 1024)} MB)"
            if label not in labels:
                labels.append(label)
    # a sub-folder that is itself a git repo would be added as a bare pointer, not its code
    nested_cmd = (
        f"find {quoted} -mindepth 2 -maxdepth 4 -name .git -not -path '*/node_modules/*' 2>/dev/null | head -n 20"
    )
    _, nested, _ = _run(client, f"sh -c {shlex.quote(nested_cmd)}")
    for git_dir in nested.splitlines():
        sub = git_dir.strip()[: -len("/.git")]
        rel = sub[len(folder_path.rstrip("/")) + 1:] if sub.startswith(folder_path.rstrip("/") + "/") else sub
        if not rel:
            continue
        ignored, _, _ = _run(client, f"git -C {quoted} check-ignore -q -- {shlex.quote(rel + '/')}")
        if ignored != 0:  # not covered by a .gitignore, so git add would take it as an embedded repo
            labels.append(f"{rel}/ (its own git repo - only a pointer would be pushed, not its code)")
    return labels


def _first_push_refusal(folder_path, risky):
    shown = ", ".join(risky[:8]) + (f", and {len(risky) - 8} more" if len(risky) > 8 else "")
    return (
        f"Nothing was pushed or committed. The first commit from {folder_path} would include things that "
        f"shouldn't go into git: {shown}. Add a .gitignore in that folder that excludes them (for example "
        "node_modules/, .env, *.pem, *.key); for a folder that is its own git repo, either ignore it or "
        "remove its .git folder to include its code. Then push again."
    )


def _discard_new_checkout(client, folder_path):
    """Undoes a `git init` done a moment ago by _init_checkout_for_first_push, leaving the folder
    exactly as it was found. Only ever called on a .git that this same push created."""
    _run(client, f"rm -rf {shlex.quote(folder_path)}/.git")


def _init_checkout_for_first_push(client, repo_url, branch, folder_path, auth_arg):
    """The folder already holds code but isn't a git checkout, and the remote has no such branch
    yet (an empty repo): there is nothing to pull, so turns the folder into a checkout of
    `repo_url` in place — `git init`, branch name, origin — so the normal commit+push below can
    create the branch. Refuses (leaving the folder untouched) if the remote already has the
    branch, or if the first commit would sweep in secrets / dependency folders / huge files."""
    check_repo_reachable(client, repo_url, auth_arg)
    _, ls_out, _ = _run(client, _git_cmd(auth_arg, "ls-remote", "--heads", shlex.quote(repo_url), shlex.quote(branch)))
    if ls_out.strip():
        raise GitCloneError(
            f"{folder_path} has files in it but isn't a checkout of this repo, and {repo_url} already has a "
            f"'{branch}' branch — Planwise won't guess how to merge the two. Move this code aside and pull "
            "into an empty folder, or connect the folder to the repo directly on the VM."
        )
    quoted = shlex.quote(folder_path)
    status, out, err = _run(client, f"git init {quoted}")
    if status != 0:
        raise GitCloneError(f"Could not set up git in {folder_path}: {(err or out).strip()[:300]}")
    # `symbolic-ref` rather than `git init -b`, which needs git 2.28+
    commands = (
        f"git -C {quoted} symbolic-ref HEAD refs/heads/{shlex.quote(branch)}",
        f"git -C {quoted} remote add origin {shlex.quote(_strip_credentials(repo_url))}",
    )
    for command in commands:
        status, out, err = _run(client, command)
        if status != 0:
            _discard_new_checkout(client, folder_path)
            raise GitCloneError(f"Could not set up git in {folder_path}: {(err or out).strip()[:300]}")
    risky = _risky_files_for_first_push(client, folder_path)
    if risky:
        _discard_new_checkout(client, folder_path)
        raise GitCloneError(_first_push_refusal(folder_path, risky))


def push_code(vm, repo_url, branch, folder_path, commit_message, username=None, token=None):
    """Stages everything currently sitting in `folder_path` on the VM and commits it (skipped
    if nothing's actually changed), then pushes to `repo_url`@`branch` — but first checks that
    branch's commit history against the VM's: if the VM is purely behind, it fast-forwards from
    the remote before pushing; if the two have genuinely diverged (commits on both sides that
    the other doesn't have), it refuses to guess how to merge them and raises instead. Never
    clones — `folder_path` must already be a checkout of that exact repo (i.e. pulled once) —
    except for the first push of an existing code folder into an EMPTY remote (no such branch
    yet): there is nothing to pull, so the folder is turned into a checkout in place first.
    -> True when that first-push setup happened, else False."""
    if not repo_url:
        raise VMGitError("No git repo URL is set for this VM assignment yet.")
    if not folder_path:
        raise VMGitError("No folder path is set for this VM assignment yet.")
    branch = branch or "main"
    auth_arg = _auth_config_arg(username, token)
    quoted_folder = shlex.quote(folder_path)
    client = connect(vm)
    try:
        ensure_git_installed(client, vm.password)
        state, origin = _repo_state(client, folder_path)
        if state == "missing":
            raise VMGitError(f"{folder_path} doesn't exist on {vm.host} yet — create it in the VM section first.")
        initialised = False
        if state == "nonempty":
            _init_checkout_for_first_push(client, repo_url, branch, folder_path, auth_arg)
            initialised = True
        elif state != "git":
            raise GitCloneError(f"{folder_path} isn't a git checkout yet — pull the repo before pushing to it.")
        else:
            _heal_origin_if_credentialed(client, folder_path, origin, repo_url)
            if not _same_repo(origin, repo_url):
                raise GitCloneError(
                    f"{folder_path} holds a checkout of a different repo (origin: {_strip_credentials(origin) or 'unknown'})."
                )
            _check_no_unexpected_deletions(client, folder_path)
            if _run(client, f"git -C {quoted_folder} rev-parse --verify -q HEAD")[0] != 0:
                # `git init` was done by hand and nothing is committed yet: this push makes the
                # first commit, so give it the same secrets / build-output / embedded-repo check
                risky = _risky_files_for_first_push(client, folder_path)
                if risky:
                    raise GitCloneError(_first_push_refusal(folder_path, risky))

        _run(client, f"git -C {quoted_folder} add -A")
        status, _, _ = _run(client, f"git -C {quoted_folder} diff --cached --quiet")
        if initialised and status == 0:  # everything in the folder is ignored — nothing to make a first commit of
            _discard_new_checkout(client, folder_path)
            raise GitCloneError(f"Nothing to push: every file in {folder_path} is excluded by a .gitignore.")
        if status != 0:  # diff --quiet exits 1 when there's something staged to commit
            message = (commit_message or "").strip() or "Update via Planwise"
            # -c user.name/user.email scoped to just this command, in case the VM has no git
            # identity configured yet — commit would otherwise fail with "Please tell me who you are"
            commit_cmd = (
                f"git -C {quoted_folder} -c user.name=Planwise -c user.email=planwise@noreply.local "
                f"commit -m {shlex.quote(message)}"
            )
            status, out, err = _run(client, commit_cmd)
            if status != 0:
                raise GitCloneError(f"Commit failed: {(err or out).strip()[:300]}")

        # does this branch exist on the remote yet? if not, there's nothing to reconcile —
        # this push will be the one that creates it.
        _, ls_out, _ = _run(client, _git_cmd(auth_arg, "ls-remote", "--heads", shlex.quote(repo_url), shlex.quote(branch)))
        if ls_out.strip():
            fetch_cmd = _git_cmd(auth_arg, "-C", quoted_folder, "fetch", shlex.quote(repo_url), shlex.quote(branch))
            status, out, err = _run(client, fetch_cmd, timeout=INSTALL_TIMEOUT)
            if status != 0:
                raise GitCloneError(
                    f"Could not fetch {branch} to compare commit history before pushing: {(err or out).strip()[:300]}"
                )
            sync, behind, ahead = _sync_status(client, folder_path)
            if sync == "behind":
                status, out, err = _run(client, f"git -C {quoted_folder} merge --ff-only FETCH_HEAD")
                if status != 0:
                    raise GitCloneError(
                        f"Could not fast-forward to the remote's latest commits before pushing: {(err or out).strip()[:300]}"
                    )
            elif sync == "diverged":
                raise GitCloneError(
                    f"{folder_path}'s branch and {repo_url}@{branch} have diverged — {ahead} commit(s) only on "
                    f"the VM and {behind} only on the remote. Pull first (or resolve it directly on the VM) "
                    "before pushing."
                )

        push_cmd = _git_cmd(
            auth_arg, "-C", quoted_folder, "push", shlex.quote(repo_url), f"HEAD:{shlex.quote(branch)}"
        )
        status, out, err = _run(client, push_cmd, timeout=INSTALL_TIMEOUT)
        if status != 0:
            raise GitCloneError(f"Push failed: {(err or out).strip()[:300]}")
        return initialised
    finally:
        client.close()


def pull_code(vm, repo_url, branch, folder_path, username=None, token=None):
    """Clones `repo_url`@`branch` into `folder_path` if it's empty, or fetches+merges the latest
    commits if it's already a checkout of that same repo. `folder_path` must already exist
    (created via the VM section's directory step) — this never creates it. Raises a VMGitError
    subclass — GitCloneError specifically — if the folder holds unrelated content, or already
    holds a checkout of a *different* repo."""
    if not repo_url:
        raise VMGitError("No git repo URL is set for this VM assignment yet.")
    if not folder_path:
        raise VMGitError("No folder path is set for this VM assignment yet.")
    branch = branch or "main"
    auth_arg = _auth_config_arg(username, token)
    client = connect(vm)
    try:
        ensure_git_installed(client, vm.password)
        status, _, _ = _run(client, f"sh -c '[ -d {shlex.quote(folder_path)} ]'")
        if status != 0:
            raise VMGitError(f"{folder_path} doesn't exist on {vm.host} yet — create it in the VM section first.")
        check_repo_reachable(client, repo_url, auth_arg)
        check_branch_exists(client, repo_url, branch, auth_arg)
        state, origin = _repo_state(client, folder_path)
        if state == "git":
            _heal_origin_if_credentialed(client, folder_path, origin, repo_url)
            if not _same_repo(origin, repo_url):
                raise GitCloneError(
                    f"{folder_path} already holds a checkout of a different repo "
                    f"(origin: {_strip_credentials(origin) or 'unknown'})."
                )
            quoted_folder = shlex.quote(folder_path)
            fetch_cmd = _git_cmd(auth_arg, "-C", quoted_folder, "fetch", shlex.quote(repo_url), shlex.quote(branch))
            status, out, err = _run(client, fetch_cmd, timeout=INSTALL_TIMEOUT)
            if status != 0:
                raise GitCloneError(f"Pull failed: {(err or out).strip()[:300]}")
            sync, behind, ahead = _sync_status(client, folder_path)
            if sync == "behind":
                status, out, err = _run(client, f"git -C {quoted_folder} merge --ff-only FETCH_HEAD")
                if status != 0:
                    raise GitCloneError(f"Pull failed: {(err or out).strip()[:300]}")
            elif sync == "diverged":
                # a plain `git pull` would hit git's own "divergent branches, specify how to
                # reconcile" hard stop here — surface it as our own clear error instead of
                # guessing whether to merge or rebase (that choice belongs to whoever wrote
                # the commits, not to an automated pull).
                raise GitCloneError(
                    f"{folder_path}'s branch and {repo_url}@{branch} have diverged — {ahead} commit(s) only on "
                    f"the VM and {behind} only on the remote. Resolve it directly on the VM (or push the VM's "
                    "commits first) before pulling."
                )
            # sync == "even" or "ahead": nothing new to fetch — but the working tree itself
            # might still be desynced from HEAD (e.g. files removed outside of git), so this
            # isn't skipped even when there was nothing to merge.
            _restore_working_tree(client, folder_path)
        elif state == "nonempty":
            raise GitCloneError(f"{folder_path} already has files in it that aren't a checkout of this repo.")
        else:  # empty
            clone_cmd = _git_cmd(
                auth_arg, "clone", "--branch", shlex.quote(branch), shlex.quote(repo_url), shlex.quote(folder_path)
            )
            status, out, err = _run(client, clone_cmd, timeout=INSTALL_TIMEOUT)
            if status != 0:
                raise GitCloneError(f"Clone failed: {(err or out).strip()[:300]}")
    finally:
        client.close()
