import calendar as pycal
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from apps.accounts.permissions import has_cap
from apps.projects.models import Project

from . import attendance_export, rules, services
from . import permissions as perms
from .models import Attendance, Holiday, TimesheetDay, TimesheetEntry, WorkingDay


def _nav(request):
    user = request.user
    return {
        "ts_has_people": perms.can_see_team(user),
        "ts_is_admin": perms.is_admin(user),
        "ts_is_manager": perms.can_see_team(user),
        "ts_can_holidays": perms.can_manage_holidays(user),
    }


def _need_tab(user, tab):
    """Each Timesheet tab is its own permission (timesheet.tab.<tab>), set per role on the role page."""
    if not has_cap(user, f"timesheet.tab.{tab}"):
        raise PermissionDenied("Your role doesn't include this Timesheet tab.")


def _first_allowed_tab(user):
    """The Timesheet page opens on the first tab this person may use (None if they have none)."""
    order = [("week", "timesheet:week", True), ("people", "timesheet:people", perms.can_see_team(user)),
             ("calendar", "timesheet:calendar", True), ("holidays", "timesheet:holidays", True),
             ("attendance", "timesheet:attendance", perms.can_see_team(user)),
             ("reports", "timesheet:reports", True)]
    for tab, name, available in order:
        if available and has_cap(user, f"timesheet.tab.{tab}"):
            return reverse(name)
    return None


def _target(request, param="user"):
    """The person whose timesheet is being looked at; defaults to yourself."""
    pk = request.GET.get(param) or request.POST.get(param)
    target = get_object_or_404(User, pk=pk) if pk else request.user
    if not perms.can_view_timesheet(request.user, target):
        raise PermissionDenied("You can't view this person's timesheet.")
    return target


def _week(request):
    day = parse_date(request.GET.get("week") or request.POST.get("week") or "") or services.today()
    return rules.week_start(day)


def _qs(**params):
    return "?" + "&".join(f"{k}={v}" for k, v in params.items() if v not in (None, ""))


# --------------------------------------------------------------------------- my timesheet (week)
@login_required
def week_view(request):
    if not has_cap(request.user, "timesheet.tab.week"):
        landing = _first_allowed_tab(request.user) if request.method == "GET" else None
        if landing:
            return redirect(landing)
        raise PermissionDenied("Your role doesn't include the Timesheet page.")
    target = _target(request)
    monday = _week(request)
    back = reverse("timesheet:week") + _qs(user=target.pk if target.pk != request.user.pk else "", week=monday)

    if request.method == "POST":
        raw, leave_raw = {}, {}
        for key, value in request.POST.items():
            if key.startswith("h_"):
                _, day_s, pid = key.split("_", 2)
                day = parse_date(day_s)
                if day and pid.isdigit():
                    raw[(day, int(pid))] = value
            elif key.startswith("l_"):  # the Leave row
                day = parse_date(key[2:])
                if day:
                    leave_raw[day] = value
        loaded_at = parse_datetime(request.POST.get("loaded_at", "") or "")
        was_submitted = TimesheetDay.objects.filter(
            user=target, date__in=rules.week_days(monday), status="submitted").exists()
        errors, warnings = services.save_week(
            request.user, target, monday, raw, submit=request.POST.get("action") == "submit", loaded_at=loaded_at,
            leave_raw=leave_raw,
        )
        for e in errors:
            messages.error(request, e)
        for w in warnings:
            messages.warning(request, w)
        if not errors:
            if request.POST.get("action") == "submit":
                messages.success(request, "Timesheet updated." if was_submitted else "Timesheet submitted.")
            else:
                messages.success(request, "Draft saved.")
        return redirect(back)

    grid = services.week_grid(request.user, target, monday)
    profile = grid["profile"]
    admin_cards = None
    if perms.is_admin(request.user):  # team headline numbers, for admins only
        scope = perms.viewable_people(request.user)
        team = services.people_week(scope, monday)
        admin_cards = {"today": services.team_today(scope), "fully": team["fully_submitted"], "people": len(team["rows"])}
    context = {
        **_nav(request), "tab": "week", "target": target, "monday": monday, "grid": grid,
        "prev": monday - timedelta(days=7), "next": monday + timedelta(days=7),
        "this_week": rules.week_start(services.today()),
        "people": perms.viewable_people(request.user),
        "can_edit_any": any(d["editable"] for d in grid["days"]),
        "can_remainder": perms.can_set_remainder(request.user, target),
        "can_star": perms.can_set_main_projects(request.user, target),
        "can_profile": perms.can_manage_profile(request.user),
        "profile": profile,
        "unlocks": target.timesheet_unlocks.filter(active=True),
        "loaded_at": timezone.now().isoformat(),
        "month_first": monday.replace(day=1),
        "edit_until": rules.month_end(monday),
        "edit_locked": rules.is_locked(monday, services.today()),
        "keep": {"week": monday.isoformat()}, "admin_cards": admin_cards,
    }
    return render(request, "timesheet/week.html", context)


