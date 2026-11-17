"""The one entry point every feature calls to record an audited action.

Best-effort by design: a logging failure must never break the real action it's
recording, so every write is swallowed and reported to the app log instead."""

import logging

from .context import note_row
from .models import AuditLog

logger = logging.getLogger(__name__)


def log(actor, action, module, target=None, status="success", details=""):
    try:
        AuditLog.objects.create(
            actor=actor if getattr(actor, "is_authenticated", False) else None,
            action=action,
            module=module,
            object_repr=str(target)[:255] if target is not None else "",
            status=status,
            details=details[:4000],
        )
        note_row()
    except Exception:  # noqa: BLE001 - never let audit logging break the caller
        logger.exception("audit log write failed: action=%s module=%s", action, module)
