"""Timesheet business logic: attendance, auto-fill, saving, holidays, reports and scheduled jobs.

Everything is computed in the configured TIME_ZONE (settings.TIME_ZONE) via timezone.localdate().
Other apps are only read here; all writes go to the timesheet tables.
"""
import json
import logging
from collections import OrderedDict, defaultdict
from datetime import datetime, time, timedelta

from django.db import transaction
from django.db.models import Min
from django.utils import timezone

from apps.accounts.models import User
from apps.projects.models import Project, ProjectMembership

from . import permissions as perms
from . import rules
from .models import (
    Attendance, Holiday, JobRun, ProjectStatusLog, TimesheetAudit, TimesheetDay, TimesheetEntry, TimesheetMainProject,
    WorkingDay,
    TimesheetProfile,
)

logger = logging.getLogger(__name__)

COMPLETED = "completed"
START_KEY = "timesheet:start"


# --------------------------------------------------------------------------- basics
def today():
    return timezone.localdate()


def now_local():
    return timezone.localtime()


def _hhmm(value, default):
    try:
        h, m = (int(x) for x in str(value).split(":"))
        return time(h, m)
    except (TypeError, ValueError):
        return default


def work_window(day):
    """The attendance window for a date: 00:00–18:30 local time by default (settings.TIMESHEET_WORK_START/END).
    Logging in or using Planwise any time that day up to 18:30 makes a workday Present; nothing = Absent."""
    from django.conf import settings

    start = _hhmm(getattr(settings, "TIMESHEET_WORK_START", "00:00"), time(0, 0))
    end = _hhmm(getattr(settings, "TIMESHEET_WORK_END", "18:30"), time(18, 30))
    tz = timezone.get_current_timezone()
    return (timezone.make_aware(datetime.combine(day, start), tz), timezone.make_aware(datetime.combine(day, end), tz))


def local_date(dt):
    return timezone.localtime(dt).date()


def day_bounds(day):
    start = timezone.make_aware(datetime.combine(day, time.min))
    return start, start + timedelta(days=1)


def go_live_date():
    """The first day the timesheet ran. Earlier days are never auto-filled or marked absent,
    since there is no attendance record for them."""
    run, _ = JobRun.objects.get_or_create(key=START_KEY, defaults={"detail": "timesheet go-live"})
    return local_date(run.ran_at)


def timesheet_people():
    """Everyone who keeps a timesheet: active staff accounts (client-side logins excluded)."""
    return perms.staff_accounts()


def profile_for(user):
    profile, _ = TimesheetProfile.objects.get_or_create(
        user=user, defaults={"joining_date": local_date(user.date_joined) if user.date_joined else None}
    )
    return profile


class HolidayBook:
    """The holidays and extra working days of a date range, loaded once. Holidays can be limited to teams, so each
    person has their own map: `for_user(user)` -> rules.HolidayDays, {date: Holiday} with `.working` =
    {date: WorkingDay} (working days are for everyone).

    A holiday for a team also covers its sub-teams and any team that follows it (TeamHolidayLink)."""

    def __init__(self, start, end):
        from apps.accounts.models import Team

        from .models import TeamHolidayLink

        self.holidays = list(Holiday.objects.filter(date__gte=start, date__lte=end).prefetch_related("teams"))
        self._teams = {h.pk: {t.pk for t in h.teams.all()} for h in self.holidays}
        self.working_days = list(WorkingDay.objects.filter(date__gte=start, date__lte=end))
        self._parent = dict(Team.objects.values_list("pk", "parent_id"))
        self._follows = dict(TeamHolidayLink.objects.values_list("team_id", "follows_id"))
        self._maps = {}

    def team_reach(self, team_id):
        """The team, its parent teams, and the teams it follows (and theirs): whose holidays it gets."""
        reach, frontier = set(), [team_id] if team_id else []
        while frontier:
            tid = frontier.pop()
            if tid in reach:
                continue
            reach.add(tid)
            frontier += [t for t in (self._parent.get(tid), self._follows.get(tid)) if t]
        return reach

    def applies(self, holiday, team_id):
        teams = self._teams[holiday.pk]
        return not teams or bool(teams & self.team_reach(team_id))

    def for_team(self, team_id):
        if team_id not in self._maps:
            found = rules.HolidayDays()
            for h in self.holidays:
                if h.date not in found and self.applies(h, team_id):
                    found[h.date] = h
            found.working = {}
            for w in self.working_days:
                found.working.setdefault(w.date, w)
            self._maps[team_id] = found
        return self._maps[team_id]

    def for_user(self, user):
        return self.for_team(user.team_id)

    def on(self, day):
        """Every holiday on a date, whoever it is for (for labels on shared column headers)."""
        return [h for h in self.holidays if h.date == day]

    def works_on(self, day, people):
        """True when an extra working day opens this date for at least one of the people shown."""
        maps = [self.for_user(p) for p in people] or [self.for_team(None)]
        return any(day in m.working for m in maps)

    def header(self, day, people):
        """-> (kind, holiday, label) for a date column showing several people: the column counts as a
        holiday only when it is one for every person shown, and as a workday when it is one for anyone
        shown (normal workday, or an extra working day); `label` names every holiday that day."""
        maps = [self.for_user(p) for p in people] or [self.for_team(None)]  # nobody shown: what applies to all
        kinds = [rules.day_kind(day, m) for m in maps]
        if all(k == rules.HOLIDAY for k in kinds):
            kind = rules.HOLIDAY
        elif any(k == rules.WORKDAY for k in kinds):
            kind = rules.WORKDAY
        else:
            kind = rules.WEEKEND
        todays = self.on(day)
        return kind, (todays[0] if todays else None), " · ".join(h.label for h in todays)


def holiday_map(start, end, user):
    """{date: Holiday} of the holidays that apply to this person."""
    return HolidayBook(start, end).for_user(user)


def audit(actor, entity, ref="", old="", new="", target=None, after_lock=False):
    try:
        TimesheetAudit.objects.create(
            actor=actor if getattr(actor, "is_authenticated", False) else None,
            target_user=target, entity=entity, ref=str(ref)[:200],
            old_value=str(old)[:255], new_value=str(new)[:255], after_lock=after_lock,
        )
    except Exception:  # noqa: BLE001 - never let the audit trail break the action itself
        logger.exception("timesheet audit write failed")


# --------------------------------------------------------------------------- project eligibility
class StatusHistory:
    """Answers "was this project Completed on that day?" from ProjectStatusLog."""

    def __init__(self, project_ids):
        self.current = dict(Project.objects.filter(pk__in=project_ids).values_list("pk", "status"))
        self.logs = defaultdict(list)
        for log in ProjectStatusLog.objects.filter(project_id__in=project_ids).order_by("changed_at", "pk"):
            self.logs[log.project_id].append((local_date(log.changed_at), log.old_status, log.new_status))

    def _status_at_start(self, pid, day):
        status = None
        for changed_on, old, new in self.logs.get(pid, []):
            if changed_on < day:
                status = new
            else:
                return status if status is not None else old
        return status if status is not None else self.current.get(pid)

    def _status_at_end(self, pid, day):
        status = None
        for changed_on, old, new in self.logs.get(pid, []):
            if changed_on <= day:
                status = new
            else:
                return status if status is not None else old
        return status if status is not None else self.current.get(pid)

    def open_on(self, pid, day):
        """Hours count on a day unless the project was Completed for the whole of it: the
        completion day itself still counts, and a reactivation counts from its own day."""
        return self._status_at_start(pid, day) != COMPLETED or self._status_at_end(pid, day) != COMPLETED


def hours_allowed_on(viewer, kind):
    """Workdays take hours from everyone; weekends only from an admin (Administrator / Devadmin); holidays never."""
    return kind == rules.WORKDAY or (kind == rules.WEEKEND and perms.is_admin(viewer))


