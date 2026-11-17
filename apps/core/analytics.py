"""Numbers for the dashboard graphs. Everything here only reads, and only from what the signed-in person is already
allowed to see (the same selectors the rest of Planwise uses). The ORM part is thin: it pulls plain rows, and the
counting is done by small pure functions below so it is easy to test.

A ticket is "pending" while its status is one of tickets.models.OPEN_STATUSES. When it stopped being pending is taken
from resolved_at / closed_at, else from its status-change history, else its last update."""
from collections import Counter
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date

PALETTE = ["#6366f1", "#22c55e", "#f59e0b", "#ef4444", "#06b6d4", "#a855f7", "#84cc16", "#f97316", "#64748b"]
PRESETS = {"7": 7, "30": 30, "90": 90}
MAX_SPAN_DAYS = 366


# ----------------------------------------------------------------------------- filters and time buckets
def parse_filters(GET, today=None):
    today = today or timezone.localdate()
    rng = GET.get("range", "30")
    if GET.get("from") or GET.get("to"):  # dates typed in -> those dates are used, whatever the drop-down says
        rng = "custom"
    if rng == "custom":
        end = parse_date(GET.get("to") or "") or today
        start = parse_date(GET.get("from") or "") or end - timedelta(days=29)
    else:
        rng = rng if rng in PRESETS else "30"
        end, start = today, today - timedelta(days=PRESETS[rng] - 1)
    if start > end:
        start, end = end, start
    start = max(start, end - timedelta(days=MAX_SPAN_DAYS - 1))
    pick = lambda key: int(GET[key]) if GET.get(key, "").isdigit() else None  # noqa: E731
    return {"range": rng, "start": start, "end": end, "project": pick("project"), "customer": pick("customer"),
            "from_value": GET.get("from", ""), "to_value": GET.get("to", "")}


def week_start(day):
    return day - timedelta(days=day.weekday())


def buckets(start, end):
    """-> [(label, first_day, last_day)]: one per day for a range up to 31 days, else one per Monday-Sunday week
    (the first and last week are cut to the range)."""
    out = []
    if (end - start).days <= 31:
        day = start
        while day <= end:
            out.append((f"{day.day} {day:%b}", day, day))
            day += timedelta(days=1)
        return out
    monday = week_start(start)
    while monday <= end:
        first, last = max(monday, start), min(monday + timedelta(days=6), end)
        out.append((f"{first.day} {first:%b}", first, last))
        monday += timedelta(days=7)
    return out


# ----------------------------------------------------------------------------- pure counting
def left_open_day(row, open_statuses):
    """The moment a ticket stopped being pending, or None while it still is."""
    if row["status"] in open_statuses:
        return None
    moment = row.get("resolved_at") or row.get("closed_at") or row.get("closed_event") or row.get("updated_at")
    return moment


def pending_on(rows, day, open_statuses, to_date):
    """How many tickets were pending at the end of `day`."""
    count = 0
    for r in rows:
        if to_date(r["created_at"]) > day:
            continue
        left = left_open_day(r, open_statuses)
        if left is None or to_date(left) > day:
            count += 1
    return count


def count_between(moments, first, last, to_date):
    return sum(1 for m in moments if m is not None and first <= to_date(m) <= last)


# ----------------------------------------------------------------------------- chart helpers
def _items(counter, labels, colors=None, hrefs=None):
    items = []
    for i, (key, label) in enumerate(labels):
        value = counter.get(key, 0)
        if value:
            items.append({"label": label, "value": value, "color": (colors or {}).get(key) or PALETTE[i % len(PALETTE)],
                          "href": (hrefs or {}).get(key, "")})
    return items


def _chart(cid, kind, title, note="", **data):
    chart = {"id": cid, "type": kind, "title": title, "note": note}
    chart.update(data)
    chart["empty"] = not (data.get("items") or any(any(s["values"]) for s in data.get("series", [])))
    # the total is written into the note, so a change of dates is visible even when the shapes look alike
    if data.get("items"):
        total = sum(i["value"] for i in data["items"])
    else:
        total = sum(sum(s["values"]) for s in data.get("series", []))
    if not chart["empty"]:
        shown = int(total) if float(total).is_integer() else round(total, 1)
        chart["note"] = (note + " · " if note else "") + f"{shown:,} in total"
    return chart


def _top_items(counter, limit=10):
    return [{"label": name, "value": value, "color": PALETTE[0], "href": ""}
            for name, value in sorted(counter.items(), key=lambda kv: -kv[1])[:limit]]


