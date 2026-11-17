"""VAPID key handling.

The key pair lives in a file on the `vapid` volume so it survives rebuilds — a new key
pair would silently invalidate every existing browser subscription. Generated on first
use; override with VAPID_PRIVATE_KEY_PATH.
"""

import base64
import json
import os
import threading
from pathlib import Path

KEY_PATH = Path(os.environ.get("VAPID_PRIVATE_KEY_PATH", "/app/secrets/vapid.json"))
CLAIM_EMAIL = os.environ.get("VAPID_CLAIM_EMAIL", "admin@example.com")
_lock = threading.Lock()
_cache = {}


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def generate():
    from cryptography.hazmat.primitives.serialization import (
        Encoding, NoEncryption, PrivateFormat, PublicFormat,
    )
    from py_vapid import Vapid02

    vapid = Vapid02()
    vapid.generate_keys()
    private_pem = vapid.private_key.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    ).decode()
    public_raw = vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return {"private_pem": private_pem, "public_key": _b64(public_raw)}


def keys(create=True):
    """-> {'private_pem': str, 'public_key': str} (public key is base64url, for the browser)."""
    if _cache:
        return _cache
    with _lock:
        if _cache:
            return _cache
        if KEY_PATH.exists():
            _cache.update(json.loads(KEY_PATH.read_text()))
            return _cache
        if not create:
            return {}
        pair = generate()
        try:
            KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
            KEY_PATH.write_text(json.dumps(pair))
            os.chmod(KEY_PATH, 0o600)
        except OSError:
            pass  # read-only mount: keep the pair in memory for this process
        _cache.update(pair)
        return _cache


def public_key():
    return keys().get("public_key", "")


def private_pem():
    return keys().get("private_pem", "")


def private_pem_path():
    """pywebpush wants a PEM *file path* (a PEM string is parsed as base64 DER and fails)."""
    pem = private_pem()
    if not pem:
        return ""
    path = KEY_PATH.with_suffix(".pem")
    try:
        if not path.exists() or path.read_text() != pem:
            path.write_text(pem)
            os.chmod(path, 0o600)
    except OSError:
        import tempfile

        path = Path(tempfile.gettempdir()) / "vapid_private.pem"
        if not path.exists():
            path.write_text(pem)
    return str(path)


def reset_cache():
    _cache.clear()