class PersonContext:
    """Everything needed to compute one person's auto-fill over a date range, loaded once."""

    def __init__(self, user):
        self.user = user
        self.profile = profile_for(user)
        self.memberships = list(
            ProjectMembership.objects.filter(user=user, side="internal", project__archived=False)
            .select_related("project").order_by("joined_at", "pk")
        )
        self.projects = {m.project_id: m.project for m in self.memberships}
        self.history = StatusHistory(list(self.projects))
        self.main_ids = set(TimesheetMainProject.objects.filter(user=user).values_list("project_id", flat=True))
        from .models import PastAssignment

        current = set(self.projects)
        self.past = [a for a in PastAssignment.objects.filter(user=user, project__archived=False).select_related("project")
                     if a.project_id not in current]

    def active_on(self, day):
        p = self.profile
        if p.joining_date and day < p.joining_date:
            return False
        if p.last_working_day and day > p.last_working_day:
            return False
        return True

    def eligible_projects(self, day):
        """Assigned on that day (from the assignment date on) and not Completed then; in assignment order."""
        return [
            m.project for m in self.memberships
            if local_date(m.joined_at) <= day and self.history.open_on(m.project_id, day)
        ]

    def editable_projects(self, day, any_month=False):
        """Projects hours may be typed against on that day. The month a project was assigned in counts
        in full, so one assigned on the 10th can be edited from the 1st; earlier months stay closed.
        any_month (an admin typing) lifts that: every project the person is on can take hours in any month,
        so work done before the project was added can still be recorded.
        Automatic splitting still starts on the assignment date (eligible_projects)."""
        out = [
            m.project for m in self.memberships
            if (any_month or local_date(m.joined_at).replace(day=1) <= day) and self.history.open_on(m.project_id, day)
        ]
        # projects the person has since left stay fully editable for the days they were on them
        seen = {p.pk for p in out}
        for a in self.past:
            if a.project_id not in seen and a.started_on.replace(day=1) <= day <= a.ended_on \
                    and self.open_on(a.project_id, day):
                out.append(a.project)
                seen.add(a.project_id)
        return out

    def split_projects(self, day):
        """The projects that share the 8 hours that day: the person's main (starred) projects that are
        eligible, or every eligible project when none of their main projects is."""
        eligible = self.eligible_projects(day)
        main = [p for p in eligible if p.pk in self.main_ids]
        return main or eligible

    def open_on(self, project_id, day):
        return self.history.open_on(project_id, day) if project_id in self.projects else self._open_other(project_id, day)

    def _open_other(self, project_id, day):
        return StatusHistory([project_id]).open_on(project_id, day)


# --------------------------------------------------------------------------- attendance
def activity_first_seen(user, day):
    """Earliest page this person opened that day (Planwise's existing page-visit log), or None."""
    from apps.accounts.models import PageVisit

    start, end = day_bounds(day)
    return PageVisit.objects.filter(user=user, visited_at__gte=start, visited_at__lt=end).aggregate(t=Min("visited_at"))["t"]


def record_login(user, when=None):
    """Stores the first login time. A login inside the work window marks the day Present at once;
    one outside it does not (the day can still become Present from use inside the window)."""
    when = when or timezone.now()
    day = local_date(when)
    start, end = work_window(day)
    in_window = start <= when < end
    att, created = Attendance.objects.get_or_create(
        user=user, date=day, defaults={"status": "present" if in_window else "absent", "first_login": when}
    )
    if not created:
        changed = []
        if att.first_login is None or when < att.first_login:
            att.first_login = when
            changed.append("first_login")
        if in_window and not att.overridden and att.status != "present":
            att.status = "present"
            changed.append("status")
        if changed:
            att.save(update_fields=changed + ["updated_at"])
    return att


def record_logout(user, when=None):
    when = when or timezone.now()
    day = local_date(when)
    att, _ = Attendance.objects.get_or_create(user=user, date=day, defaults={"status": "present", "first_login": when})
    if att.last_logout is None or when > att.last_logout:
        att.last_logout = when
        att.save(update_fields=["last_logout", "updated_at"])
    return att


def seen_in_window(user, day, att=None):
    """Logged in, or opened any Planwise page, between the work window's start and end that day."""
    from apps.accounts.models import PageVisit

    start, end = work_window(day)
    if att and att.first_login and start <= att.first_login < end:
        return True
    return PageVisit.objects.filter(user=user, visited_at__gte=start, visited_at__lt=end).exists()


def on_full_leave(user, day):
    return TimesheetDay.objects.filter(user=user, date=day, leave_hours__gte=rules.DAILY_HOURS).exists()


def attendance_state(user, day, holidays, now_day, start_day, finalize=False):
    """-> 'holiday' | 'weekend' | 'leave' | 'present' | 'absent' | 'not_tracked' (before go-live / outside employment).
    Today, someone who has not logged in yet is Absent; they turn Present as soon as they are seen in the window.
    A full day of leave (8 hours in the Leave row) is 'leave': attendance is not checked.

    A workday is Present when the person logged in or used Planwise inside the work window
    (default 00:00–18:30, i.e. any login that day before 18:30); otherwise it is Absent once the window has closed. An override by an
    admin or reporting manager always wins. finalize=True writes the result down."""
    kind = rules.day_kind(day, holidays)
    if kind != rules.WORKDAY:
        return kind  # holiday wins over absent; weekends are not checked
    if on_full_leave(user, day):
        return "leave"
    att = Attendance.objects.filter(user=user, date=day).first()
    if att and att.overridden:
        return att.status
    if day < start_day or day > now_day:
        return att.status if att else "not_tracked"
    present = seen_in_window(user, day, att)
    if not present and day == now_day and now_local() < work_window(day)[1]:
        return "absent"  # not logged in yet today: Absent until they log in (becomes Present at once)
    status = "present" if present else "absent"
    if finalize or (att and att.status != status):
        if att is None:
            Attendance.objects.create(user=user, date=day, status=status, first_login=activity_first_seen(user, day))
        elif att.status != status:
            att.status = status
            att.save(update_fields=["status", "updated_at"])
    return status


# --------------------------------------------------------------------------- auto-fill
def auto_projects(ctx, day, holidays, now_day, start_day, leave=0, finalize=False):
    """-> (projects that share the day's hours, reason). reason: ok | no_projects | off_day | leave |
    absent | pending | not_tracked. Weekends and holidays get no hours (type them if you worked);
    workdays need attendance, so only from go-live up to today."""
    if not ctx.active_on(day):
        return [], "not_tracked"
    if rules.day_kind(day, holidays) != rules.WORKDAY:
        return [], "off_day"
    if day > now_day or day < start_day:
        return [], "not_tracked"
    if leave >= rules.DAILY_HOURS:
        return [], "leave"
    state = attendance_state(ctx.user, day, holidays, now_day, start_day, finalize=finalize)
    if state in ("absent", "pending", "not_tracked", "leave"):
        return [], state
    projects = ctx.split_projects(day)
    return (projects, "ok") if projects else ([], "no_projects")


def auto_target(ctx, day, holidays, now_day, start_day, finalize=False, leave=None):
    """-> ({project_id: hours}, reason): the 8-hour day minus any leave, split across the projects."""
    if leave is None:
        leave = TimesheetDay.objects.filter(user=ctx.user, date=day).values_list("leave_hours", flat=True).first() or 0
    projects, reason = auto_projects(ctx, day, holidays, now_day, start_day, leave=leave, finalize=finalize)
    if not projects:
        return {}, reason
    return rules.split_hours([p.pk for p in projects], ctx.profile.remainder_project_id,
                             total=rules.DAILY_HOURS - leave), "ok"


def ensure_day(ctx, day, holidays, now_day, start_day, force=False, finalize=False):
    """Create or refresh one auto-filled day. A day a person edited or submitted is never touched,
    locked months are never touched, and a finished past day is only recomputed when forced
    (holiday added/removed, attendance overridden)."""
    ts = TimesheetDay.objects.filter(user=ctx.user, date=day).first()
    if ts and (ts.edited or ts.status == "submitted"):
        return ts
    if rules.is_locked(day, now_day):
        return ts
    if ts and ts.auto_filled and day < now_day and not force:
        return ts
    target, _reason = auto_target(ctx, day, holidays, now_day, start_day, finalize=finalize,
                                  leave=ts.leave_hours if ts else 0)
    with transaction.atomic():
        if not target:
            if ts and ts.leave_hours:
                ts.entries.all().delete()  # keep the day: it holds the leave
                return ts
            if ts:
                ts.delete()  # untouched auto day that no longer qualifies (e.g. holiday added, absent, off day)
            return None
        if ts is None:
            ts = TimesheetDay.objects.create(user=ctx.user, date=day, auto_filled=True)
        current = {e.project_id: e for e in ts.entries.all()}
        for pid, hours in target.items():
            entry = current.pop(pid, None)
            if entry is None:
                TimesheetEntry.objects.create(day=ts, project_id=pid, hours=hours)
            elif entry.hours != hours:
                entry.hours = hours
                entry.save(update_fields=["hours"])
        for entry in current.values():
            entry.delete()
        if not ts.auto_filled:
            ts.auto_filled = True
            ts.save(update_fields=["auto_filled", "updated_at"])
    return ts


