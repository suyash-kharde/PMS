from datetime import datetime, timedelta

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.permissions import require

from .models import ACTIONS, MODULES, STATUSES, AuditLog

PAGE_SIZE = 25

PRESETS = ["today", "yesterday", "7d", "30d", "custom"]


def _preset_range(preset):
    """-> (start, end) datetimes for a named preset, or (None, None) for 'custom'/unknown."""
    now = timezone.localtime()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if preset == "today":
        return today_start, now
    if preset == "yesterday":
        return today_start - timedelta(days=1), today_start
    if preset == "7d":
        return today_start - timedelta(days=7), now
    if preset == "30d":
        return today_start - timedelta(days=30), now
    return None, None


def _parse_local(value):
    """-> an aware datetime for a 'YYYY-MM-DDTHH:MM' input value, or None."""
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            naive = datetime.strptime(value, fmt)
            return timezone.make_aware(naive, timezone.get_current_timezone())
        except ValueError:
            continue
    return None


@login_required
def user_activity(request, pk):
    person = get_object_or_404(User, pk=pk)
    require(request.user, "role.manage")  # the activity log is for administrators only

    preset = request.GET.get("range", "7d")
    if preset not in PRESETS:
        preset = "7d"
    start, end = _preset_range(preset)
    if preset == "custom":
        start = _parse_local(request.GET.get("from")) or start
        end = _parse_local(request.GET.get("to")) or end

    logs = (
        AuditLog.objects.filter(actor=person)
        .select_related("actor")
        # older rows where every opened page was logged as a "Create" (see audit/generic.py)
        .exclude(module="org", object_repr__regex=r" @ \d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
        .exclude(module="org", object_repr__regex=r" \d{4}-\d{2}-\d{2}: \d+s$")
    )
    if start:
        logs = logs.filter(created_at__gte=start)
    if end:
        logs = logs.filter(created_at__lte=end)

    action = request.GET.get("action", "")
    if action:
        logs = logs.filter(action=action)
    module = request.GET.get("module", "")
    if module:
        logs = logs.filter(module=module)
    status = request.GET.get("status", "")
    if status:
        logs = logs.filter(status=status)

    paginator = Paginator(logs, PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "audit/user_activity.html",
        {
            "person": person,
            "page": page,
            "preset": preset,
            "action": action,
            "module": module,
            "status": status,
            "from_value": request.GET.get("from", ""),
            "to_value": request.GET.get("to", ""),
            "action_choices": ACTIONS,
            "module_choices": MODULES,
            "status_choices": STATUSES,
        },
    )
