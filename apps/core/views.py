import json
import os
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import SuspiciousFileOperation
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts import site_settings
from apps.accounts.permissions import has_cap, require
from apps.intake.models import BRDVersion, TimelineEntry
from apps.projects.models import Activity, Task
from apps.projects.selectors import visible_customers, visible_projects, visible_tasks, visible_users
from apps.tickets.models import CLOSED_STATUSES
from apps.tickets.selectors import visible_tickets
from apps.vms.models import VM


@login_required
def dashboard(request):
    if request.user.is_client_login:
        return _client_dashboard(request)
    today = timezone.localdate()
    projects = visible_projects(request.user).filter(archived=False).select_related("customer")
    tasks = visible_tasks(request.user)
    mine = tasks.filter(assignee=request.user).exclude(state__category="done")

    # group the projects the user can see under their customer
    by_customer = {}
    for project in projects:
        by_customer.setdefault(project.customer_id, []).append(project)

    customers = []
    for customer in visible_customers(request.user):
        rows = by_customer.get(customer.pk, [])
        customer_tasks = tasks.filter(project__customer=customer)
        customers.append(
            {
                "customer": customer,
                "projects": rows,
                "stats": customer.stats(rows),
                "open_tasks": customer_tasks.exclude(state__category="done").count(),
                "overdue": customer_tasks.filter(due_date__lt=today).exclude(state__category="done").count(),
                "entries": TimelineEntry.objects.filter(project__customer=customer).count(),
                "brd_versions": BRDVersion.objects.filter(brd__project__customer=customer).count(),
            }
        )
    customers.sort(key=lambda row: (-row["stats"]["active"], row["customer"].name))

    by_state = list(
        tasks.values("state__name", "state__color", "state__order").annotate(n=Count("id")).order_by("state__order")
    )
    chart_states = [
        {"label": r["state__name"] or "Unset", "color": r["state__color"] or "#94a3b8", "value": r["n"]}
        for r in by_state
    ]
    burn = []
    for offset in range(13, -1, -1):
        day = today - timedelta(days=offset)
        burn.append(
            {
                "label": day.strftime("%d %b"),
                "created": tasks.filter(created_at__date=day).count(),
                "done": tasks.filter(state__category="done", updated_at__date=day).count(),
            }
        )

    tickets = visible_tickets(request.user)
    open_tickets = tickets.exclude(status__in=CLOSED_STATUSES)

    context = {
        "customers": customers,
        "unassigned": by_customer.get(None, []),
        "ticket_open": open_tickets.count(),
        "ticket_mine": open_tickets.filter(assignee=request.user).count(),
        "my_tickets": open_tickets.filter(assignee=request.user).select_related("project")[:8],
        "customer_count": len(customers),
        "project_count": projects.count(),
        "task_count": tasks.count(),
        "user_count": User.objects.count(),
        "vm_count": VM.objects.count(),
        "my_tasks": mine.select_related("project", "state")[:10],
        "my_open": mine.count(),
        "overdue": tasks.filter(due_date__lt=today).exclude(state__category="done").count(),
        "due_this_week": tasks.filter(due_date__gte=today, due_date__lte=today + timedelta(days=7))
        .exclude(state__category="done").count(),
        "people": visible_users(request.user).count(),
        "hours": tasks.aggregate(e=Sum("estimate_hours"), s=Sum("spent_hours")),
        "activity_count": Activity.objects.filter(Q(project__in=projects) | Q(project__isnull=True)).count(),
        "chart_states": json.dumps(chart_states),
        "chart_burn": json.dumps(burn),
        "milestones": [m for p in projects for m in p.milestones.filter(completed=False, due_date__isnull=False)][:6],
        "can_create_project": has_cap(request.user, "project.create"),
        "can_manage_customers": has_cap(request.user, "customer.manage"),
        "can_view_customers": has_cap(request.user, "customer.view"),
        "is_admin": request.user.is_site_admin,
    }
    if request.user.is_site_admin:
        # same rule as the timesheet's Present / Not logged in today (staff only, any login today)
        from apps.timesheet import permissions as ts_perms, services as ts_services

        today_counts = ts_services.team_today(ts_perms.viewable_people(request.user))
        context["logged_in_today"] = today_counts["present"]
        context["not_logged_in_today"] = today_counts["absent"]
    context.update(_dashboard_graphs(request))
    return render(request, "core/dashboard.html", context)


