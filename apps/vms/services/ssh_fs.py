"""Directory operations on a VM over SSH: creating the folder a project gets assigned to,
listing it read-only afterwards, and uploading individual files/folders straight into it (or
any subfolder of it) — a plain drag-and-drop-style drop, not tied to git at all. Reuses the
same SSH connection plumbing as ssh_git.py."""

import posixpath
import shlex

from .ssh_git import SSHConnectionError, _run, connect  # noqa: F401 (SSHConnectionError re-exported for callers)


class DirectoryListError(Exception):
    """Raised with a clean, user-facing message — bad path, VM unreachable, etc."""


class DirectoryExistsError(Exception):
    """Raised when the requested folder path is already there on the VM."""


class UploadError(Exception):
    """Raised with a clean, user-facing message for anything that goes wrong uploading files."""


def create_directory(vm, path):
    """Creates `path` on `vm`. Raises DirectoryExistsError if anything is already at that path
    (file or directory), or DirectoryListError/SSHConnectionError on any other failure."""
    if not path:
        raise DirectoryListError("No folder path given.")
    client = connect(vm)
    try:
        quoted = shlex.quote(path)
        _, out, _ = _run(client, f"sh -c '[ -e {quoted} ] && echo EXISTS || echo OK'")
        if "EXISTS" in out:
            raise DirectoryExistsError(f"{path} already exists on {vm.host}.")
        status, out, err = _run(client, f"mkdir -p {quoted}")
        if status != 0:
            raise DirectoryListError(f"Could not create {path}: {(err or out).strip()[:200]}")
    finally:
        client.close()


def list_directory(vm, path):
    """-> raw `ls -la` output for `path` on `vm`. Raises SSHConnectionError or DirectoryListError."""
    if not path:
        raise DirectoryListError("No folder path is set for this project on this VM yet.")
    client = connect(vm)
    try:
        status, out, err = _run(client, f"ls -la {shlex.quote(path)}")
        if status != 0:
            raise DirectoryListError((err or out).strip()[:500] or f"Could not list {path}")
        return out
    finally:
        client.close()


def _safe_relative_path(raw):
    """Normalizes a user- or browser-supplied relative path and rejects anything that would
    escape the target directory — a leading slash, '..' segments, a bare '.', or backslashes
    from a Windows-style path. -> the cleaned POSIX-style relative path. Raises UploadError."""
    if not raw or not raw.strip():
        raise UploadError("A file was uploaded with no name.")
    cleaned = raw.replace("\\", "/").strip("/")
    parts = [p for p in cleaned.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise UploadError(f"Refusing to upload to an unsafe path: {raw!r}")
    return "/".join(parts)


def upload_files(vm, folder_path, dest_subfolder, files):
    """Uploads `files` — an iterable of (relative_path, local_temp_path) pairs — into
    `folder_path`/`dest_subfolder` on the VM (subfolder may be blank for the project's own
    folder root), creating whatever subdirectories each file's relative path needs. Overwrites
    a file already there by the same name — this is a plain drop-files-in operation, not a
    reconciliation, so unlike Pull/Push it never refuses on existing content. Every path
    involved (the subfolder and each file's own relative path) is sanitized to stay inside the
    project's folder. Raises UploadError/DirectoryListError on failure."""
    if not folder_path:
        raise DirectoryListError("No folder path is set for this project on this VM yet.")
    files = list(files)
    if not files:
        raise UploadError("Choose at least one file or folder to upload.")

    safe_dest = _safe_relative_path(dest_subfolder) if dest_subfolder else ""
    base = posixpath.join(folder_path.rstrip("/"), safe_dest) if safe_dest else folder_path.rstrip("/")

    client = connect(vm)
    try:
        status, _, _ = _run(client, f"sh -c '[ -d {shlex.quote(folder_path)} ]'")
        if status != 0:
            raise DirectoryListError(f"{folder_path} doesn't exist on {vm.host} yet — create it in the VM section first.")

        status, out, err = _run(client, f"mkdir -p {shlex.quote(base)}")
        if status != 0:
            raise UploadError(f"Could not create {base}: {(err or out).strip()[:200]}")

        sftp = client.open_sftp()
        try:
            made_dirs = {base}
            for relative_path, local_path in files:
                safe_rel = _safe_relative_path(relative_path)
                remote_path = posixpath.join(base, safe_rel)
                remote_dir = posixpath.dirname(remote_path)
                if remote_dir not in made_dirs:
                    status, out, err = _run(client, f"mkdir -p {shlex.quote(remote_dir)}")
                    if status != 0:
                        raise UploadError(f"Could not create {remote_dir}: {(err or out).strip()[:200]}")
                    made_dirs.add(remote_dir)
                try:
                    sftp.put(local_path, remote_path)
                except Exception as exc:  # noqa: BLE001 - surface a clean message, not a paramiko traceback
                    raise UploadError(f"Could not upload {safe_rel} to {vm.host}: {exc}") from exc
        finally:
            sftp.close()
    finally:
        client.close()