@login_required
@require_POST
def person_settings(request, pk):
    target = get_object_or_404(User, pk=pk)
    action = request.POST.get("action")
    week = request.POST.get("week", "")
    if action == "remainder":
        pid = request.POST.get("remainder_project")
        project = get_object_or_404(Project, pk=pid) if pid else None
        services.set_remainder_project(request.user, target, project)
        messages.success(request, "Remainder project saved. It applies from today.")
    elif action == "employment":
        services.set_employment_dates(
            request.user, target, parse_date(request.POST.get("joining_date") or ""),
            parse_date(request.POST.get("last_working_day") or ""),
        )
        messages.success(request, "Joining / last working day saved.")
    elif action in ("unlock", "relock"):
        month = parse_date((request.POST.get("month") or "") + "-01") if len(request.POST.get("month", "")) == 7 \
            else parse_date(request.POST.get("month") or "")
        if not month:
            messages.error(request, "Pick a month.")
        else:
            services.set_unlock(request.user, target, month.replace(day=1), action == "unlock")
            messages.success(request, f"{month:%B %Y} {'unlocked' if action == 'unlock' else 'locked again'} for {target.display_name}.")
    else:
        raise PermissionDenied("Unknown action.")
    return redirect(reverse("timesheet:week") + _qs(user=target.pk, week=week))


@login_required
@require_POST
def main_project(request, pk):
    """The star on a week-view row: "pin:<project id>" or "unpin:<project id>"."""
    target = get_object_or_404(User, pk=pk)
    if not perms.can_set_main_projects(request.user, target):
        raise PermissionDenied("You can't change this person's main projects.")
    action, _, pid = (request.POST.get("star") or "").partition(":")
    if action not in ("pin", "unpin") or not pid.isdigit():
        raise PermissionDenied("Unknown action.")
    project = get_object_or_404(Project, pk=int(pid))
    try:
        services.set_main_project(request.user, target, project, action == "pin")
        messages.success(request, f"{project.key} {'marked as a main project' if action == 'pin' else 'is no longer a main project'}. "
                                  "Hours are split across main projects from today.")
    except ValueError as exc:
        messages.error(request, str(exc))
    week = request.POST.get("week", "")
    return redirect(reverse("timesheet:week") + _qs(user=target.pk if target.pk != request.user.pk else "", week=week))


