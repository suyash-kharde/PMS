"""Pure timesheet rules — no Django imports, so they can be unit-tested on their own.

The 8-hour split (spec §1):
    N = number of eligible (assigned, not Completed) projects
    share = floor(8 / N), remainder = 8 - share * N
    every project gets `share`; the remainder goes to ONE project — the chosen
    remainder project if it is eligible, otherwise the first eligible one.
    N = 0 -> nothing is filled (the caller shows a warning).
"""
import calendar
from datetime import date, timedelta

DAILY_HOURS = 8
MAX_DAY_HOURS = DAILY_HOURS  # one person works 8 hours a day in total, across all their projects

WORKDAY, WEEKEND, HOLIDAY = "workday", "weekend", "holiday"

# Saturdays are off except these ones of each month (1 = the first Saturday). Sundays are always off.
WORKING_SATURDAYS = (1,)


def split_hours(project_ids, remainder_project_id=None, total=DAILY_HOURS):
    """Ordered project ids -> {project_id: whole hours}. Sum is always `total` (or {} when none)."""
    project_ids = list(dict.fromkeys(project_ids))  # de-duplicate, keep order
    n = len(project_ids)
    if n == 0:
        return {}
    share = total // n
    remainder = total - share * n
    hours = {pid: share for pid in project_ids}
    if remainder:
        target = remainder_project_id if remainder_project_id in hours else project_ids[0]
        hours[target] += remainder
    return hours


def nth_weekday_of_month(day):
    """1 for the first Monday/Tuesday/... of the month, 2 for the second, and so on."""
    return (day.day - 1) // 7 + 1


def is_weekend_off(day):
    """Every Sunday, and every Saturday except the WORKING_SATURDAYS. No hours are entered on these."""
    if day.weekday() == 6:
        return True
    return day.weekday() == 5 and nth_weekday_of_month(day) not in WORKING_SATURDAYS


class HolidayDays(dict):
    """{date: Holiday} for one person, plus `working`: {date: WorkingDay} for the dates an admin opened as an
    extra working day (a Saturday or Sunday that is normally off). A plain dict means no working days."""

    working = {}


def day_kind(day, holiday_dates):
    """WORKDAY, WEEKEND (an off day) or HOLIDAY. A holiday wins over everything else.
    Monday–Friday and the WORKING_SATURDAYS are workdays; other Saturdays and every Sunday are off, unless the
    date was opened as a working day (`holiday_dates.working`)."""
    if day in holiday_dates:
        return HOLIDAY
    if is_weekend_off(day) and day not in getattr(holiday_dates, "working", ()):
        return WEEKEND
    return WORKDAY


def month_end(day):
    return date(day.year, day.month, calendar.monthrange(day.year, day.month)[1])


def is_locked(day, today):
    """A month locks once its last day has passed (works for 28/29/30/31-day months)."""
    return today > month_end(day)


def week_start(day):
    return day - timedelta(days=day.weekday())


def week_days(monday):
    return [monday + timedelta(days=i) for i in range(7)]


def validate_day_hours(values):
    """values: iterable of raw inputs -> (ints, error). Whole numbers >= 0, day total <= 24."""
    clean = []
    for raw in values:
        text = str(raw).strip() if raw is not None else ""
        if text == "":
            clean.append(0)
            continue
        if not text.isdigit():
            return None, "Hours must be whole numbers of 0 or more (no decimals, no negatives)."
        clean.append(int(text))
    if sum(clean) > MAX_DAY_HOURS:
        return None, f"has {sum(clean)} hours; a person can log at most {MAX_DAY_HOURS} hours a day across all projects."
    return clean, None
