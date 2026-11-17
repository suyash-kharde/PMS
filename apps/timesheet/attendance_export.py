"""One Excel workbook with attendance and the hours people filled in, for any date range.

Separate from exports.py (the weekly report) so that stays untouched. Everything here is read-only:
attendance comes from the same attendance_state() the Attendance tab uses, hours from the same
timesheet rows the week view saves, and the people included are exactly perms.viewable_people()."""
import io
from collections import defaultdict
from datetime import timedelta

from django.db.models import Min
from django.db.models.functions import TruncDate
from django.http import HttpResponse
from django.utils import timezone

from . import permissions as perms
from . import rules, services
from .models import Attendance, TimesheetDay, TimesheetProfile

MAX_DAYS = 93  # about three months; a longer range would make the sheet slow to build and hard to read

CODE = {"present": "P", "absent": "A", "leave": "L", "holiday": "H", "weekend": "W"}
WORD = {"present": "Present", "absent": "Absent", "leave": "Leave", "holiday": "Holiday",
        "weekend": "Weekend", "not_tracked": "Not tracked", "future": ""}


def clean_range(start, end):
    """-> (start, end) in order, never past today. Raises ValueError with a message for the user."""
    now_day = services.today()
    end = min(end or now_day, now_day)
    start = start or end.replace(day=1)
    if start > end:
        start, end = end, start
    if (end - start).days + 1 > MAX_DAYS:
        raise ValueError(f"Choose a range of at most {MAX_DAYS} days (you chose {(end - start).days + 1}).")
    return start, end


def build(viewer, start, end, user_filter=None):
    """-> dict with the people in scope and, for each person and date, the attendance state, login
    times, hours per project, leave hours and whether the day was submitted."""
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    people = list(perms.viewable_people(viewer))
    if user_filter:
        people = [p for p in people if str(p.pk) == str(user_filter)]
        if not people:
            raise PermissionError("You can't view this person's attendance.")
    people.sort(key=lambda p: (p.display_name.lower(), p.pk))
    ids = [p.pk for p in people]

    now_day, start_day = services.today(), services.go_live_date()
    book = services.HolidayBook(start, end)

    profiles = {pr.user_id: pr for pr in TimesheetProfile.objects.filter(user_id__in=ids)}

    def employed(person, d):
        pr = profiles.get(person.pk)
        joined = pr.joining_date if pr else (services.local_date(person.date_joined) if person.date_joined else None)
        last = pr.last_working_day if pr else None
        return (not joined or d >= joined) and (not last or d <= last)

    attendance = {(a.user_id, a.date): a for a in
                  Attendance.objects.filter(user_id__in=ids, date__range=(start, end)).select_related("override_by")}

    from apps.accounts.models import PageVisit

    seen = {}
    window_start, _ = services.day_bounds(start)
    _, window_end = services.day_bounds(end)
    for row in (PageVisit.objects.filter(user_id__in=ids, visited_at__gte=window_start, visited_at__lt=window_end)
                .annotate(day=TruncDate("visited_at")).values("user_id", "day").annotate(t=Min("visited_at"))):
        seen[(row["user_id"], row["day"])] = row["t"]

    hours = defaultdict(list)  # (user_id, date) -> [(project, hours)]
    meta = {}  # (user_id, date) -> (leave_hours, status)
    for day in (TimesheetDay.objects.filter(user_id__in=ids, date__range=(start, end))
                .prefetch_related("entries__project")):
        meta[(day.user_id, day.date)] = (day.leave_hours, day.status)
        for entry in day.entries.all():
            if entry.hours:
                hours[(day.user_id, day.date)].append((entry.project, entry.hours))

    from apps.accounts.models import Team

    team_names = dict(Team.objects.values_list("pk", "name"))
    table = []
    for person in people:
        holidays = book.for_user(person)
        cells = []
        for d in days:
            if not employed(person, d):
                state = "not_tracked"
            elif d > now_day:
                state = "future"
            else:
                state = services.attendance_state(person, d, holidays, now_day, start_day)
            att = attendance.get((person.pk, d))
            leave, status = meta.get((person.pk, d), (0, ""))
            projects = sorted(hours.get((person.pk, d), []), key=lambda ph: ph[0].key.lower())
            note = holidays[d].label if d in holidays else ""
            if att and att.overridden:
                who = att.override_by.display_name if att.override_by else "someone"
                note = (note + "; " if note else "") + f"marked {att.status} by {who}" + (
                    f" ({att.override_reason})" if att.override_reason else "")
            cells.append({
                "date": d, "state": state, "note": note,
                "first_login": (att.first_login if att and att.first_login else seen.get((person.pk, d))),
                "last_logout": att.last_logout if att else None,
                "projects": projects, "hours": sum(h for _p, h in projects),
                "leave": leave, "submitted": status == "submitted", "has_day": bool(status),
            })
        table.append({"person": person, "cells": cells})
    return {"start": start, "end": end, "days": days, "table": table, "team_names": team_names}