def ensure_range(user, days, force=False):
    now_day, start_day = today(), go_live_date()
    ctx = PersonContext(user)
    holidays = holiday_map(min(days), max(days), user)
    for day in days:
        try:
            ensure_day(ctx, day, holidays, now_day, start_day, force=force)
        except Exception:  # noqa: BLE001 - one bad day must not break the page
            logger.exception("timesheet auto-fill failed for %s on %s", user.pk, day)
    return ctx


def refresh_date_for_everyone(day):
    """After a holiday is added or removed: recompute that date for every untouched timesheet."""
    now_day = today()
    if day > now_day or rules.is_locked(day, now_day):
        return 0
    start_day = go_live_date()
    book = HolidayBook(day, day)
    count = 0
    for user in timesheet_people():
        try:
            ensure_day(PersonContext(user), day, book.for_user(user), now_day, start_day, force=True)
            count += 1
        except Exception:  # noqa: BLE001
            logger.exception("holiday refresh failed for %s on %s", user.pk, day)
    return count


def clear_off_day_autofill(dry_run=False):
    """One-off after the "off days get 0 hours" rule: delete auto-filled weekend/holiday days nobody
    edited or submitted, in months that are not locked yet. -> list of (username, date) removed."""
    now_day = today()
    days = (TimesheetDay.objects.filter(auto_filled=True, edited=False, status="draft", leave_hours=0,
                                        date__gte=now_day.replace(day=1))
            .select_related("user").order_by("date"))
    days = [d for d in days if not rules.is_locked(d.date, now_day)]
    if not days:
        return []
    book = HolidayBook(days[0].date, days[-1].date)
    removed = []
    for ts in days:
        if rules.day_kind(ts.date, book.for_user(ts.user)) != rules.WORKDAY:
            removed.append((ts.user.username, ts.date))
            if not dry_run:
                ts.delete()
    return removed


# --------------------------------------------------------------------------- week grid
def week_grid(viewer, target, monday):
    """Everything the timesheet page needs for one person and one week."""
    days = rules.week_days(monday)
    ctx = ensure_range(target, days)
    now_day, start_day = today(), go_live_date()
    holidays = holiday_map(days[0], days[-1], target)
    ts_days = {d.date: d for d in TimesheetDay.objects.filter(user=target, date__in=days).prefetch_related("entries")}
    entries = {(d.date, e.project_id): e.hours for d in ts_days.values() for e in d.entries.all()}

    admin = perms.is_admin(viewer)
    # rows: projects eligible on any day of the week, plus any project that already has hours
    project_ids = []
    for day in days:
        project_ids += [p.pk for p in ctx.editable_projects(day, any_month=admin) if ctx.active_on(day)]
    project_ids += [pid for (_d, pid) in entries]
    project_ids = list(dict.fromkeys(project_ids))
    projects = {p.pk: p for p in Project.objects.filter(pk__in=project_ids)}
    attendance = {a.date: a for a in Attendance.objects.filter(user=target, date__in=days)}

    columns = []
    for day in days:
        kind = rules.day_kind(day, holidays)
        eligible = {p.pk for p in ctx.editable_projects(day, any_month=admin)} if ctx.active_on(day) else set()
        # off days (holidays, Sundays, Saturdays other than the 1st) are read-only, except weekends for admins
        editable = perms.can_edit_day(viewer, target, day, now_day) and hours_allowed_on(viewer, kind)
        ts = ts_days.get(day)
        state = attendance_state(target, day, holidays, now_day, start_day)
        leave = ts.leave_hours if ts else 0
        # untouched auto days re-split their project hours around the leave; the page previews it
        auto = (editable and not rules.is_locked(day, now_day) and not (ts and (ts.edited or ts.status == "submitted")))
        splits = None
        if auto and kind == rules.WORKDAY and ctx.active_on(day):
            sharing, _reason = auto_projects(ctx, day, holidays, now_day, start_day)
            ids = [p.pk for p in sharing]
            splits = {h: rules.split_hours(ids, ctx.profile.remainder_project_id, total=rules.DAILY_HOURS - h)
                      if h < rules.DAILY_HOURS else {} for h in range(rules.DAILY_HOURS + 1)} if ids else {}
        # no login that day (Absent): hours stay 0 and cannot be added, except by an admin. Hours already there
        # can still be lowered or cleared, and a full day of leave turns the day into Leave instead.
        no_login = state == "absent" and not admin
        columns.append({
            "no_login": no_login,
            "leave": leave, "leave_input": editable and kind == rules.WORKDAY and ctx.active_on(day),
            "splits": json.dumps(splits) if splits is not None else "",
            "date": day, "kind": kind, "holiday": holidays.get(day), "working_day": holidays.working.get(day),
            "locked": rules.is_locked(day, now_day), "editable": editable,
            "eligible": eligible, "status": ts.status if ts else "",
            "attendance": state, "att": attendance.get(day),
            "total": sum(entries.get((day, pid), 0) for pid in project_ids) + leave,
            "is_today": day == now_day, "future": day > now_day,
            "updated_at": ts.updated_at if ts else None,
        })

    open_now = {p.pk for p in ctx.eligible_projects(now_day)}
    rows = []
    for pid in project_ids:
        project = projects.get(pid)
        if project is None:
            continue
        cells = []
        for col in columns:
            day = col["date"]
            allowed = pid in col["eligible"]
            cells.append({
                "date": day, "kind": col["kind"], "hours": entries.get((day, pid), 0), "has": (day, pid) in entries,
                "allowed": allowed,
                # hours left on a project the person is no longer open for: editable, but only downwards
                "reduce_only": col["editable"] and (not allowed or col["no_login"]) and entries.get((day, pid), 0) > 0,
                "no_login": col["no_login"],
                "input": col["editable"] and ((allowed and not col["no_login"]) or entries.get((day, pid), 0) > 0),
            })
        rows.append({"project": project, "cells": cells, "total": sum(c["hours"] for c in cells),
                     "completed": project.status == COMPLETED, "main": pid in ctx.main_ids,
                     "can_star": pid in open_now})
    rows.sort(key=lambda r: not r["main"])  # main projects first, otherwise assignment order

    no_projects = not any(col["eligible"] for col in columns)
    past = [c for c in columns if not c["future"]]
    statuses = {c["status"] for c in columns if c["status"]}
    week_status = ("submitted" if statuses == {"submitted"} else "partial" if "submitted" in statuses
                   else "draft" if statuses else "empty")
    if all(c["locked"] for c in columns):
        week_status = "locked"
    return {
        "present_days": sum(1 for c in past if c["attendance"] == "present"),
        "absent_days": sum(1 for c in past if c["attendance"] == "absent"),
        "submitted_days": sum(1 for c in columns if c["status"] == "submitted"),
        "week_status": week_status,
        "expected": rules.DAILY_HOURS * len([c for c in columns if c["eligible"] and c["kind"] == rules.WORKDAY]),
        "leave_total": sum(c["leave"] for c in columns),
        "leave_editable": any(c["leave_input"] for c in columns),
        "days": columns, "rows": rows, "week_total": sum(c["total"] for c in columns), "today": now_day,
        "no_projects": no_projects, "profile": ctx.profile,
        "eligible_now": ctx.eligible_projects(min(max(now_day, days[0]), days[-1])),
        "main_count": len(ctx.main_ids & open_now), "main_max": rules.DAILY_HOURS,
        "open_count": len(open_now),
        "too_many": len(open_now) > rules.DAILY_HOURS and not (ctx.main_ids & open_now),
    }