# ----------------------------------------------------------------------------- tickets by status (own date range)
STATUS_COLORS = {"resolved": "#22c55e", "new": "#2563eb", "in_progress": "#f59e0b", "solved": "#a855f7", "closed": "#64748b",
                 "rejected": "#ef4444", "triaged": "#06b6d4", "accepted": "#14b8a6", "blocked": "#dc2626"}


def status_color(key):
    """A fixed colour for the known statuses; any other status gets a stable colour of its own."""
    return STATUS_COLORS.get(key) or PALETTE[sum(ord(ch) for ch in str(key)) % len(PALETTE)]


def ticket_status_counts(user, start, end):
    """Tickets raised from `start` to `end` (the ones this person may see), counted by their status now. The statuses
    are whatever exists in the data, biggest first, so a new status shows up on its own."""
    from apps.tickets.models import TICKET_STATUS
    from apps.tickets.selectors import visible_tickets

    qs = visible_tickets(user).filter(created_at__date__gte=start, created_at__date__lte=end)
    counts = Counter(qs.values_list("status", flat=True))
    labels = dict(TICKET_STATUS)
    items = [{"key": key, "label": labels.get(key) or str(key).replace("_", " ").title(), "value": value,
              "color": status_color(key), "href": reverse("tickets:list") + f"?status={key}"}
             for key, value in sorted(counts.items(), key=lambda kv: (-kv[1], labels.get(kv[0], kv[0])))]
    return {"items": items, "total": sum(counts.values()), "from": start.isoformat(), "to": end.isoformat()}


PROJECT_BAR_COLOR = "#3b82f6"


def ticket_project_counts(user, start, end, limit=10):
    """Tickets raised from `start` to `end` (the ones this person may see), counted per project: the busiest `limit`
    projects, biggest first. Only projects that have tickets in the period appear."""
    from apps.tickets.selectors import visible_tickets

    qs = visible_tickets(user).filter(created_at__date__gte=start, created_at__date__lte=end)
    counts = Counter()
    names = {}
    for pid, key, name in qs.values_list("project_id", "project__key", "project__name"):
        counts[pid] += 1
        names[pid] = (key, name)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], names[kv[0]][0].lower()))[:limit]
    items = [{"key": pid, "label": names[pid][0], "title": names[pid][1], "value": value, "color": PROJECT_BAR_COLOR,
              "href": reverse("tickets:list") + f"?project={pid}&status=all"} for pid, value in ranked]
    return {"items": items, "total": sum(i["value"] for i in items), "from": start.isoformat(), "to": end.isoformat(),
            "limit": limit}


# ----------------------------------------------------------------------------- the sections
def tickets_section(user, f):
    from apps.tickets.models import OPEN_STATUSES, TicketEvent
    from apps.tickets.selectors import visible_tickets

    qs = visible_tickets(user)
    if f["project"]:
        qs = qs.filter(project_id=f["project"])
    if f["customer"]:
        qs = qs.filter(project__customer_id=f["customer"])
    rows = list(qs.values("id", "status", "priority", "assignee_id", "created_at", "resolved_at", "closed_at",
                          "updated_at", "project__key", "project__customer__name"))
    done_ids = [r["id"] for r in rows if r["status"] not in OPEN_STATUSES and not (r["resolved_at"] or r["closed_at"])]
    if done_ids:  # no timestamp saved: use when its status was moved to Resolved / Closed / Rejected
        closed = {}
        for ev in TicketEvent.objects.filter(ticket_id__in=done_ids, verb__regex=r"^moved status from .* to (Resolved|Closed|Rejected)").values("ticket_id", "created_at"):
            closed[ev["ticket_id"]] = min(ev["created_at"], closed.get(ev["ticket_id"], ev["created_at"]))
        for r in rows:
            r["closed_event"] = closed.get(r["id"])
    to_date = lambda dt: timezone.localtime(dt).date()  # noqa: E731
    today = timezone.localdate()

    open_rows = [r for r in rows if r["status"] in OPEN_STATUSES]
    in_range = [r for r in rows if f["start"] <= to_date(r["created_at"]) <= f["end"]]

    solved_moments = [left_open_day(r, OPEN_STATUSES) for r in rows]


    this_monday = week_start(today)
    last_week_end = this_monday - timedelta(days=1)
    open_now = len(open_rows)
    open_before = pending_on(rows, last_week_end, OPEN_STATUSES, to_date)
    solved_week = count_between(solved_moments, this_monday, today, to_date)
    unassigned = sum(1 for r in open_rows if not r["assignee_id"])

    note_range = f"{f['start']:%d %b} to {f['end']:%d %b %Y}"
    cards = {"open": open_now, "open_change": open_now - open_before, "open_change_abs": abs(open_now - open_before),
             "solved_week": solved_week, "unassigned": unassigned}
    status_card = dict(ticket_status_counts(user, f["start"], f["end"]), col1="Status", col2="Count")
    project_card = ticket_project_counts(user, f["start"], f["end"])
    charts = [
        _chart("t-status", "status", "Tickets by Status", "", **status_card),
        _chart("t-project", "project", "Tickets per project", "", col1="Project", col2="Tickets", **project_card),
    ]
    return {"cards": cards, "charts": charts}


