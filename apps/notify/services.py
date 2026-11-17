"""Create notifications and deliver them over Web Push."""

import json
import logging

from django.utils import timezone

from . import vapid
from .models import Notification, PushSubscription

logger = logging.getLogger(__name__)

# endpoints reply with these when a subscription is dead
GONE = (404, 410)


def notify(user, title, body="", url="", kind="mention", project=None, push=True):
    """Store an in-app notification and (optionally) push it. Returns the Notification."""
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    notification = Notification.objects.create(
        user=user, title=title, body=body, url=url, kind=kind, project=project
    )
    if push:
        sent, error = push_to_user(user, notification)
        if sent or error:
            notification.pushed = bool(sent)
            notification.push_error = (error or "")[:300]
            notification.save(update_fields=["pushed", "push_error"])
    return notification


def notify_many(users, **kwargs):
    seen, out = set(), []
    for user in users:
        if user is None or user.pk in seen:
            continue
        seen.add(user.pk)
        out.append(notify(user, **kwargs))
    return out


def payload_for(notification):
    return {
        "title": notification.title,
        "body": notification.body,
        "url": notification.url or "/notifications/",
        "tag": f"{notification.kind}-{notification.project_id or 0}",
        "kind": notification.kind,
        "id": notification.pk,
    }


def push_to_user(user, notification):
    """-> (delivered_count, error_message)."""
    subscriptions = list(PushSubscription.objects.filter(user=user))
    if not subscriptions:
        return 0, ""
    payload = json.dumps(payload_for(notification))
    delivered, last_error = 0, ""
    for subscription in subscriptions:
        ok, error = send_raw(subscription, payload)
        if ok:
            delivered += 1
        else:
            last_error = error
    return delivered, last_error


def send_raw(subscription, payload: str):
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:  # pragma: no cover - dependency missing
        return False, "pywebpush is not installed"

    private_key = vapid.private_pem_path()
    if not private_key:
        return False, "no VAPID key available"

    try:
        webpush(
            subscription_info=subscription.as_subscription_info(),
            data=payload,
            vapid_private_key=private_key,
            vapid_claims={"sub": f"mailto:{vapid.CLAIM_EMAIL}"},
            timeout=10,
        )
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in GONE:
            subscription.delete()
            return False, f"subscription gone ({status})"
        subscription.failures += 1
        subscription.save(update_fields=["failures"])
        logger.warning("web push failed for %s: %s", subscription.user, exc)
        return False, f"{status or 'error'}: {exc}"[:300]
    except Exception as exc:  # noqa: BLE001 - never let a push break the caller
        logger.warning("web push error for %s: %s", subscription.user, exc)
        return False, str(exc)[:300]

    subscription.last_used_at = timezone.now()
    subscription.failures = 0
    subscription.save(update_fields=["last_used_at", "failures"])
    return True, ""


# --------------------------------------------------------------------------- domain events
def project_audience(project, extra=None):
    """Everyone who should hear about a project: members, owner, plus extras."""
    from apps.accounts.models import User

    ids = set(project.members.values_list("id", flat=True))
    if project.owner_id:
        ids.add(project.owner_id)
    for user in extra or []:
        if user is not None and getattr(user, "pk", None):
            ids.add(user.pk)
    return User.objects.filter(pk__in=ids, is_active=True)


def entry_processed(entry):
    needs_names = entry.status == "awaiting_speakers"
    title = f"{entry.project.key}: recording processed"
    body = (
        f"“{entry.title}” is transcribed. {entry.unnamed_speakers} speaker(s) still need names."
        if needs_names
        else f"“{entry.title}” is transcribed and ready."
    )
    return notify_many(
        project_audience(entry.project, [entry.author]),
        title=title,
        body=body,
        url=f"/entries/{entry.pk}/",
        kind="speakers_needed" if needs_names else "entry_processed",
        project=entry.project,
    )


def entry_failed(entry, reason=""):
    return notify_many(
        project_audience(entry.project, [entry.author]),
        title=f"{entry.project.key}: processing failed",
        body=f"“{entry.title}” could not be processed. {reason}"[:400],
        url=f"/entries/{entry.pk}/",
        kind="entry_failed",
        project=entry.project,
    )


def brd_updated(version):
    project = version.brd.project
    return notify_many(
        project_audience(project, [version.created_by]),
        title=f"{project.key}: BRD updated to v{version.number}",
        body=version.summary[:300],
        url=f"/projects/{project.pk}/brd/?v={version.number}",
        kind="brd_updated",
        project=project,
    )