@transaction.atomic
def save_week(actor, target, monday, raw, submit=False, loaded_at=None, leave_raw=None):
    """raw: {(date, project_id): "value"}; leave_raw: {date: "value"} from the Leave row.
    Validates every day first; saves nothing on error. -> (errors, warnings).
    Last save wins; a concurrent change by someone else is reported.

    Leave counts toward the 8-hour day. On a day nobody has edited yet, changing only the leave (the
    page previews the new split) keeps the day automatic: its project hours are re-split around the leave."""
    days = rules.week_days(monday)
    now_day, start_day = today(), go_live_date()
    ctx = PersonContext(target)
    holidays = holiday_map(days[0], days[-1], target)
    leave_raw = leave_raw or {}
    errors, warnings = [], []
    ts_days = {d.date: d for d in TimesheetDay.objects.select_for_update().filter(user=target, date__in=days)}
    existing = {(d.date, e.project_id): e for d in ts_days.values() for e in d.entries.all()}
    names = dict(Project.objects.filter(pk__in={pid for _d, pid in raw}).values_list("pk", "key"))

    plan, auto_leave = {}, {}
    for day in days:
        cells = {pid: val for (d, pid), val in raw.items() if d == day}
        if not cells and day not in leave_raw:
            continue
        ts = ts_days.get(day)
        old_leave = ts.leave_hours if ts else 0
        editable = perms.can_edit_day(actor, target, day, now_day)
        pids = list(cells)
        # checked separately: the day total (projects + leave) is checked below, once we know whether
        # the project hours are being re-split around the leave
        values, error = rules.validate_day_hours([cells[pid] for pid in pids])
        leave_value, leave_error = rules.validate_day_hours([leave_raw.get(day, old_leave)])
        if error or leave_error:
            errors.append(f"{day:%a %d %b}: {error or leave_error}")
            continue
        new, new_leave = dict(zip(pids, values)), leave_value[0]
        leave_changed = new_leave != old_leave
        changed = {pid: h for pid, h in new.items()
                   if h != (existing[(day, pid)].hours if (day, pid) in existing else 0)}
        if not changed and not leave_changed:
            continue
        kind = rules.day_kind(day, holidays)
        if kind == rules.HOLIDAY:
            errors.append(f"{day:%a %d %b} is a holiday ({holidays[day].name}); no hours can be entered.")
            continue
        if kind == rules.WEEKEND and not hours_allowed_on(actor, kind):
            errors.append(f"{day:%a %d %b} is a weekend; hours can only be entered on a working day (the 1st Saturday "
                          "of the month, or a day an admin opened as a working day).")
            continue
        if not editable:
            errors.append(f"{day:%a %d %b} is locked; only an admin or the reporting manager can change it.")
            continue
        raised = [pid for pid, h in changed.items() if h > (existing[(day, pid)].hours if (day, pid) in existing else 0)]
        if (raised and new_leave < rules.DAILY_HOURS and not perms.is_admin(actor)
                and attendance_state(target, day, holidays, now_day, start_day) == "absent"):
            errors.append(f"{day:%a %d %b}: no login that day, so no hours can be added. Ask an admin or the reporting "
                          "manager to mark the day Present first.")
            continue
        untouched = not (ts and (ts.edited or ts.status == "submitted")) and not rules.is_locked(day, now_day)
        if leave_changed and untouched:
            expected, _reason = auto_target(ctx, day, holidays, now_day, start_day, leave=new_leave)
            if not changed or all(new.get(pid, 0) == expected.get(pid, 0) for pid in new):
                auto_leave[day] = new_leave  # still automatic: re-split around the leave
                continue
        eligible = {p.pk for p in ctx.editable_projects(day, any_month=perms.is_admin(actor))} \
            if ctx.active_on(day) else set()
        # a project the person is not open for that day may only go DOWN (old hours can be corrected or cleared)
        bad = [pid for pid, h in changed.items()
               if h and pid not in eligible and h > (existing[(day, pid)].hours if (day, pid) in existing else 0)]
        if bad:
            errors.append(
                f"{day:%a %d %b}: no hours can be entered for {', '.join(names.get(p, str(p)) for p in bad)} "
                "(Completed, not assigned, or outside the joining / last working day)."
            )
            continue
        other_total = sum(e.hours for (d, pid), e in existing.items() if d == day and pid not in new)
        day_total = other_total + sum(new.values()) + new_leave
        if day_total > rules.MAX_DAY_HOURS:
            errors.append(f"{day:%a %d %b}: has {day_total} hours; a person can log at most "
                          f"{rules.MAX_DAY_HOURS} hours a day across all projects and leave.")
            continue
        plan[day] = (changed, new_leave if leave_changed else None)

    if errors:
        transaction.set_rollback(True)
        return errors, warnings

    def _warn_if_overwritten(ts, day):
        if ts and loaded_at and ts.updated_at > loaded_at and ts.updated_by_id not in (None, actor.pk):
            who = ts.updated_by.display_name if ts.updated_by else "someone"
            warnings.append(f"{day:%a %d %b} was changed by {who} after you opened this page; your values replaced theirs.")

    for day, leave in auto_leave.items():
        ts = ts_days.get(day)
        _warn_if_overwritten(ts, day)
        old = ts.leave_hours if ts else 0
        if ts is None:
            ts = TimesheetDay.objects.create(user=target, date=day, auto_filled=True)
        ts.leave_hours = leave
        ts.updated_by = actor
        ts.save(update_fields=["leave_hours", "updated_by", "updated_at"])
        audit(actor, "hours", f"{day:%Y-%m-%d} · Leave", old, leave, target=target)
        ensure_day(ctx, day, holidays, now_day, start_day, force=True)

    for day, (changed, leave) in plan.items():
        ts = ts_days.get(day)
        _warn_if_overwritten(ts, day)
        if ts is None:
            ts = TimesheetDay.objects.create(user=target, date=day)
        locked = rules.is_locked(day, now_day)
        for pid, hours in changed.items():
            entry = existing.get((day, pid))
            old = entry.hours if entry else 0
            if entry:
                entry.hours = hours
                entry.save(update_fields=["hours"])
            else:
                TimesheetEntry.objects.create(day=ts, project_id=pid, hours=hours)
            audit(actor, "hours", f"{day:%Y-%m-%d} · {names.get(pid, pid)}", old, hours, target=target, after_lock=locked)
        if leave is not None:
            audit(actor, "hours", f"{day:%Y-%m-%d} · Leave", ts.leave_hours, leave, target=target, after_lock=locked)
            ts.leave_hours = leave
        ts.edited = True
        if ts.status == "submitted" and not submit:
            audit(actor, "day_status", f"{day:%Y-%m-%d}", "submitted", "draft", target=target, after_lock=locked)
            ts.status = "draft"
        ts.updated_by = actor
        ts.save()

    if submit:
        for ts in TimesheetDay.objects.filter(user=target, date__in=days).exclude(status="submitted"):
            if (not hours_allowed_on(actor, rules.day_kind(ts.date, holidays))
                    or not perms.can_edit_day(actor, target, ts.date, now_day)):
                continue
            ts.status = "submitted"
            ts.updated_by = actor
            ts.save(update_fields=["status", "updated_by", "updated_at"])
            audit(actor, "day_status", f"{ts.date:%Y-%m-%d}", "draft", "submitted", target=target,
                  after_lock=rules.is_locked(ts.date, now_day))
    return errors, warnings


