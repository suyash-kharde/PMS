"""Symmetric encryption for secrets we store at rest, keyed off Django's own SECRET_KEY so
there's no separate key to manage or rotate out-of-band. Used for ProjectVM.git_token only —
VM.password and ProjectVM.jenkins_api_token predate this and are out of scope here."""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet():
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())
    return Fernet(key)


def encrypt(raw):
    if not raw:
        return ""
    return _fernet().encrypt(raw.encode()).decode()


def decrypt(value):
    """-> the decrypted secret, or "" if value is empty or wasn't encrypted with the current key."""
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        return ""