def projects_section(user, f):
    from apps.projects.models import PROJECT_STATUS
    from apps.projects.selectors import visible_projects

    qs = visible_projects(user).filter(archived=False)
    if f["customer"]:
        qs = qs.filter(customer_id=f["customer"])
    if f["project"]:
        qs = qs.filter(pk=f["project"])
    all_rows = list(qs.values("status", "created_at", "customer__name"))
    rows = [r for r in all_rows if f["start"] <= timezone.localtime(r["created_at"]).date() <= f["end"]]
    note_range = f"created {f['start']:%d %b} to {f['end']:%d %b %Y}"
    status_labels = dict(PROJECT_STATUS)
    by_status = Counter(r["status"] for r in all_rows)  # every project till date, not just the date range
    colors = {"lead": "#94a3b8", "proposal": "#06b6d4", "active": "#22c55e", "under_amc": "#6366f1", "completed": "#a855f7"}
    hrefs = {k: reverse("projects:list") + f"?status={k}" for k in by_status}
    per_customer = Counter(r["customer__name"] for r in rows if r["customer__name"])
    bks = buckets(f["start"], f["end"])
    made = [sum(1 for r in rows if first <= timezone.localtime(r["created_at"]).date() <= last) for _l, first, last in bks]
    return {"charts": [
        _chart("p-status", "donut", "Projects by status", "All projects till date", items=_items(by_status, [(k, v) for k, v in PROJECT_STATUS], colors, hrefs)),
        _chart("p-customer", "bars", "Projects per customer", f"Projects {note_range}; top 10", items=_top_items(per_customer)),
        _chart("p-new", "line", "New projects over time", f"{f['start']:%d %b} to {f['end']:%d %b %Y}", labels=[b[0] for b in bks],
               series=[{"name": "New projects", "values": made, "color": "#6366f1"}], area=True),
    ]}


def attendance_section(user, f, people):
    from apps.timesheet.models import Attendance, TimesheetDay

    # always the current month so far (1st to today), whatever date range the dashboard filter is set to
    end = timezone.localdate()
    f = {**f, "start": end.replace(day=1), "end": end}
    present, absent, leave = Counter(), Counter(), Counter()
    for r in Attendance.objects.filter(user__in=people, date__gte=f["start"], date__lte=f["end"]).values("date", "status"):
        (present if r["status"] == "present" else absent)[r["date"]] += 1
    for r in TimesheetDay.objects.filter(user__in=people, date__gte=f["start"], date__lte=f["end"], leave_hours__gt=0).values("date"):
        leave[r["date"]] += 1
    bks = buckets(f["start"], f["end"])

    def total(counter, first, last):
        return sum(v for d, v in counter.items() if first <= d <= last)

    # muted, standard tones (softer than the bright Tailwind 500s) so the bars are easy on the eye
    series = [
        {"name": "Present", "values": [total(present, b[1], b[2]) for b in bks], "color": "#3f8f5f"},
        {"name": "Absent", "values": [total(absent, b[1], b[2]) for b in bks], "color": "#c2524a"},
        {"name": "On leave", "values": [total(leave, b[1], b[2]) for b in bks], "color": "#d4a03d"},
    ]
    return {"charts": [_chart("a-day", "stacked", "Present, absent and on leave", f"This month: {f['start']:%d %b} to {f['end']:%d %b %Y}; people-days",
                              labels=[b[0] for b in bks], series=series)]}


# ----------------------------------------------------------------------------- everything the page needs
def build(user, GET):
    from apps.accounts.permissions import has_cap
    from apps.timesheet import permissions as ts_perms

    f = parse_filters(GET)
    people = ts_perms.viewable_people(user)
    sections = []
    cards = {}
    if has_cap(user, "ticket.view"):
        t = tickets_section(user, f)
        cards.update(t["cards"])
        sections.append({"key": "tickets", "title": "Tickets", "charts": t["charts"]})
    # the attendance chart sits next to "New projects over time" (the third Projects chart), filling that row
    charts = projects_section(user, f)["charts"] + attendance_section(user, f, people)["charts"]
    sections.append({"key": "projects", "title": "Projects & attendance", "charts": charts})
    return {"filters": f, "cards": cards, "sections": sections}