# --------------------------------------------------------------------------- people (one row per person)
@login_required
def people_view(request):
    _need_tab(request.user, "people")
    from django.core.paginator import Paginator

    monday = _week(request)
    scope = perms.viewable_people(request.user)
    people = scope
    q = (request.GET.get("q") or "").strip()
    person = request.GET.get("person") or ""
    project_id = request.GET.get("project") or ""
    status = request.GET.get("status") or ""
    if q:
        people = people.filter(Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(username__icontains=q))
    if person.isdigit():
        people = people.filter(pk=int(person))
    project = None
    if project_id.isdigit():
        project = Project.objects.filter(pk=int(project_id)).first()
        if project:
            people = people.filter(memberships__project=project, memberships__side="internal").distinct()
    data = services.people_week(people, monday, project=project)
    if status == "submitted":
        data["rows"] = [r for r in data["rows"] if r["fully"]]
    elif status == "pending":
        data["rows"] = [r for r in data["rows"] if not r["fully"]]
    elif status == "absent":
        data["rows"] = [r for r in data["rows"] if r["absent_days"]]
    page = Paginator(data["rows"], 15).get_page(request.GET.get("page"))
    offset = (page.number - 1) * 15
    keep = _qs(week=monday, q=q, person=person, project=project_id, status=status)
    return render(request, "timesheet/people.html", {
        **_nav(request), "tab": "people", "monday": monday, "data": data, "page": page, "offset": offset,
        "q": q, "person": person, "project_id": project_id, "status": status, "keep": keep,
        "scope": scope, "projects": Project.objects.filter(memberships__user__in=scope, archived=False).distinct().order_by("key"),
        "prev": monday - timedelta(days=7), "next": monday + timedelta(days=7),
        "this_week": rules.week_start(services.today()),
        "last_week": rules.week_start(services.today()) - timedelta(days=7),
        "next_week": rules.week_start(services.today()) + timedelta(days=7),
    })


@login_required
def people_today(request, show):
    _need_tab(request.user, "people")
    """The people behind a Team timesheet card: who is present / absent / on leave today, or has logged hours."""
    if show not in ("present", "working", "absent", "leave"):
        raise PermissionDenied("Unknown list.")
    if not perms.can_see_team(request.user):
        raise PermissionDenied("The team view is for admins, reporting managers and people with the timesheet management view.")
    now_day, start_day = services.today(), services.go_live_date()
    book = services.HolidayBook(now_day, now_day)
    scope = list(perms.viewable_people(request.user).select_related("team", "manager"))
    records = {a.user_id: a for a in Attendance.objects.filter(date=now_day, user__in=scope)}
    splits = {}
    for e in (TimesheetEntry.objects.filter(day__user__in=scope, day__date=now_day, hours__gt=0)
              .select_related("day", "project").order_by("project__key")):
        splits.setdefault(e.day.user_id, []).append((e.project, e.hours))
    rows = []
    for p in scope:
        hours = sum(h for _proj, h in splits.get(p.pk, []))
        state = services.attendance_state(p, now_day, book.for_user(p), now_day, start_day)
        if show == "present" and state != "present":
            continue
        if show == "absent" and state != "absent":
            continue
        if show == "leave" and state != "leave":
            continue
        if show == "working" and not hours:
            continue
        att = records.get(p.pk)
        rows.append({"user": p, "att": att, "hours": hours, "splits": splits.get(p.pk, []), "state": state,
                     "first_login": att.first_login if att and att.first_login else services.activity_first_seen(p, now_day)})
    window_end = services.work_window(now_day)[1]
    return render(request, "timesheet/people_today.html", {
        **_nav(request), "tab": "people", "show": show, "rows": rows, "day": now_day, "total": len(scope),
        "teams": sorted({r["user"].team.name for r in rows if r["user"].team_id}),
        "before_cutoff": services.now_local() < window_end, "cutoff": timezone.localtime(window_end),
    })