# --------------------------------------------------------------------------- attendance override / holidays
def override_attendance(actor, target, day, status, reason=""):
    """Mark a person Present, Absent or on Leave for one date. Leave is a full day (8 hours) in their
    Leave row — the same thing they would enter themselves — so it also shows in their timesheet and in
    the Leave count. Leave is refused on an off day, and on a day where hours were already logged by hand
    (those need clearing first, so nothing a person typed is deleted silently). Marking Present or Absent
    on a day that is a full day of leave removes that leave, otherwise the leave would keep winning."""
    perms.require(perms.can_override_attendance(actor, target))
    now_day, start_day = today(), go_live_date()
    holidays = holiday_map(day, day, target)
    locked = rules.is_locked(day, now_day)
    with transaction.atomic():
        ts = TimesheetDay.objects.select_for_update().filter(user=target, date=day).first()
        att = Attendance.objects.filter(user=target, date=day).first()
        old = f"{att.status}{' (override)' if att and att.overridden else ''}" if att else "not recorded"

        if status == "leave":
            if rules.day_kind(day, holidays) != rules.WORKDAY:
                raise ValueError(f"{day:%a %d %b} is not a working day, so it can't be marked as leave.")
            worked = sum(e.hours for e in ts.entries.all()) if ts else 0
            if worked and (ts.edited or ts.status == "submitted"):
                raise ValueError(
                    f"{target.display_name} has {worked} h logged on {day:%a %d %b}. Clear those hours in their "
                    "timesheet first, then mark the day as leave.")
            old_leave = ts.leave_hours if ts else 0
            if ts is None:
                ts = TimesheetDay.objects.create(user=target, date=day, auto_filled=True)
            ts.leave_hours = rules.DAILY_HOURS
            ts.updated_by = actor
            ts.save(update_fields=["leave_hours", "updated_by", "updated_at"])
            audit(actor, "attendance", f"{day:%Y-%m-%d}", old, f"leave (override){': ' + reason if reason else ''}",
                  target=target, after_lock=locked)
            if old_leave != rules.DAILY_HOURS:
                audit(actor, "hours", f"{day:%Y-%m-%d} · Leave", old_leave, rules.DAILY_HOURS, target=target,
                      after_lock=locked)
            ensure_day(PersonContext(target), day, holidays, now_day, start_day, force=True)  # auto hours make way
            return ts

        if ts and ts.leave_hours >= rules.DAILY_HOURS:
            audit(actor, "hours", f"{day:%Y-%m-%d} · Leave", ts.leave_hours, 0, target=target, after_lock=locked)
            ts.leave_hours = 0
            ts.updated_by = actor
            ts.save(update_fields=["leave_hours", "updated_by", "updated_at"])
        if att is None:
            att = Attendance(user=target, date=day)
        att.status = status
        att.overridden = True
        att.override_by = actor
        att.override_reason = reason[:240]
        att.save()
        audit(actor, "attendance", f"{day:%Y-%m-%d}", old, f"{status} (override){': ' + reason if reason else ''}",
              target=target, after_lock=locked)
        ensure_day(PersonContext(target), day, holidays, now_day, start_day, force=True)
    return att


def save_holiday(actor, day, name, holiday=None, teams=None):
    """Add or edit a holiday. `teams` = the teams it is limited to ([] = everyone; None = keep as is
    when editing, everyone when adding). Refused when it overlaps another holiday on that date:
    either one is for everyone, or they share a team."""
    perms.require(perms.can_manage_holidays(actor))
    if teams is None:
        teams = list(holiday.teams.all()) if holiday else []
    team_ids = {t.pk for t in teams}
    for other in Holiday.objects.filter(date=day).exclude(pk=getattr(holiday, "pk", None)).prefetch_related("teams"):
        other_ids = {t.pk for t in other.teams.all()}
        if not team_ids or not other_ids or team_ids & other_ids:
            raise ValueError(f"There is already a holiday on {day:%d %b %Y} for {other.applies_to}: {other.name}.")
    old_day = holiday.date if holiday else None
    applies = ", ".join(sorted((t.name for t in teams), key=str.lower)) or "Everyone"
    with transaction.atomic():
        if holiday is None:
            holiday = Holiday.objects.create(date=day, name=name, created_by=actor)
            holiday.teams.set(teams)
            audit(actor, "holiday", f"{day:%Y-%m-%d}", "", f"added: {name} · {applies}")
        else:
            old = f"{holiday.date:%Y-%m-%d} {holiday.name} · {holiday.applies_to}"
            holiday.date, holiday.name = day, name
            holiday.save()
            holiday.teams.set(teams)
            audit(actor, "holiday", f"{day:%Y-%m-%d}", old, f"{day:%Y-%m-%d} {name} · {applies}")
    refresh_date_for_everyone(day)
    if old_day and old_day != day:
        refresh_date_for_everyone(old_day)
    return holiday


def delete_holiday(actor, holiday):
    perms.require(perms.can_manage_holidays(actor))
    day, name = holiday.date, holiday.label
    holiday.delete()
    audit(actor, "holiday", f"{day:%Y-%m-%d}", f"{name}", "deleted")
    refresh_date_for_everyone(day)


def save_working_day(actor, day, name, working_day=None):
    """Add or edit an extra working day: a Saturday or Sunday that counts as a normal workday for everyone
    (8 hours auto-filled, attendance tracked). Refused on a date that is already a workday, and when another
    working day is already set on that date."""
    perms.require(perms.can_manage_holidays(actor))
    if not rules.is_weekend_off(day):
        raise ValueError(f"{day:%a %d %b %Y} is already a normal working day; only Sundays and the other "
                         "Saturdays need to be opened. (To close a normal working day, add it as a holiday.)")
    other = WorkingDay.objects.filter(date=day).exclude(pk=getattr(working_day, "pk", None)).first()
    if other:
        raise ValueError(f"There is already a working day on {day:%d %b %Y}: {other.name}.")
    old_day = working_day.date if working_day else None
    with transaction.atomic():
        if working_day is None:
            working_day = WorkingDay.objects.create(date=day, name=name, created_by=actor)
            audit(actor, "holiday", f"{day:%Y-%m-%d}", "", f"working day added: {name}")
        else:
            old = f"{working_day.date:%Y-%m-%d} {working_day.name}"
            working_day.date, working_day.name = day, name
            working_day.save()
            audit(actor, "holiday", f"{day:%Y-%m-%d}", f"working day {old}", f"{day:%Y-%m-%d} {name}")
    refresh_date_for_everyone(day)
    if old_day and old_day != day:
        refresh_date_for_everyone(old_day)
    return working_day


def delete_working_day(actor, working_day):
    perms.require(perms.can_manage_holidays(actor))
    day, name = working_day.date, working_day.name
    working_day.delete()
    audit(actor, "holiday", f"{day:%Y-%m-%d}", f"working day {name}", "deleted")
    refresh_date_for_everyone(day)


def holiday_links():
    from .models import TeamHolidayLink

    return TeamHolidayLink.objects.select_related("team", "follows").order_by("team__name")


def _recent_holiday_dates():
    """Holiday and working-day dates whose untouched days may need recomputing after a team link changes."""
    now_day = today()
    first = now_day.replace(day=1)
    return sorted(set(Holiday.objects.filter(date__gte=first, date__lte=now_day).values_list("date", flat=True))
                  | set(WorkingDay.objects.filter(date__gte=first, date__lte=now_day).values_list("date", flat=True)))


def set_team_holiday_link(actor, team, follows):
    """`team` takes `follows`'s holidays (follows=None removes the link). Timesheet-only."""
    from .models import TeamHolidayLink

    perms.require(perms.can_manage_holidays(actor))
    if follows is not None:
        if follows.pk == team.pk:
            raise ValueError("A team can't follow itself.")
        chain, tid = set(), follows.pk
        links = dict(TeamHolidayLink.objects.values_list("team_id", "follows_id"))
        while tid and tid not in chain:
            chain.add(tid)
            tid = links.get(tid)
        if team.pk in chain:
            raise ValueError(f"{follows.name} already follows {team.name}'s holidays.")
    link = TeamHolidayLink.objects.filter(team=team).select_related("follows").first()
    old = link.follows.name if link else "own holidays"
    if follows is None:
        if link:
            link.delete()
    elif link:
        link.follows = follows
        link.save(update_fields=["follows"])
    else:
        TeamHolidayLink.objects.create(team=team, follows=follows, created_by=actor)
    audit(actor, "holiday", f"{team.name} follows", old, follows.name if follows else "own holidays")
    for day in _recent_holiday_dates():
        refresh_date_for_everyone(day)


def set_remainder_project(actor, target, project):
    perms.require(perms.can_set_remainder(actor, target))
    profile = profile_for(target)
    old = profile.remainder_project.key if profile.remainder_project else "first eligible project"
    profile.remainder_project = project
    profile.save(update_fields=["remainder_project"])
    audit(actor, "settings", "remainder project", old, project.key if project else "first eligible project", target=target)
    ensure_range(target, [today()])  # today onward; finished days keep their split