def _ticket_card_json(request, build):
    """Shared by the ticket cards: check the permission and the dates, then answer with build(user, start, end)."""
    from django.utils.dateparse import parse_date

    from . import analytics

    if not has_cap(request.user, "ticket.view"):
        return JsonResponse({"error": "Not allowed."}, status=403)
    start, end = parse_date(request.GET.get("from", "")), parse_date(request.GET.get("to", ""))
    if not start or not end or start > end or (end - start).days > analytics.MAX_SPAN_DAYS:
        return JsonResponse({"error": "Choose a valid date range (up to one year)."}, status=400)
    return JsonResponse(build(request.user, start, end))


@login_required
def tickets_by_status(request):
    """JSON for the "Tickets by Status" card, so changing its dates redraws the card without reloading the page."""
    from . import analytics

    return _ticket_card_json(request, analytics.ticket_status_counts)


@login_required
def tickets_per_project(request):
    """JSON for the "Tickets per project" card (busiest 10 projects for the dates)."""
    from . import analytics

    return _ticket_card_json(request, analytics.ticket_project_counts)


def _dashboard_graphs(request):
    """Graph data for the dashboard. If anything goes wrong here the rest of the dashboard still loads."""
    import logging

    from apps.projects.selectors import visible_customers

    from . import analytics

    try:
        dash = analytics.build(request.user, request.GET)
        f = dash["filters"]
        return {
            "dash": dash,
            "dash_ranges": [("7", "Last 7 days"), ("30", "Last 30 days"), ("90", "Last 90 days"), ("custom", "Custom range")],
            "dash_json": {"sections": dash["sections"]},
            "dash_filter": {"range": f["range"], "project": f["project"] or "", "customer": f["customer"] or "",
                            "from": f["from_value"], "to": f["to_value"]},
            "dash_projects": list(visible_projects(request.user).filter(archived=False).order_by("name").values("id", "key", "name")),
            "dash_customers": list(visible_customers(request.user).filter(is_active=True).order_by("name").values("id", "name")),
        }
    except Exception:  # noqa: BLE001 - a graph problem must never take the dashboard down
        logging.getLogger(__name__).exception("dashboard graphs failed")
        return {"dash": None}


def _client_dashboard(request):
    """What a client-side login sees: the projects assigned to them and each project's external links."""
    projects = list(
        visible_projects(request.user).filter(archived=False).select_related("customer").prefetch_related("links")
        .order_by("name")
    )
    return render(request, "core/client_dashboard.html", {
        "rows": [{"project": p, "links": list(p.links.all())} for p in projects],
    })


@login_required
def activity_list(request):
    projects = visible_projects(request.user).filter(archived=False)
    activities = Activity.objects.select_related("actor", "project", "task").filter(
        Q(project__in=projects) | Q(project__isnull=True)
    )

    q = request.GET.get("q", "").strip()
    actor_id = request.GET.get("actor", "")
    project_id = request.GET.get("project", "")
    if q:
        activities = activities.filter(verb__icontains=q)
    if actor_id:
        activities = activities.filter(actor_id=actor_id)
    if project_id:
        activities = activities.filter(project_id=project_id)

    context = {
        "activities": activities[:200],
        "q": q,
        "actor_id": actor_id,
        "project_id": project_id,
        "actors": visible_users(request.user).order_by("first_name", "username"),
        "projects": projects,
    }
    return render(request, "core/activity_list.html", context)