# --------------------------------------------------------------------------- calendar
@login_required
def calendar_view(request):
    _need_tab(request.user, "calendar")
    target = _target(request)
    now_day = services.today()
    raw = request.GET.get("month") or f"{now_day:%Y-%m}"
    first = parse_date(raw + "-01") or now_day.replace(day=1)
    last = rules.month_end(first)
    start_day = services.go_live_date()
    grid_days = pycal.Calendar(firstweekday=0).monthdatescalendar(first.year, first.month)
    grid_first, grid_last = grid_days[0][0], grid_days[-1][-1]
    holidays = services.holiday_map(grid_first, grid_last, target)
    totals, splits = {}, {}
    for e in (TimesheetEntry.objects.filter(day__user=target, day__date__gte=grid_first, day__date__lte=grid_last)
              .select_related("day", "project").order_by("project__key")):
        totals[e.day.date] = totals.get(e.day.date, 0) + e.hours
        if e.hours:
            splits.setdefault(e.day.date, []).append((e.project, e.hours))
    statuses = dict(TimesheetDay.objects.filter(user=target, date__gte=first, date__lte=last).values_list("date", "status"))
    leaves = dict(TimesheetDay.objects.filter(user=target, date__gte=grid_first, date__lte=grid_last, leave_hours__gt=0)
                  .values_list("date", "leave_hours"))

    weeks = []
    for week in pycal.Calendar(firstweekday=0).monthdatescalendar(first.year, first.month):
        cells = []
        for d in week:
            cells.append({
                "date": d, "in_month": d.month == first.month, "kind": rules.day_kind(d, holidays),
                "holiday": holidays.get(d), "working_day": holidays.working.get(d), "hours": totals.get(d, 0), "status": statuses.get(d, ""),
                "attendance": services.attendance_state(target, d, holidays, now_day, start_day)
                if d.month == first.month else "",
                "today": d == now_day, "locked": rules.is_locked(d, now_day),
                "splits": splits.get(d, []), "future": d > now_day, "leave": leaves.get(d, 0),
            })
        weeks.append(cells)
    view = request.GET.get("view") if request.GET.get("view") in ("month", "list") else "month"
    week_link = rules.week_start(now_day if first <= now_day <= last else first)
    prev_month = (first - timedelta(days=1)).replace(day=1)
    next_month = last + timedelta(days=1)
    return render(request, "timesheet/calendar.html", {
        **_nav(request), "tab": "calendar", "target": target, "first": first, "weeks": weeks,
        "prev": prev_month, "next": next_month, "people": perms.viewable_people(request.user),
        "month_total": sum(h for d, h in totals.items() if first <= d <= last), "keep": {"month": f"{first:%Y-%m}", "view": view},
        "view": view, "week_link": week_link, "this_month": now_day.replace(day=1),
        "days_list": [c for w in weeks for c in w if c["in_month"]],
    })


# --------------------------------------------------------------------------- holidays
def _posted_teams(request):
    """The teams ticked under "Applies to"; none ticked = everyone."""
    from apps.accounts.models import Team

    ids = [int(i) for i in request.POST.getlist("teams") if i.isdigit()]
    return list(Team.objects.filter(pk__in=ids))


@login_required
def holidays(request):
    _need_tab(request.user, "holidays")
    from apps.accounts.models import Team

    now_day = services.today()
    year = int(request.GET.get("year") or now_day.year)
    team_id = request.GET.get("team") or ""
    can_manage = perms.can_manage_holidays(request.user)
    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied("You need the \"Manage holidays & working days\" permission to change holidays.")
        day = parse_date(request.POST.get("date") or "")
        name = (request.POST.get("name") or "").strip()
        if not day or not name:
            messages.error(request, "Enter both a date and a name.")
        else:
            try:
                holiday = services.save_holiday(request.user, day, name, teams=_posted_teams(request))
                messages.success(request, f"Holiday added: {name} on {day:%d %b %Y} for {holiday.applies_to}.")
            except ValueError as exc:
                messages.error(request, str(exc))
        return redirect(reverse("timesheet:holidays") + _qs(year=year))

    qs = Holiday.objects.filter(date__year=year) if request.GET.get("year") else Holiday.objects.filter(
        date__year__gte=now_day.year)
    qs = list(qs.prefetch_related("teams"))
    wqs = (WorkingDay.objects.filter(date__year=year) if request.GET.get("year") else WorkingDay.objects.filter(
        date__year__gte=now_day.year))
    wqs = list(wqs)
    teams = list(Team.objects.order_by("name"))
    links = list(services.holiday_links())
    following = {link.team_id: link.follows for link in links}
    if team_id.isdigit():
        book = services.HolidayBook(date.min, date.max)
        qs = [h for h in qs if book.applies(h, int(team_id))]
    for h in qs:
        h.team_ids = {t.pk for t in h.teams.all()}
    return render(request, "timesheet/holidays.html", {
        **_nav(request), "tab": "holidays", "year": year, "can_manage": can_manage, "holidays": qs,
        "working_days": wqs,
        "today": now_day, "team_id": team_id, "teams": teams, "links": links,
        # teams that follow another team get their holidays from it, so they are not offered as a target
        "pickable_teams": [t for t in teams if t.pk not in following], "following": following,
    })


