"""Automatic Create/Edit/Delete tracking for every model in the project, so a future
feature's models are covered with zero new code. Two things are deliberately skipped:

  * models already hand-instrumented elsewhere with a friendlier action name
    (Login/Logout, Users, Permissions, Projects, VMs) — to avoid logging the same
    action twice
  * a short list of Django internals and pure bookkeeping models that save far more
    often than a person actually "does" something, which would drown real activity
    in noise (see MANUALLY_COVERED / EXCLUDED below)
"""

import re

from django.apps import apps as django_apps
from django.conf import settings
from django.db.models.signals import post_delete, post_save, pre_save

from .context import get_current_user
from .services import log

MANUALLY_COVERED = {
    ("accounts", "user"),
    ("accounts", "rolecapability"),
    ("accounts", "usercapability"),
    ("projects", "project"),
    ("vms", "vm"),
    ("vms", "projectvm"),
}

EXCLUDED = {
    ("audit", "auditlog"),  # would log itself into an infinite loop otherwise
    ("sessions", "session"),  # written on literally every request (incl. the idle-timeout feature)
    ("contenttypes", "contenttype"),
    ("auth", "permission"),
    ("admin", "logentry"),
    ("projects", "activity"),  # the existing project-activity feed — a log logging itself
    ("intake", "transcriptsegment"),  # per-line transcript bookkeeping, not a user action
    ("intake", "mediaasset"),  # audio-pipeline bookkeeping, updated many times per file
    ("notify", "notification"),  # a side-effect of other actions, not an action itself
    ("notify", "pushsubscription"),
    ("rag", "chunk"),  # embedding chunks, created in bulk during reindexing
    ("rag", "chatmessage"),  # conversational content, not a discrete "action"
    ("accounts", "pagevisit"),  # one row per page opened — logging it made every click a fake "Create"
    ("accounts", "userdayactivity"),  # active-time counter, saved on nearly every request
    ("timesheet", "timesheetaudit"),  # the timesheet's own audit trail: logging it here only repeats every change
    ("timesheet", "jobrun"),  # scheduled-job bookkeeping
}

# fields whose values are never written into the log (only that they changed)
_SECRET_FIELD = re.compile(r"pass|secret|token|otp|api[_-]?key|private|credential|totp|ssh|cookie|hash", re.I)
_MAX_VALUE = 60
_MAX_CHANGES = 1000

MODULE_LABELS = {
    "accounts": "org",
    "projects": "projects",
    "tickets": "tickets",
    "sprints": "sprints",
    "intake": "brd",
    "aimodels": "ai",
    "bulk": "bulk_import",
    "rag": "chat",
    "mcp": "mcp",
    "devlog": "devlog",
    "penpot": "design",
    "vms": "vms",
}


def _skip(sender):
    key = (sender._meta.app_label, sender._meta.model_name)
    return key in MANUALLY_COVERED or key in EXCLUDED


def _module_for(sender):
    return MODULE_LABELS.get(sender._meta.app_label, sender._meta.app_label)


def _target(instance):
    """The kind of record first ("Ticket › PW-12 Fix login"), so the activity log can say what was touched."""
    return f"{instance._meta.verbose_name.capitalize()} › {instance}"


def _short(value):
    text = "" if value is None else str(value)
    text = " ".join(text.split())
    return text if len(text) <= _MAX_VALUE else text[: _MAX_VALUE - 1] + "…"


def _value(instance, field):
    """A readable value: the related record's name for a link, otherwise the stored value."""
    raw = field.value_from_object(instance)
    if field.is_relation and raw is not None:
        try:
            return getattr(instance, field.name)
        except Exception:  # noqa: BLE001 - fall back to the id
            return raw
    return raw


def _changes(old, new):
    """"Hours: 4 → 6; Status: draft → submitted" for the fields that differ between the saved row and the new one."""
    parts = []
    for field in new._meta.concrete_fields:
        if field.primary_key or getattr(field, "auto_now", False) or getattr(field, "auto_now_add", False):
            continue
        before, after = field.value_from_object(old), field.value_from_object(new)
        if before == after:
            continue
        label = str(field.verbose_name).capitalize()
        if _SECRET_FIELD.search(field.name):
            parts.append(f"{label}: changed")
        else:
            parts.append(f"{label}: {_short(_value(old, field)) or '—'} → {_short(_value(new, field)) or '—'}")
    return "; ".join(parts)[:_MAX_CHANGES]


def _on_pre_save(sender, instance, raw=False, **kwargs):
    """Remember what an edit changed, for the log row written right after the save. Only for a save made by a
    signed-in person (background jobs are not looked up), and never allowed to break the save itself."""
    instance._audit_changes = ""
    if raw or _skip(sender) or instance.pk is None or get_current_user() is None:
        return
    try:
        old = sender._default_manager.filter(pk=instance.pk).first()
        if old is not None:
            instance._audit_changes = _changes(old, instance)
    except Exception:  # noqa: BLE001
        instance._audit_changes = ""


def _on_save(sender, instance, created, **kwargs):
    if _skip(sender):
        return
    changes = "" if created else getattr(instance, "_audit_changes", "")
    log(get_current_user(), "create" if created else "edit", _module_for(sender), target=_target(instance),
        details=changes)


def _on_delete(sender, instance, **kwargs):
    if _skip(sender):
        return
    log(get_current_user(), "delete", _module_for(sender), target=_target(instance))


def connect():
    if not getattr(settings, "AUDIT_AUTO_TRACK", True):
        return
    for model in django_apps.get_models():
        pre_save.connect(_on_pre_save, sender=model, weak=False)
        post_save.connect(_on_save, sender=model, weak=False)
        post_delete.connect(_on_delete, sender=model, weak=False)