@login_required
def internal_projects(request):
    projects = visible_projects(request.user).filter(archived=False, customer__isnull=True)
    return render(request, "core/internal_projects.html", {"projects": projects})


@login_required
def logged_in_users(request):
    require(request.user, "user.view")
    cutoff = timezone.now() - timedelta(seconds=site_settings.idle_timeout_seconds())
    people = User.objects.filter(last_active__gte=cutoff).select_related("role", "team").order_by("-last_active")
    return render(request, "core/logged_in_users.html", {"people": people, "cutoff": cutoff})


@login_required
def media_serve(request, path):
    """Serve uploads with HTTP Range support, behind a login.

    Two reasons this exists instead of django.views.static.serve:
      * that view ignores Range, so a browser cannot seek inside a long recording —
        it would have to download the whole file before playing a 20-second sample;
      * uploaded call recordings should not be readable by anyone who guesses the URL.
    """
    import mimetypes
    import re

    from django.http import FileResponse, Http404, HttpResponse
    from django.utils._os import safe_join

    try:
        full_path = safe_join(settings.MEDIA_ROOT, path)
    except (ValueError, SuspiciousFileOperation):
        raise Http404("Not found")
    parts = path.split("/")
    if len(parts) > 3 and parts[0] == "projects" and parts[2] == "files":
        raise Http404("Not found")  # project documents are served by the Documents tab, which checks project access
    if not os.path.exists(full_path) or not os.path.isfile(full_path):
        raise Http404("Not found")

    size = os.path.getsize(full_path)
    content_type = mimetypes.guess_type(full_path)[0] or "application/octet-stream"
    safe_inline = (
        content_type.startswith(("audio/", "video/"))
        or content_type in {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp",
                            "application/pdf", "text/plain", "text/csv"}
    )

    def _harden(response):
        # uploads can be any type (even .html / .svg): never let the browser run them on our origin
        response["X-Content-Type-Options"] = "nosniff"
        if not safe_inline:
            name = os.path.basename(full_path).replace('"', "")
            response["Content-Disposition"] = f'attachment; filename="{name}"'
        return response

    range_header = request.META.get("HTTP_RANGE", "")
    match = re.match(r"bytes=(\d*)-(\d*)", range_header)

    if not match:
        response = FileResponse(open(full_path, "rb"), content_type=content_type)
        response["Accept-Ranges"] = "bytes"
        response["Content-Length"] = str(size)
        return _harden(response)

    start = int(match.group(1)) if match.group(1) else 0
    end = int(match.group(2)) if match.group(2) else size - 1
    end = min(end, size - 1)
    if start > end or start >= size:
        response = HttpResponse(status=416)
        response["Content-Range"] = f"bytes */{size}"
        return response

    length = end - start + 1
    handle = open(full_path, "rb")
    handle.seek(start)
    response = FileResponse(handle, status=206, content_type=content_type)
    response["Content-Range"] = f"bytes {start}-{end}/{size}"
    response["Content-Length"] = str(length)
    response["Accept-Ranges"] = "bytes"
    return _harden(response)


def home(request):
    if request.user.is_authenticated:
        return redirect("core:dashboard")
    return redirect("accounts:login")


@login_required
def docs(request):
    require(request.user, "docs.view")
    return render(request, "core/docs.html", {"tab": "docs"})


@login_required
def search(request):
    q = request.GET.get("q", "").strip()
    projects = tasks = people = customers = []
    if q:
        projects = visible_projects(request.user).filter(Q(name__icontains=q) | Q(key__icontains=q))[:10]
        tasks = visible_tasks(request.user).filter(title__icontains=q)[:20]
        people = visible_users(request.user).filter(
            Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(username__icontains=q)
        )[:10]
        customers = visible_customers(request.user).filter(Q(name__icontains=q) | Q(code__icontains=q))[:10]
    return render(
        request,
        "core/search.html",
        {"q": q, "projects": projects, "tasks": tasks, "people": people, "customers": customers},
    )
