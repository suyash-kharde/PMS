import json
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from apps.accounts.permissions import has_cap, require
from apps.notify import services as notify
from apps.projects.models import Activity, Task
from apps.projects.selectors import visible_projects

from .forms import TicketCommentForm, TicketForm, assignable_users
from .models import CLOSED_STATUSES, TICKET_STATUS, TICKET_TYPES, Ticket, TicketAttachment, TicketEvent
from .selectors import visible_tickets

STATUS_LABELS = dict(TICKET_STATUS)
TYPE_LABELS = dict(TICKET_TYPES)


def _get_ticket(user, pk):
    ticket = visible_tickets(user).filter(pk=pk).first()
    if not ticket:
        raise Http404("Ticket not found")
    return ticket


def _can_triage(user, ticket):
    return has_cap(user, "ticket.triage", ticket)


def _can_edit(user, ticket):
    return _can_triage(user, ticket) or has_cap(user, "ticket.edit", ticket) or ticket.reporter_id == user.pk


ATTACH_MAX_BYTES = 10 * 1024 * 1024  # 10 MB per file
ATTACH_MAX_FILES = 10

def _save_attachments(request, ticket, field="attachments"):
    """Store any uploaded files against `ticket`. Returns (saved, skipped)."""
    files = request.FILES.getlist(field)
    saved, skipped = [], []
    for upload in files[:ATTACH_MAX_FILES]:
        if upload.size > ATTACH_MAX_BYTES:  # any file type is accepted (.penpot, .har, .sql, …); only size is limited
            skipped.append(upload.name)
            continue
        attachment = TicketAttachment.objects.create(
            ticket=ticket, file=upload, original_name=upload.name[:255], uploaded_by=request.user
        )
        saved.append(attachment)
    if saved:
        TicketEvent.log(
            ticket, request.user,
            "attached " + ", ".join(a.original_name for a in saved),
        )
    if skipped:
        messages.warning(
            request,
            f"Skipped {len(skipped)} file(s) — larger than 10 MB: {', '.join(skipped)}",
        )
    return saved, skipped


# --------------------------------------------------------------------------- list / board
def _safe_date(value):
    """A YYYY-MM-DD date, or None for blank / junk. parse_date() itself raises for well-formed but impossible dates."""
    try:
        return parse_date(value)
    except ValueError:
        return None


def _ticket_kpis(user):
    """The cards at the top of the tickets page: always every ticket this person may see, never the filters.
    Raised / resolved today count today only; the rest count all open tickets, however old."""
    from django.db.models import Count

    today = timezone.localdate()
    base = visible_tickets(user)
    open_ = base.exclude(status__in=CLOSED_STATUSES)
    return open_.aggregate(
        pending=Count("pk"),
        unassigned=Count("pk", filter=Q(assignee__isnull=True)),
        in_progress=Count("pk", filter=Q(status="in_progress")),
        blocked=Count("pk", filter=Q(status="blocked")),
    ) | base.aggregate(
        raised_today=Count("pk", filter=Q(created_at__date=today)),
        resolved_today=Count("pk", filter=Q(resolved_at__date=today)),
    )