def set_main_project(actor, target, project, main):
    """Star (main=True) or unstar one of the person's projects. At most rules.DAILY_HOURS open main
    projects, so the split never needs fractions. Applies from today; finished days keep their split."""
    perms.require(perms.can_set_main_projects(actor, target))
    ctx = PersonContext(target)
    open_now = {p.pk for p in ctx.eligible_projects(today())}
    if main:
        if project.pk not in open_now:
            raise ValueError(f"{project.key} isn't one of {target.display_name}'s open projects.")
        if project.pk in ctx.main_ids:
            return
        if len(ctx.main_ids & open_now) >= rules.DAILY_HOURS:
            raise ValueError(f"You can mark at most {rules.DAILY_HOURS} main projects. Remove a star first.")
        TimesheetMainProject.objects.create(user=target, project=project, created_by=actor)
    else:
        if not TimesheetMainProject.objects.filter(user=target, project=project).delete()[0]:
            return
    audit(actor, "settings", f"main project {project.key}", "" if main else "main", "main" if main else "",
          target=target)
    ensure_range(target, [today()])  # today onward; finished days keep their split


def set_employment_dates(actor, target, joining, last_day):
    perms.require(perms.can_manage_profile(actor))
    profile = profile_for(target)
    old = f"{profile.joining_date or '—'} → {profile.last_working_day or '—'}"
    profile.joining_date, profile.last_working_day = joining, last_day
    profile.save(update_fields=["joining_date", "last_working_day"])
    audit(actor, "settings", "joining / last working day", old, f"{joining or '—'} → {last_day or '—'}", target=target)


def set_unlock(actor, target, month_first, active):
    from .models import TimesheetUnlock

    perms.require(perms.can_manage_profile(actor))
    TimesheetUnlock.objects.filter(user=target, month=month_first, active=True).update(active=False)
    if active:
        TimesheetUnlock.objects.create(user=target, month=month_first, unlocked_by=actor)
    audit(actor, "unlock", f"{month_first:%b %Y}", "locked" if active else "unlocked",
          "unlocked" if active else "locked", target=target)


# --------------------------------------------------------------------------- people view
def people_week(people, monday, project=None):
    """One row per person for a week: hours per day (all projects together, or one project when
    `project` is given), attendance, totals, and submitted working days."""
    from django.db.models import Sum

    days = rules.week_days(monday)
    now_day, start_day = today(), go_live_date()
    book = HolidayBook(days[0], days[-1])
    people = list(people)
    ids = [p.pk for p in people]
    hours = {
        (r["day__user_id"], r["day__date"]): r["h"]
        for r in TimesheetEntry.objects.filter(day__user_id__in=ids, day__date__in=days,
                                               **({"project": project} if project else {}))
        .values("day__user_id", "day__date").annotate(h=Sum("hours"))
    }
    submitted = {
        (u, d) for u, d in TimesheetDay.objects.filter(user_id__in=ids, date__in=days, status="submitted")
        .values_list("user_id", "date")
    }
    leave = {
        (u, d): h for u, d, h in TimesheetDay.objects.filter(user_id__in=ids, date__in=days, leave_hours__gt=0)
        .values_list("user_id", "date", "leave_hours")
    }
    absent = {
        (u, d) for u, d in Attendance.objects.filter(user_id__in=ids, date__in=days, status="absent")
        .exclude(date=now_day).values_list("user_id", "date")
        if leave.get((u, d), 0) < rules.DAILY_HOURS  # a full day of leave is not an absence
    }
    if now_day in days:
        for person in people:  # today is decided live: Absent only once the work window has closed
            holidays = book.for_user(person)
            if (rules.day_kind(now_day, holidays) == rules.WORKDAY
                    and attendance_state(person, now_day, holidays, now_day, start_day) == "absent"):
                absent.add((person.pk, now_day))
    rows = []
    for person in people:
        holidays = book.for_user(person)
        workdays = [d for d in days if rules.day_kind(d, holidays) == rules.WORKDAY]
        cells = []
        for d in days:
            kind = rules.day_kind(d, holidays)
            cells.append({
                "date": d, "kind": kind, "hours": hours.get((person.pk, d), 0), "leave": leave.get((person.pk, d), 0),
                "absent": kind == rules.WORKDAY and (person.pk, d) in absent,
                "submitted": (person.pk, d) in submitted, "future": d > now_day,
            })
        rows.append({
            "user": person, "cells": cells, "total": sum(c["hours"] for c in cells),
            "absent_days": sum(1 for c in cells if c["absent"]),
            "leave_total": sum(c["leave"] for c in cells),
            "submitted_days": sum(1 for c in cells if c["submitted"]),
            "submitted_work": sum(1 for d in workdays if (person.pk, d) in submitted),
            "workdays": len(workdays),
        })
    for r in rows:
        r["fully"] = bool(r["workdays"]) and r["submitted_work"] == r["workdays"]
        r["submitted_pct"] = round(100 * r["submitted_work"] / r["workdays"]) if r["workdays"] else 0
    columns = []
    for d in days:
        kind, holiday, label = book.header(d, people)
        columns.append({"date": d, "kind": kind, "holiday": holiday, "holiday_label": label, "is_today": d == now_day})
    workdays = [c["date"] for c in columns if c["kind"] == rules.WORKDAY]
    return {
        "days": columns, "rows": rows,
        "day_totals": [sum(r["cells"][i]["hours"] for r in rows) for i in range(7)],
        "grand_total": sum(r["total"] for r in rows),
        "absent_total": sum(r["absent_days"] for r in rows),
        "fully_submitted": sum(1 for r in rows if r["fully"]),
        "workdays": len(workdays),
        "today": now_day,
    }


def team_today(people):
    """Today's headline numbers for the Team timesheet cards."""
    from django.db.models import Sum

    now_day, start_day = today(), go_live_date()
    book = HolidayBook(now_day, now_day)
    people = list(people)
    states = [attendance_state(p, now_day, book.for_user(p), now_day, start_day) for p in people]
    working = (TimesheetEntry.objects.filter(day__user__in=people, day__date=now_day)
               .values("day__user_id").annotate(h=Sum("hours")).filter(h__gt=0).count())
    return {
        "total": len(people),
        "present": sum(1 for s in states if s == "present"),
        "absent": sum(1 for s in states if s == "absent"),
        "leave": sum(1 for s in states if s == "leave"),
        "pending": sum(1 for s in states if s == "pending"),
        "working": working,
        "kind": book.header(now_day, people)[0],
    }


# --------------------------------------------------------------------------- reports
def report_scope(viewer):
    """-> (user_ids with full visibility, project_ids visible as Project Lead)."""
    ids = perms.view_ids(viewer)  # None = everyone (admins, or the timesheet view permission at "all")
    if ids is None:
        return None, set()
    return ids, perms.led_project_ids(viewer)