def _hhmm(dt):
    return timezone.localtime(dt).strftime("%H:%M") if dt else ""


def _day_label(d):
    return f"{d:%d %b}\n{d:%a}"


def to_xlsx(data):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    head_font = Font(name="Arial", bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="4F46E5")
    body, bold = Font(name="Arial"), Font(name="Arial", bold=True)
    fills = {
        "present": PatternFill("solid", fgColor="D1FAE5"), "absent": PatternFill("solid", fgColor="FECACA"),
        "leave": PatternFill("solid", fgColor="FDE68A"), "holiday": PatternFill("solid", fgColor="E5E7EB"),
        "weekend": PatternFill("solid", fgColor="F3F4F6"),
    }
    title = f"Attendance and timesheet {data['start']:%d %b %Y} – {data['end']:%d %b %Y}"

    def sheet(ws, heads, rows, widths, freeze="C4"):
        ws.append([title])
        ws["A1"].font = Font(name="Arial", bold=True, size=13)
        ws.append([])
        ws.append(heads)
        for cell in ws[3]:
            cell.font, cell.fill = head_font, head_fill
            cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        for row in rows:
            ws.append(row)
            for cell in ws[ws.max_row]:
                cell.font = body
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = freeze

    def team_of(person):
        return data["team_names"].get(person.team_id, "")

    # ---- Summary: one row per person
    ws = wb.active
    ws.title = "Summary"
    rows = []
    for item in data["table"]:
        cs = item["cells"]
        count = lambda s: sum(1 for c in cs if c["state"] == s)  # noqa: E731
        # days there is something to submit (present or on leave) vs how many of those were submitted
        due = [c for c in cs if c["state"] in ("present", "leave")]
        done = sum(1 for c in due if c["submitted"])
        if not due:
            filled = "–"
        elif done == len(due):
            filled = "Filled"
        elif done == 0:
            filled = "Not filled"
        else:
            filled = "Partially filled"
        leave_days = sum(c["leave"] for c in cs) / rules.DAILY_HOURS  # a half day of leave counts as 0.5
        rows.append([
            item["person"].display_name, count("present"), int(leave_days) if leave_days == int(leave_days) else round(leave_days, 1),
            sum(c["hours"] for c in cs),
            sum(1 for c in cs if c["submitted"]), filled,
            ", ".join(sorted({p.name for c in cs for p, _h in c["projects"]}, key=str.lower)),  # projects worked on
        ])
    sheet(ws, ["Employee", "Present days", "Leave days", "Hours logged", "Days submitted", "Timesheet", "Projects"],
          rows, [26, 12, 12, 13, 14, 17, 60], freeze="B4")
    filled_fill = {"Filled": PatternFill("solid", fgColor="D1FAE5"), "Partially filled": PatternFill("solid", fgColor="FDE68A"),
                   "Not filled": PatternFill("solid", fgColor="FEE2E2")}
    for r in range(4, ws.max_row + 1):
        for c in (2, 3, 4, 5, 6):
            ws.cell(row=r, column=c).alignment = Alignment(horizontal="center")
        fill = filled_fill.get(ws.cell(row=r, column=6).value)
        if fill:
            ws.cell(row=r, column=6).fill = fill
        ws.cell(row=r, column=7).alignment = Alignment(wrap_text=True, vertical="top")
    ws.auto_filter.ref = f"A3:G{max(ws.max_row, 3)}"

    # ---- Attendance: person x date grid of P / A / L / H / W
    ws = wb.create_sheet("Attendance")
    heads = ["Employee"] + [_day_label(d) for d in data["days"]] + ["Present", "Absent", "Leave"]
    rows = []
    for item in data["table"]:
        cs = item["cells"]
        rows.append([item["person"].display_name]
                    + [CODE.get(c["state"], "–" if c["state"] == "not_tracked" else "") for c in cs]
                    + [sum(1 for c in cs if c["state"] == "present"), sum(1 for c in cs if c["state"] == "absent"),
                       sum(1 for c in cs if c["state"] == "leave")])
    sheet(ws, heads, rows, [26] + [6] * len(data["days"]) + [9, 9, 9], freeze="B4")
    ws.row_dimensions[3].height = 32
    for r_idx, item in enumerate(data["table"], start=4):
        for c_idx, cell in enumerate(item["cells"], start=2):
            ws.cell(row=r_idx, column=c_idx).alignment = Alignment(horizontal="center")
            fill = fills.get(cell["state"])
            if fill:
                ws.cell(row=r_idx, column=c_idx).fill = fill
    ws.append([])
    ws.append(["P = Present   A = Absent   L = Leave   H = Holiday   W = Weekend off   – = not tracked (before joining / "
               "go-live)   blank = future date"])
    ws.cell(row=ws.max_row, column=1).font = Font(name="Arial", italic=True)

    buf = io.BytesIO()
    wb.save(buf)
    response = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = (
        f'attachment; filename="attendance_timesheet_{data["start"]:%Y-%m-%d}_to_{data["end"]:%Y-%m-%d}.xlsx"')
    return response