@login_required
def ticket_list(request):
    require(request.user, "ticket.view")
    tickets = visible_tickets(request.user).prefetch_related("labels")
    kpis = _ticket_kpis(request.user)

    q = request.GET.get("q", "").strip()
    type_ = request.GET.get("type", "")
    status = request.GET.get("status", "open")
    priority = request.GET.get("priority", "")
    assignee = request.GET.get("assignee", "")
    project_id = request.GET.get("project", "")
    scope = request.GET.get("scope", "")
    view_mode = request.GET.get("view", "list")
    date_from_raw = request.GET.get("date_from", "").strip()
    date_to_raw = request.GET.get("date_to", "").strip()
    date_from, date_to = _safe_date(date_from_raw), _safe_date(date_to_raw)  # None when blank or not a real date
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from  # entered the wrong way round: still show that range

    if q:
        tickets = tickets.filter(Q(title__icontains=q) | Q(description__icontains=q))
    if type_:
        tickets = tickets.filter(type=type_)
    if status == "open":
        tickets = tickets.exclude(status__in=CLOSED_STATUSES)
    elif status and status != "all":
        tickets = tickets.filter(status=status)
    if priority:
        tickets = tickets.filter(priority=priority)
    if assignee == "none":
        tickets = tickets.filter(assignee__isnull=True)
    elif assignee:
        tickets = tickets.filter(assignee_id=assignee)
    resolved = request.GET.get("resolved", "")
    if resolved == "today":  # from the "Resolved today" card
        tickets = tickets.filter(resolved_at__date=timezone.localdate())
    if date_from:
        tickets = tickets.filter(created_at__date__gte=date_from)  # raised on or after
    if date_to:
        tickets = tickets.filter(created_at__date__lte=date_to)    # raised on or before (the whole day)
    if scope == "mine":
        tickets = tickets.filter(assignee=request.user)
    elif scope == "reported":
        tickets = tickets.filter(reporter=request.user)
    elif scope == "watching":
        tickets = tickets.filter(watchers=request.user)

    project = None
    if project_id:
        project = visible_projects(request.user).filter(pk=project_id).first()
        if project:
            tickets = tickets.filter(project=project)

    columns = None
    if view_mode == "board":
        buckets = {key: [] for key, _ in TICKET_STATUS}
        for ticket in tickets:
            buckets.setdefault(ticket.status, []).append(ticket)
        columns = [{"key": key, "label": label, "tickets": buckets.get(key, [])} for key, label in TICKET_STATUS]

    members = User.objects.filter(is_active=True)
    if project:
        members = members.filter(memberships__project=project).distinct()

    context = {
        "tickets": tickets,
        "columns": columns,
        "view_mode": view_mode,
        "q": q,
        "f_type": type_,
        "f_status": status,
        "f_priority": priority,
        "f_assignee": assignee,
        "f_scope": scope,
        "f_date_from": date_from.isoformat() if date_from else "",
        "f_date_to": date_to.isoformat() if date_to else "",
        "f_project": project_id,
        "project": project,
        "projects": visible_projects(request.user).filter(archived=False),
        "members": members,
        "type_choices": TICKET_TYPES,
        "status_choices": TICKET_STATUS,
        "priority_choices": Ticket._meta.get_field("priority").choices,
        "can_create": has_cap(request.user, "ticket.create"),
        "can_triage": has_cap(request.user, "ticket.triage"),
        "today": timezone.localdate(),
        "tab": "tickets" if project else None,
        "kpis": kpis,
        "f_resolved": resolved,
    }
    template = "tickets/ticket_board.html" if view_mode == "board" else "tickets/ticket_list.html"
    return render(request, template, context)


@login_required
def my_tickets(request):
    base = visible_tickets(request.user).exclude(status__in=CLOSED_STATUSES)
    return render(
        request,
        "tickets/my_tickets.html",
        {
            "groups": [
                {"label": "Assigned to me", "rows": base.filter(assignee=request.user)},
                {"label": "Reported by me", "rows": base.filter(reporter=request.user)},
                {"label": "Watching", "rows": base.filter(watchers=request.user).exclude(assignee=request.user).exclude(reporter=request.user)},
            ],
        },
    )


# --------------------------------------------------------------------------- detail
@login_required
def ticket_detail(request, pk):
    ticket = _get_ticket(request.user, pk)
    can_triage = _can_triage(request.user, ticket)
    can_edit = _can_edit(request.user, ticket)

    if request.method == "POST":
        require(request.user, "ticket.comment")
        form = TicketCommentForm(request.POST)
        if form.is_valid():
            comment = form.save(commit=False)
            comment.ticket = ticket
            comment.author = request.user
            comment.save()
            ticket.watchers.add(request.user)
            TicketEvent.log(ticket, request.user, "commented")
            notify.ticket_commented(ticket, comment)
            return redirect(ticket.get_absolute_url())
    else:
        form = TicketCommentForm()

    members, _pinned = assignable_users(ticket.project)
    context = {
        "ticket": ticket,
        "project": ticket.project,
        "comment_form": form,
        "comments": ticket.comments.select_related("author"),
        "attachments": ticket.attachments.select_related("uploaded_by"),
        "watchers": ticket.watchers.all(),
        "is_watching": ticket.watchers.filter(pk=request.user.pk).exists(),
        "members": members,
        "can_edit": can_edit,
        "can_triage": can_triage,
        "can_delete": has_cap(request.user, "ticket.delete", ticket),
        "can_convert": (
            can_triage
            and ticket.is_open
            and not ticket.linked_task_id
            and has_cap(request.user, "task.create", ticket.project)
        ),
        "type_choices": TICKET_TYPES,
        "status_choices": TICKET_STATUS,
        "priority_choices": Ticket._meta.get_field("priority").choices,
        "severity_choices": Ticket._meta.get_field("severity").choices,
        "patch_url": f"/api/tickets/{ticket.pk}/patch/",
    }
    return render(request, "tickets/ticket_detail.html", context)