def weekly_report(viewer, monday, user_filter=None, project_filter=None):
    """Hours per employee, per project, per day for one Monday–Sunday week, plus totals,
    absent days, weekends, holidays and — for a week that spans two months — each month's part."""
    days = rules.week_days(monday)
    now_day, start_day = today(), go_live_date()
    book = HolidayBook(days[0], days[-1])
    full_ids, lead_projects = report_scope(viewer)

    people = timesheet_people() if full_ids is None else User.objects.filter(pk__in=full_ids, is_active=True)
    entries = TimesheetEntry.objects.filter(day__date__in=days).select_related("day", "project", "day__user")
    if full_ids is not None:
        from django.db.models import Q

        entries = entries.filter(Q(day__user_id__in=full_ids) | Q(project_id__in=lead_projects))
        if lead_projects:
            people = User.objects.filter(
                Q(pk__in=full_ids) | Q(pk__in=entries.values("day__user_id")), is_active=True
            )
    if user_filter:
        entries = entries.filter(day__user_id=user_filter)
        people = people.filter(pk=user_filter)
    if project_filter:
        entries = entries.filter(project_id=project_filter)
    people = list(people.order_by("first_name", "username"))

    grid = OrderedDict()
    for e in entries:
        key = (e.day.user_id, e.project_id)
        row = grid.setdefault(key, {"user": e.day.user, "project": e.project, "hours": {d: 0 for d in days}})
        row["hours"][e.day.date] += e.hours
    rows = sorted(grid.values(), key=lambda r: (r["user"].display_name.lower(), r["project"].key.lower()))
    for r in rows:
        r["cells"] = [r["hours"][d] for d in days]
        r["total"] = sum(r["cells"])

    months = []
    for d in days:
        if not months or months[-1]["month"] != (d.year, d.month):
            months.append({"month": (d.year, d.month), "label": d.strftime("%b %Y"), "days": []})
        months[-1]["days"].append(d)

    summary = []
    for person in people:
        person_rows = [r for r in rows if r["user"].pk == person.pk]
        # a Project Lead sees hours on their projects only, not the person's attendance
        limited = full_ids is not None and person.pk not in full_ids
        profile = profile_for(person)
        employed = lambda d: (not profile.joining_date or d >= profile.joining_date) and (  # noqa: E731
            not profile.last_working_day or d <= profile.last_working_day)
        absent = [] if limited else [
            d for d in days if d <= now_day and employed(d)
            and attendance_state(person, d, book.for_user(person), now_day, start_day) == "absent"
        ]
        by_day = [sum(r["hours"][d] for r in person_rows) for d in days]
        summary.append({
            "user": person, "by_day": by_day, "total": sum(by_day), "absent": absent, "limited": limited,
            "months": [{"label": m["label"], "total": sum(r["hours"][d] for r in person_rows for d in m["days"])}
                       for m in months],
            "submitted": TimesheetDay.objects.filter(user=person, date__in=days, status="submitted").count(),
            "leave": [] if limited else list(
                TimesheetDay.objects.filter(user=person, date__in=days, leave_hours__gt=0)
                .order_by("date").values_list("date", "leave_hours")),
        })
        summary[-1]["leave_total"] = sum(h for _d, h in summary[-1]["leave"])

    day_info = []
    for d in days:
        kind, holiday, label = book.header(d, people)
        day_info.append({"date": d, "kind": kind, "holiday": holiday, "holiday_label": label})
    return {
        "monday": monday, "sunday": days[-1], "days": day_info, "rows": rows, "summary": summary,
        "months": [{"label": m["label"], "total": sum(r["hours"][d] for r in rows for d in m["days"])} for m in months],
        "day_totals": [sum(r["hours"][d] for r in rows) for d in days],
        "grand_total": sum(r["total"] for r in rows),
        "weekends": [d for d in days if rules.is_weekend_off(d) and not book.works_on(d, people)],
        "holidays": [h for h in book.holidays if any(book.for_user(p).get(h.date) is h for p in people)],
        "spans_months": len(months) > 1,
    }


# --------------------------------------------------------------------------- scheduled jobs
def _once(key, detail=""):
    """True the first time a key is claimed; False afterwards (job already ran)."""
    _run, created = JobRun.objects.get_or_create(key=key, defaults={"detail": detail[:255]})
    return created


def job_close_day(day):
    """End of day: mark Absent (workdays with no login), then auto-fill that day for everyone."""
    now_day, start_day = today(), go_live_date()
    book = HolidayBook(day, day)
    for user in timesheet_people():
        try:
            holidays = book.for_user(user)
            ctx = PersonContext(user)
            if not ctx.active_on(day):
                continue  # before joining / after the last working day: nothing to record
            ensure_day(ctx, day, holidays, now_day, start_day, finalize=True)
            attendance_state(user, day, holidays, now_day, start_day, finalize=True)
        except Exception:  # noqa: BLE001
            logger.exception("end-of-day close failed for %s on %s", user.pk, day)


def job_recheck_early_logins():
    """One-time fix after the window start moved from 10:00 to 00:00: re-check past workdays that were
    recorded Absent automatically (not overridden). Anyone seen that day before 18:30 becomes Present
    and gets their auto-filled hours. Days a person edited, submitted or that are locked are left alone."""
    now_day, start_day = today(), go_live_date()
    fixed = 0
    rows = Attendance.objects.filter(status="absent", overridden=False, date__gte=start_day, date__lt=now_day)
    for att in rows.select_related("user"):
        try:
            if not seen_in_window(att.user, att.date, att):
                continue
            att.status = "present"
            att.save(update_fields=["status", "updated_at"])
            ctx = PersonContext(att.user)
            ensure_day(ctx, att.date, holiday_map(att.date, att.date, att.user), now_day, start_day, force=True)
            fixed += 1
        except Exception:  # noqa: BLE001
            logger.exception("early-login recheck failed for %s on %s", att.user_id, att.date)
    return fixed


LOGIN_REMINDER_SUBJECT = "Reminder: Planwise login pending for today"
LOGIN_REMINDER_LAST_SEND = time(13, 0)  # if the worker was down at 10:15, still send until 13:00, never later


def login_reminder_html(name, day):
    from django.utils.html import escape

    return (
        '<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;line-height:1.6;color:#101828">'
        f"<p>Hi {escape(name)},</p>"
        f"<p>You haven't logged in to Planwise today ({day:%A}, {day:%d %b %Y}).</p>"
        "<p>Please log in to mark your attendance:<br>"
        '<a href="https://planwise.augtrans.com">https://planwise.augtrans.com</a></p>'
        "<p>Thanks,<br>Planwise · Augtrans</p></div>"
    )


def login_reminder_recipients(day):
    """People to remind on `day`: staff on a working day (not Sat/Sun, not their holiday, not on leave,
    no Present override, within employment dates) who have not logged in yet and have an email address."""
    now_day, start_day = today(), go_live_date()
    out = []
    for user in timesheet_people().order_by("first_name", "username"):
        try:
            if not (user.email or "").strip():
                continue
            if not PersonContext(user).active_on(day):
                continue
            state = attendance_state(user, day, holiday_map(day, day, user), now_day, start_day)
            if state == "absent":
                out.append(user)
        except Exception:  # noqa: BLE001
            logger.exception("login reminder check failed for %s", user.pk)
    return out


def job_login_reminders(day, dry_run=False, only_email=None):
    """Email each person who has not logged in yet today. Each person is mailed at most once a day.
    only_email: send just to that address (testing) — nobody else is mailed."""
    from . import mailer

    sent, failed, skipped = [], [], []
    if only_email:
        user = User.objects.filter(email__iexact=only_email).first()
        name = (user.get_full_name() or user.username) if user else only_email.split("@")[0]
        targets = [(name, only_email, None)]
    else:
        targets = [(u.get_full_name() or u.username, u.email.strip(), u) for u in login_reminder_recipients(day)]
    for name, address, user in targets:
        if dry_run:
            sent.append(address)
            continue
        if user is not None and not _once(f"login-mail:{day:%Y-%m-%d}:{user.pk}"):
            skipped.append(address)  # already mailed today
            continue
        ok, error = mailer.send_mail(address, LOGIN_REMINDER_SUBJECT, login_reminder_html(name, day))
        (sent if ok else failed).append(address if ok else f"{address} ({error[:120]})")
    logger.info("login reminders %s: sent=%d failed=%d skipped=%d%s", day, len(sent), len(failed), len(skipped),
                " (dry run)" if dry_run else "")
    return {"sent": sent, "failed": failed, "skipped": skipped}


TIMESHEET_REMINDER_SUBJECT = "Reminder: Planwise timesheet submission pending"
TIMESHEET_REMINDER_LAST_SEND = time(17, 0)  # if the worker was down at 12:00 on Friday, still send until 17:00, never later


def pending_week_ranges(unsubmitted_days, day):
    """-> [(start, end)], one per Mon–Sun week of `day`'s month that holds an unsubmitted working day on or
    before `day`. The first and last week are cut at the month's edges: a closed month can't be submitted."""
    first, last = day.replace(day=1), rules.month_end(day)
    mondays = sorted({rules.week_start(d) for d in unsubmitted_days if first <= d <= day})
    return [(max(m, first), min(m + timedelta(days=6), last)) for m in mondays]