@login_required
@require_POST
def holiday_edit(request, pk):
    if not perms.can_manage_holidays(request.user):
        raise PermissionDenied("You need the \"Manage holidays & working days\" permission to change holidays.")
    holiday = get_object_or_404(Holiday, pk=pk)
    day = parse_date(request.POST.get("date") or "")
    name = (request.POST.get("name") or "").strip()
    if not day or not name:
        messages.error(request, "Enter both a date and a name.")
    else:
        try:
            services.save_holiday(request.user, day, name, holiday=holiday, teams=_posted_teams(request))
            messages.success(request, "Holiday updated.")
        except ValueError as exc:
            messages.error(request, str(exc))
    return redirect(reverse("timesheet:holidays") + _qs(year=(day or holiday.date).year))


@login_required
@require_POST
def working_day_add(request):
    """Open a Saturday/Sunday (normally off) as a working day for everyone."""
    if not perms.can_manage_holidays(request.user):
        raise PermissionDenied("You need the \"Manage holidays & working days\" permission to change working days.")
    day = parse_date(request.POST.get("date") or "")
    name = (request.POST.get("name") or "").strip()
    if not day or not name:
        messages.error(request, "Enter both a date and a name.")
    else:
        try:
            services.save_working_day(request.user, day, name)
            messages.success(request, f"Working day added: {name} on {day:%a %d %b %Y} for everyone.")
        except ValueError as exc:
            messages.error(request, str(exc))
    return redirect(reverse("timesheet:holidays") + _qs(year=(day or services.today()).year))


@login_required
@require_POST
def working_day_edit(request, pk):
    if not perms.can_manage_holidays(request.user):
        raise PermissionDenied("You need the \"Manage holidays & working days\" permission to change working days.")
    working_day = get_object_or_404(WorkingDay, pk=pk)
    day = parse_date(request.POST.get("date") or "")
    name = (request.POST.get("name") or "").strip()
    if not day or not name:
        messages.error(request, "Enter both a date and a name.")
    else:
        try:
            services.save_working_day(request.user, day, name, working_day=working_day)
            messages.success(request, "Working day updated.")
        except ValueError as exc:
            messages.error(request, str(exc))
    return redirect(reverse("timesheet:holidays") + _qs(year=(day or working_day.date).year))


@login_required
@require_POST
def working_day_delete(request, pk):
    if not perms.can_manage_holidays(request.user):
        raise PermissionDenied("You need the \"Manage holidays & working days\" permission to change working days.")
    working_day = get_object_or_404(WorkingDay, pk=pk)
    year = working_day.date.year
    services.delete_working_day(request.user, working_day)
    messages.success(request, "Working day deleted; that date follows the normal weekend rule again.")
    return redirect(reverse("timesheet:holidays") + _qs(year=year))


@login_required
@require_POST
def holiday_link(request):
    """Set or remove "team X follows team Y's holidays"."""
    from apps.accounts.models import Team

    if not perms.can_manage_holidays(request.user):
        raise PermissionDenied("You need the \"Manage holidays & working days\" permission to change holidays.")
    team = get_object_or_404(Team, pk=request.POST.get("team"))
    follows_id = request.POST.get("follows") or ""
    follows = get_object_or_404(Team, pk=follows_id) if follows_id else None
    try:
        services.set_team_holiday_link(request.user, team, follows)
        messages.success(request, f"{team.name} now follows {follows.name}'s holidays." if follows
                         else f"{team.name} now uses its own holidays.")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect(reverse("timesheet:holidays"))


@login_required
@require_POST
def holiday_delete(request, pk):
    if not perms.can_manage_holidays(request.user):
        raise PermissionDenied("You need the \"Manage holidays & working days\" permission to change holidays.")
    holiday = get_object_or_404(Holiday, pk=pk)
    year = holiday.date.year
    services.delete_holiday(request.user, holiday)
    messages.success(request, "Holiday deleted; that date follows normal-day rules again.")
    return redirect(reverse("timesheet:holidays") + _qs(year=year))