# --------------------------------------------------------------------------- tickets
def _ticket_watchers(ticket, extra=None, exclude=None):
    """Watchers + reporter + assignee as User rows, minus `exclude`."""
    from apps.accounts.models import User

    ids = set(ticket.watchers.values_list("id", flat=True))
    for uid in (ticket.reporter_id, ticket.assignee_id):
        if uid:
            ids.add(uid)
    for person in extra or []:
        if getattr(person, "pk", None):
            ids.add(person.pk)
    excl = getattr(exclude, "pk", exclude)
    return User.objects.filter(pk__in=ids, is_active=True).exclude(pk=excl)


def ticket_raised(ticket):
    project = ticket.project
    lead = project.team.lead if project.team_id else None
    audience = project_audience(project, [ticket.assignee, project.owner, lead])
    audience = audience.exclude(pk=ticket.reporter_id) if ticket.reporter_id else audience
    return notify_many(
        audience,
        title=f"{ticket.key}: {ticket.get_type_display()} raised",
        body=f"“{ticket.title}” — {ticket.get_priority_display()} priority."[:300],
        url=ticket.get_absolute_url(),
        kind="ticket_new",
        project=project,
    )


def ticket_assigned(ticket, by=None):
    if not ticket.assignee_id or ticket.assignee_id == getattr(by, "pk", None):
        return []
    return notify_many(
        [ticket.assignee],
        title=f"{ticket.key} assigned to you",
        body=f"“{ticket.title}” — {ticket.get_priority_display()} priority.",
        url=ticket.get_absolute_url(),
        kind="ticket_assigned",
        project=ticket.project,
    )


def ticket_status_changed(ticket, by=None, old=""):
    move = f"{old} → {ticket.get_status_display()}" if old else ticket.get_status_display()
    return notify_many(
        _ticket_watchers(ticket, exclude=by),
        title=f"{ticket.key}: {ticket.get_status_display()}",
        body=f"“{ticket.title}” — {move}."[:300],
        url=ticket.get_absolute_url(),
        kind="ticket_status",
        project=ticket.project,
    )


def ticket_commented(ticket, comment):
    return notify_many(
        _ticket_watchers(ticket, exclude=comment.author),
        title=f"{ticket.key}: new comment",
        body=f"{comment.author.display_name}: {comment.body}"[:300],
        url=ticket.get_absolute_url(),
        kind="ticket_comment",
        project=ticket.project,
    )


# --------------------------------------------------------------------------- sprint module
def requirement_assigned(requirement, by=None):
    if not requirement.assignee_id or requirement.assignee_id == getattr(by, "pk", None):
        return []
    return notify_many(
        [requirement.assignee],
        title=f"{requirement.key} assigned to you",
        body=f"“{requirement.title}” — {requirement.get_priority_display()} priority.",
        url=requirement.get_absolute_url(),
        kind="requirement_assigned",
        project=requirement.project,
    )


def test_case_result(test_case, by=None):
    """Tell the requirement's assignee/reporter when a unit test fails or is blocked."""
    from apps.accounts.models import User

    requirement = test_case.requirement
    if requirement is None:
        return []
    ids = {uid for uid in (requirement.assignee_id, requirement.reporter_id) if uid}
    ids.discard(getattr(by, "pk", None))
    audience = User.objects.filter(pk__in=ids, is_active=True)
    return notify_many(
        audience,
        title=f"{test_case.key}: {test_case.get_status_display()}",
        body=f"“{test_case.title}” on {requirement.key} — {test_case.get_status_display()}."[:300],
        url=test_case.get_absolute_url(),
        kind="testcase_result",
        project=test_case.project,
    )


def sprint_plan_ready(run):
    from apps.accounts.models import User

    r = run.result or {}
    ids = {uid for uid in (run.requested_by_id, run.project.owner_id) if uid}
    return notify_many(
        User.objects.filter(pk__in=ids, is_active=True),
        title=f"{run.project.key}: sprint plan generated",
        body=(
            f"{r.get('sprints', 0)} sprint(s), {r.get('requirements', 0)} requirement(s), "
            f"{r.get('test_cases', 0)} test case(s)."
        )[:300],
        url=f"/sprints/?project={run.project_id}",
        kind="sprint_plan",
        project=run.project,
    )


def sprint_plan_failed(run):
    return notify_many(
        [run.requested_by] if run.requested_by_id else [],
        title=f"{run.project.key}: sprint plan generation failed",
        body=(run.error or "See the run page for details.")[:300],
        url=run.get_absolute_url(),
        kind="sprint_plan",
        project=run.project,
    )