def timesheet_pending_days(user_ids, day):
    """{user_id: {dates}} — days from the 1st of `day`'s month up to `day` where hours or leave are saved but
    not submitted. A day with nothing saved (absent, no project) has nothing to submit, so it never counts."""
    from django.db.models import Sum

    out = {}
    rows = (TimesheetDay.objects.filter(user_id__in=user_ids, date__gte=day.replace(day=1), date__lte=day)
            .exclude(status="submitted").annotate(h=Sum("entries__hours"))
            .values_list("user_id", "date", "h", "leave_hours"))
    for uid, d, hours, leave in rows:
        if (hours or 0) > 0 or leave > 0:
            out.setdefault(uid, set()).add(d)
    return out


def timesheet_reminder_weeks(user, day, pending_days):
    """The weeks to list for one person; [] means don't mail them: no email address, nothing pending, `day` is a
    holiday for them, they are on a full day of leave, or they are outside their employment dates."""
    if not (user.email or "").strip() or not pending_days:
        return []
    ctx, holidays = PersonContext(user), holiday_map(day.replace(day=1), day, user)
    if not ctx.active_on(day) or rules.day_kind(day, holidays) != rules.WORKDAY or on_full_leave(user, day):
        return []
    days = {d for d in pending_days if rules.day_kind(d, holidays) == rules.WORKDAY and ctx.active_on(d)}
    return pending_week_ranges(days, day)


def timesheet_reminder_targets(day):
    """[(user, weeks)] — everyone to remind on `day`."""
    users = list(timesheet_people().order_by("first_name", "username"))
    pending = timesheet_pending_days([u.pk for u in users], day)
    out = []
    for user in users:
        try:
            weeks = timesheet_reminder_weeks(user, day, pending.get(user.pk))
            if weeks:
                out.append((user, weeks))
        except Exception:  # noqa: BLE001
            logger.exception("timesheet reminder check failed for %s", user.pk)
    return out


def timesheet_reminder_html(name, weeks):
    from django.utils.html import escape

    items = "".join(f"<li>{start:%d %b %Y} to {end:%d %b %Y}</li>" for start, end in weeks)
    return (
        '<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;line-height:1.6;color:#101828">'
        f"<p>Hi {escape(name)},</p>"
        "<p>This is a gentle reminder that your timesheet submission is pending for the following week(s):</p>"
        f"<ul>{items}</ul>"
        "<p>Please submit your timesheet at your earliest convenience to ensure accurate work tracking.<br>"
        "If you have already submitted your timesheet, please ignore this email.</p>"
        "<p>Thanks,<br>Planwise · Augtrans</p></div>"
    )


def job_timesheet_reminders(day, dry_run=False, only_email=None):
    """Friday reminder: email each person who has an unsubmitted week this month; at most one mail per person per day.
    only_email: send just to that address (testing) — their own pending weeks if they have any, else a sample week."""
    from . import mailer

    sent, failed, skipped, plan = [], [], [], []
    if only_email:
        user = User.objects.filter(email__iexact=only_email).first()
        name = (user.get_full_name() or user.username) if user else only_email.split("@")[0]
        weeks = timesheet_reminder_weeks(user, day, timesheet_pending_days([user.pk], day).get(user.pk)) if user else []
        if not weeks:  # nothing pending for them: still send a sample so the mail itself can be checked
            first = day.replace(day=1)
            weeks = [(first, min(first + timedelta(days=6 - first.weekday()), rules.month_end(day)))]
        targets = [(name, only_email, None, weeks)]
    else:
        targets = [(u.get_full_name() or u.username, u.email.strip(), u, weeks)
                   for u, weeks in timesheet_reminder_targets(day)]
    for name, address, user, weeks in targets:
        plan.append((name, weeks))
        if dry_run:
            sent.append(address)
            continue
        if user is not None and not _once(f"timesheet-mail:{day:%Y-%m-%d}:{user.pk}"):
            skipped.append(address)  # already mailed today
            continue
        ok, error = mailer.send_mail(address, TIMESHEET_REMINDER_SUBJECT, timesheet_reminder_html(name, weeks))
        (sent if ok else failed).append(address if ok else f"{address} ({error[:120]})")
    logger.info("timesheet reminders %s: sent=%d failed=%d skipped=%d%s", day, len(sent), len(failed), len(skipped),
                " (dry run)" if dry_run else "")
    return {"sent": sent, "failed": failed, "skipped": skipped, "plan": plan}


def job_mark_absent(day):
    """At the end of the work window (18:30): record Present / Absent for everyone for today."""
    now_day, start_day = today(), go_live_date()
    book = HolidayBook(day, day)
    for user in timesheet_people():
        holidays = book.for_user(user)
        if rules.day_kind(day, holidays) != rules.WORKDAY:
            continue  # weekend, or a holiday for this person's team
        try:
            ctx = PersonContext(user)
            if ctx.active_on(day):
                attendance_state(user, day, holidays, now_day, start_day, finalize=True)
                ensure_day(ctx, day, holidays, now_day, start_day)
        except Exception:  # noqa: BLE001
            logger.exception("marking absent failed for %s on %s", user.pk, day)


def job_weekly_reports(monday_of_report):
    from django.urls import reverse

    from apps.notify.services import notify

    managers = [u for u in timesheet_people() if perms.is_reporting_manager(u)]
    sunday = monday_of_report + timedelta(days=6)
    url = reverse("timesheet:reports") + f"?week={monday_of_report:%Y-%m-%d}"
    for manager in managers:
        try:
            notify(manager, "Weekly timesheet report",
                   f"Your team's hours for {monday_of_report:%d %b} – {sunday:%d %b %Y} are ready.", url=url, kind="mention")
        except Exception:  # noqa: BLE001
            logger.exception("weekly report notify failed for %s", manager.pk)
    return len(managers)


def run_scheduled_jobs(now=None):
    """Called every few seconds by the worker; each job claims a JobRun key so it runs once.
      * end-of-day close for every finished day since go-live (catches up after downtime, max 14 days)
      * Monday 08:00 weekly report to reporting managers for the previous Monday–Sunday
      * month-end lock recorded in the audit log on the 1st
    """
    now = timezone.localtime(now or timezone.now())
    now_day, start_day = now.date(), go_live_date()
    from django.conf import settings as dj_settings

    remind_at = _hhmm(getattr(dj_settings, "LOGIN_REMINDER_TIME", "10:15"), time(10, 15))
    if (getattr(dj_settings, "LOGIN_REMINDER_ENABLED", False) and now.weekday() < 5
            and remind_at <= now.time() < LOGIN_REMINDER_LAST_SEND and _once(f"login-reminders:{now_day:%Y-%m-%d}")):
        job_login_reminders(now_day)  # Mon-Fri 10:15: email everyone not logged in yet
    ts_remind_at = _hhmm(getattr(dj_settings, "TIMESHEET_REMINDER_TIME", "12:00"), time(12, 0))
    if (getattr(dj_settings, "TIMESHEET_REMINDER_ENABLED", False) and now.weekday() == 4
            and ts_remind_at <= now.time() < TIMESHEET_REMINDER_LAST_SEND
            and _once(f"timesheet-reminders:{now_day:%Y-%m-%d}")):
        job_timesheet_reminders(now_day)  # Friday 12:00: email whoever has an unsubmitted week this month
    if work_window(now_day)[0].time() < time(10, 0) and _once("recheck:early-logins-v1"):
        job_recheck_early_logins()  # runs once, after the window start moved earlier than 10:00
    if now >= work_window(now_day)[1] and now_day >= start_day and _once(f"absent:{now_day:%Y-%m-%d}"):
        job_mark_absent(now_day)  # the work window just closed: nobody seen in it today is Absent
    first = max(start_day, now_day - timedelta(days=14))
    day = first
    while day < now_day:
        if _once(f"close:{day:%Y-%m-%d}"):
            job_close_day(day)
        day += timedelta(days=1)
    if now.weekday() == 0 and now.hour >= 8:
        report_monday = now_day - timedelta(days=7)
        if _once(f"weekly:{report_monday:%Y-%m-%d}"):
            job_weekly_reports(report_monday)
    last_month_end = now_day.replace(day=1) - timedelta(days=1)
    if last_month_end >= start_day and _once(f"lock:{last_month_end:%Y-%m}"):
        audit(None, "lock", f"{last_month_end:%B %Y}", "open", "locked")