def _is_admin(user):
    return bool(user.is_site_admin)


LOG_RANGES = {"today", "week"}


@login_required
def ticket_log(request, pk):
    """Full change history for one ticket, admin-only — reachable only via the Log button on
    the ticket page (not linked for anyone else, and blocked here too if visited directly)."""
    ticket = _get_ticket(request.user, pk)
    if not _is_admin(request.user):
        raise Http404("Ticket not found")

    range_key = request.GET.get("range", "")
    if range_key not in LOG_RANGES:
        range_key = "all"
    events = ticket.events.select_related("actor")
    today = timezone.localdate()
    if range_key == "today":
        events = events.filter(created_at__date=today)
    elif range_key == "week":
        events = events.filter(created_at__date__gte=today - timedelta(days=today.weekday()))

    return render(
        request,
        "tickets/ticket_log.html",
        {
            "ticket": ticket,
            "project": ticket.project,
            "events": events[:500],
            "range_key": range_key,
        },
    )


# --------------------------------------------------------------------------- create / edit
@login_required
def ticket_form(request, project_pk=None, pk=None):
    if pk:
        ticket = _get_ticket(request.user, pk)
        project = ticket.project
        if not _can_edit(request.user, ticket):
            raise PermissionDenied("Missing capability: ticket.edit")
    else:
        ticket = None
        project = None
        # Raising a ticket is allowed against any project the user can see — the
        # project visibility gate below is the boundary, not a per-project grant.
        require(request.user, "ticket.create")
        if project_pk:
            project = get_object_or_404(visible_projects(request.user), pk=project_pk)

    form = TicketForm(request.POST or None, instance=ticket, project=project, user=request.user)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        creating = obj.pk is None
        if creating:
            obj.reporter = request.user
        obj.save()
        form.save_m2m()
        _save_attachments(request, obj)
        if creating:
            obj.watchers.add(request.user)
            if obj.assignee_id:
                obj.watchers.add(obj.assignee)
            TicketEvent.log(obj, request.user, f"raised this {obj.get_type_display().lower()}")
            notify.ticket_raised(obj)
            messages.success(request, f"Ticket {obj.key} raised.")
        else:
            TicketEvent.log(obj, request.user, "edited the ticket")
            messages.success(request, f"Ticket {obj.key} updated.")
        return redirect(obj.get_absolute_url())

    return render(
        request,
        "tickets/ticket_form.html",
        {
            "form": form,
            "ticket": ticket,
            "project": project,
            "bug_fields": ["steps_to_reproduce", "expected_result", "actual_result", "environment"],
        },
    )


@login_required
@require_POST
def ticket_delete(request, pk):
    ticket = _get_ticket(request.user, pk)
    require(request.user, "ticket.delete", ticket)
    key = ticket.key
    ticket.delete()
    messages.success(request, f"Ticket {key} deleted.")
    return redirect("tickets:list")


# --------------------------------------------------------------------------- transitions
@login_required
@require_POST
def ticket_transition(request, pk):
    ticket = _get_ticket(request.user, pk)
    target = request.POST.get("status", "")
    if target not in STATUS_LABELS:
        raise Http404("Unknown status")

    reporter_reopen = ticket.reporter_id == request.user.pk and target in ("new", "closed")
    if not _can_triage(request.user, ticket) and not reporter_reopen:
        raise PermissionDenied("Missing capability: ticket.triage")

    old = ticket.get_status_display()
    resolution = request.POST.get("resolution", "").strip()
    ticket.apply_status(target)
    if resolution:
        ticket.resolution = resolution
    ticket.save()
    note = f" — {ticket.handoff_status}" if ticket.handoff_status else ""
    TicketEvent.log(ticket, request.user, f"moved status from {old} to {ticket.get_status_display()}{note}")
    notify.ticket_status_changed(ticket, request.user, old)
    messages.success(request, f"{ticket.key} is now {ticket.get_status_display()}.")
    return redirect(ticket.get_absolute_url())


@login_required
@require_POST
def ticket_watch(request, pk):
    ticket = _get_ticket(request.user, pk)
    if ticket.watchers.filter(pk=request.user.pk).exists():
        ticket.watchers.remove(request.user)
    else:
        ticket.watchers.add(request.user)
    return redirect(ticket.get_absolute_url())