# --------------------------------------------------------------------------- attendance
@login_required
def attendance(request):
    _need_tab(request.user, "attendance")
    if not perms.can_see_team(request.user):
        raise PermissionDenied("Attendance is for admins, reporting managers and people with the timesheet management view.")
    now_day = services.today()
    day = parse_date(request.GET.get("date") or request.POST.get("date") or "") or now_day
    if request.method == "POST":
        target = get_object_or_404(User, pk=request.POST.get("user"))
        status = request.POST.get("status")
        if status not in ("present", "absent", "leave"):
            messages.error(request, "Choose Present, Absent or Leave.")
        else:
            try:
                services.override_attendance(request.user, target, day, status, request.POST.get("reason", "").strip())
                messages.success(request, f"{target.display_name} marked {status} on {day:%d %b %Y}.")
            except ValueError as exc:
                messages.error(request, str(exc))
        return redirect(reverse("timesheet:attendance") + _qs(date=day))

    people = perms.viewable_people(request.user)
    if not perms.sees_everyone(request.user):
        people = people.exclude(pk=request.user.pk)
    book = services.HolidayBook(day, day)
    start_day = services.go_live_date()
    records = {a.user_id: a for a in Attendance.objects.filter(date=day, user__in=people).select_related("override_by")}
    rows = [{
        "user": p, "att": records.get(p.pk),
        "first_login": (records[p.pk].first_login if p.pk in records and records[p.pk].first_login
                        else services.activity_first_seen(p, day)),
        "state": services.attendance_state(p, day, book.for_user(p), now_day, start_day),
        "can_override": perms.can_override_attendance(request.user, p),
    } for p in people]
    counts = {k: sum(1 for r in rows if r["state"] == k) for k in ("present", "absent", "leave", "pending")}
    kind, holiday, holiday_label = book.header(day, people)
    return render(request, "timesheet/attendance.html", {
        **_nav(request), "tab": "attendance", "day": day, "rows": rows, "counts": counts,
        "kind": kind, "holiday": holiday, "holiday_label": holiday_label,
        "prev": day - timedelta(days=1), "next": day + timedelta(days=1) if day < now_day else None,
    })


# --------------------------------------------------------------------------- reports
@login_required
def reports(request):
    _need_tab(request.user, "reports")
    monday = _week(request)
    full_ids, lead_projects = services.report_scope(request.user)
    user_filter = request.GET.get("user") or None
    project_filter = request.GET.get("project") or None
    if user_filter and full_ids is not None and int(user_filter) not in full_ids and not lead_projects:
        raise PermissionDenied("You can't view this person's hours.")
    data = services.weekly_report(request.user, monday, user_filter=user_filter, project_filter=project_filter)

    if full_ids is None:
        people = services.timesheet_people().order_by("first_name", "username")
        projects = Project.objects.filter(archived=False).order_by("key")
    else:
        people = User.objects.filter(pk__in=full_ids).order_by("first_name", "username")
        projects = Project.objects.filter(
            Q(pk__in=lead_projects) | Q(memberships__user_id__in=full_ids)
        ).distinct().order_by("key")
    return render(request, "timesheet/reports.html", {
        **_nav(request), "tab": "reports", "r": data, "people": people, "projects": projects,
        "user_filter": user_filter or "", "project_filter": project_filter or "",
        "prev": monday - timedelta(days=7), "next": monday + timedelta(days=7),
        "is_lead_only": full_ids is not None and bool(lead_projects),
        "dl_from": services.today().replace(day=1), "dl_to": services.today(),
    })


# --------------------------------------------------------------------------- attendance + timesheet download
@login_required
def attendance_export_xlsx(request):
    _need_tab(request.user, "attendance")
    """Excel with attendance and filled-in hours for a date range. Everyone may download, but only for
    the people they can already open (themselves; admins everyone; reporting managers their team)."""
    back = reverse("timesheet:reports")
    try:
        start, end = attendance_export.clean_range(
            parse_date(request.GET.get("from") or ""), parse_date(request.GET.get("to") or ""))
        data = attendance_export.build(request.user, start, end, user_filter=request.GET.get("user") or None)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect(back)
    except PermissionError as exc:
        raise PermissionDenied(str(exc))
    return attendance_export.to_xlsx(data)