@login_required
@require_POST
def ticket_convert_to_task(request, pk):
    ticket = _get_ticket(request.user, pk)
    require(request.user, "ticket.triage", ticket)
    require(request.user, "task.create", ticket.project)
    if ticket.linked_task_id:
        messages.info(request, "This ticket is already linked to a task.")
        return redirect(ticket.get_absolute_url())

    task = Task.objects.create(
        project=ticket.project,
        title=ticket.title,
        description=ticket.description or ticket.steps_to_reproduce,
        priority=ticket.priority,
        assignee=ticket.assignee,
        reporter=ticket.reporter or request.user,
    )
    ticket.linked_task = task
    if ticket.status in ("new", "triaged"):
        ticket.apply_status("accepted")
    ticket.save()
    TicketEvent.log(ticket, request.user, f"converted this ticket into task “{task.title}”")
    Activity.log(request.user, f"created task “{task.title}” from ticket {ticket.key}", task=task)
    notify.ticket_status_changed(ticket, request.user, "")
    messages.success(request, f"Task created from {ticket.key}.")
    return redirect(task.get_absolute_url())


# --------------------------------------------------------------------------- attachments
@login_required
@require_POST
def ticket_attachment_upload(request, pk):
    ticket = _get_ticket(request.user, pk)
    require(request.user, "ticket.comment")
    saved, skipped = _save_attachments(request, ticket, field="files")
    if not saved and not skipped:
        messages.error(request, "No file selected.")
    elif saved:
        messages.success(request, f"Added {len(saved)} attachment(s).")
    return redirect(ticket.get_absolute_url())


@login_required
@require_POST
def ticket_attachment_delete(request, pk):
    attachment = get_object_or_404(TicketAttachment, pk=pk)
    ticket = _get_ticket(request.user, attachment.ticket_id)
    if attachment.uploaded_by_id != request.user.pk and not _can_edit(request.user, ticket):
        raise PermissionDenied("Not allowed")
    attachment.file.delete(save=False)
    attachment.delete()
    return redirect(ticket.get_absolute_url())


# --------------------------------------------------------------------------- JSON patch (inline sidebar)
@login_required
@require_POST
def api_ticket_patch(request, pk):
    ticket = _get_ticket(request.user, pk)
    if not _can_triage(request.user, ticket) and not has_cap(request.user, "ticket.edit", ticket):
        raise PermissionDenied("Missing capability: ticket.triage")
    payload = json.loads(request.body or "{}")

    changed = []
    events = []
    if "assignee" in payload:
        new_id = int(payload["assignee"]) if payload["assignee"] else None
        if new_id and new_id != ticket.assignee_id and not assignable_users(ticket.project)[0].filter(pk=new_id).exists():
            return JsonResponse({"error": f"That person isn't on {ticket.project.key}."}, status=400)
        if new_id != ticket.assignee_id:
            ticket.assignee_id = new_id
            changed.append("assignee")
            if new_id:
                ticket.watchers.add(new_id)
                who = User.objects.filter(pk=new_id).first()
                events.append(f"assigned to {who.display_name if who else 'someone'}")
            else:
                events.append("unassigned")
    if "priority" in payload:
        ticket.priority = int(payload["priority"])
        changed.append("priority")
        events.append(f"set priority to {ticket.get_priority_display()}")
    if "severity" in payload:
        ticket.severity = int(payload["severity"]) if payload["severity"] else None
        changed.append("severity")
        events.append(f"set severity to {ticket.get_severity_display() or '—'}")
    if "type" in payload and payload["type"] in TYPE_LABELS:
        ticket.type = payload["type"]
        changed.append("type")
        events.append(f"changed type to {ticket.get_type_display()}")
    if "status" in payload and payload["status"] in STATUS_LABELS:
        old = ticket.get_status_display()
        ticket.apply_status(payload["status"])
        changed += ["status", "resolved_at", "closed_at", "handoff_status"]
        note = f" — {ticket.handoff_status}" if ticket.handoff_status else ""
        events.append(f"moved status from {old} to {ticket.get_status_display()}{note}")
    if "component" in payload:
        ticket.component = payload["component"][:120]
        changed.append("component")
    if "due_date" in payload:
        ticket.due_date = payload["due_date"] or None
        changed.append("due_date")

    if changed:
        ticket.save(update_fields=list(dict.fromkeys(changed)) + ["updated_at"])
        for verb in events:
            TicketEvent.log(ticket, request.user, verb)
        if "assignee" in changed and ticket.assignee_id:
            notify.ticket_assigned(ticket, request.user)
        elif "status" in changed:
            notify.ticket_status_changed(ticket, request.user, "")
    return JsonResponse({"ok": True, "changed": changed})
