# Timesheet — design notes

Code: `apps/timesheet/` · pages under `/timesheet/` · sidebar item **Timesheet**.
The app only **reads** existing data (projects, project members, managers, logins, page visits);
everything it writes goes to its own `timesheet_*` tables.

## 1. Database schema

| Table | Key fields | Notes |
|---|---|---|
| `timesheet_holiday` | `date` (unique), `name`, `created_by`, timestamps | Duplicate date rejected (DB unique + form check) |
| `timesheet_timesheetprofile` | `user` (1:1), `joining_date`, `last_working_day`, `remainder_project` (FK project) | Created on first use; joining date defaults to the account's `date_joined` |
| `timesheet_attendance` | `user`, `date` (unique together), `status` present/absent, `first_login`, `last_logout`, `overridden`, `override_by`, `override_reason` | |
| `timesheet_timesheetday` | `user`, `date` (unique together), `status` draft/submitted, `auto_filled`, `edited`, `updated_by`, `updated_at` | `edited` = a person changed it; auto-fill never overwrites it |
| `timesheet_timesheetentry` | `day` (FK), `project` (FK), `hours` (whole number ≥ 0) | Unique per day + project |
| `timesheet_projectstatuslog` | `project`, `old_status`, `new_status`, `changed_at`, `changed_by` | Written on every project status change; drives the completion / reactivation date rules |
| `timesheet_timesheetunlock` | `user`, `month`, `active`, `unlocked_by` | Admin reopens a locked month for one person |
| `timesheet_timesheetaudit` | `actor`, `target_user`, `entity`, `ref`, `old_value`, `new_value`, `after_lock`, `created_at` | Hours, draft/submit, attendance, holidays, project status, unlocks, month locks, settings |
| `timesheet_jobrun` | `key` (unique), `ran_at` | Makes each scheduled job run exactly once; `timesheet:start` = go-live date |

Relationships: every table links to `accounts_user`; entries, status logs and the remainder project link to `projects_project`.

## 2. Endpoints and access

| URL | Method | Who |
|---|---|---|
| `/timesheet/` `?user=&week=` | GET, POST (save draft / submit week) | Own timesheet; Reporting Manager for their team; Admin for anyone |
| `/timesheet/calendar/` `?user=&month=` | GET | Same as above |
| `/timesheet/holidays/` `?year=` | GET everyone · POST add = Admin / Reporting Manager, else **403** | |
| `/timesheet/holidays/<id>/edit/` · `/delete/` | POST | Admin / Reporting Manager, else **403** |
| `/timesheet/attendance/` `?date=` | GET, POST override | Admin (everyone) / Reporting Manager (team); never your own; else **403** |
| `/timesheet/reports/` `?week=&user=&project=&format=xlsx\|pdf` | GET | Admin all · RM team + self · Project Lead only their projects · Employee self |
| `/timesheet/audit/` | GET | Admin all · RM their team + holidays |
| `/timesheet/people/<id>/settings/` | POST remainder project (Admin/RM) · joining / last day, unlock (Admin) | else **403** |

Role mapping (no new setup needed):
- **Admin** = superuser or the `admin` role.
- **Reporting Manager** = anyone who has people reporting to them (People → Edit → Manager), or the `reporting-manager` role. "Their team" = everyone below them in the reporting line.
- **Project Lead** = the project's **Owner**.
- **Employee** = everyone else.

## 3. Screens

1. **Timesheet** (week): rows = projects, columns = Mon–Sun with Weekend/Holiday/Locked tags and an attendance row (Present + first login–last logout, Absent, override). Whole-hour inputs (max 8 per day in total across projects), live day/week totals, **Save draft** / **Submit week**. Managers/admins get a person picker and a settings panel (remainder project, joining / last working day, unlock month).
2. **Calendar**: month grid per person, weekends and holidays highlighted, hours, attendance, submitted, lock. Click a day to open its week.
3. **Holidays**: year list; add / edit / delete for Admin and Reporting Manager only.
4. **Attendance**: one day, everyone in your team, status, first login, last logout, override with reason.
5. **Reports**: weekly summary (total, absent days, submitted days, split per month when the week spans two) and hours per employee × project × day; Excel and PDF downloads.
6. **Audit log**: filterable by type; shows "after lock" edits.

## 4. Auto-fill algorithm

`apps/timesheet/rules.py → split_hours()`:

```
N = eligible projects (assigned on that day, internal side, not archived, not Completed that day)
share = 8 // N ; remainder = 8 - share * N
each project gets share; remainder goes to the chosen remainder project if eligible, else the first
eligible project (earliest assignment). N = 0 -> nothing, with a warning on the page.
```

When a day is filled (`services.ensure_day`):
- **Workday**: only if Present — logged in, or used Planwise, any time that day **up to 18:30** (settings `TIMESHEET_WORK_START` / `TIMESHEET_WORK_END`), or overridden to Present. No login in that window → Absent → no hours.
- **Saturday / Sunday / Holiday**: always 8 hours (a holiday on a weekend is still 8, not 16), no attendance check.
- Nothing before the joining date, after the last working day, or before the go-live date.
- A day someone edited or submitted is never overwritten; a locked month is never auto-changed.
- A finished day is recomputed only when a holiday is added/removed for it or its attendance is overridden.

Completion rule: a project counts on a day unless it was Completed for the whole day — the completion day itself still counts, and a reactivated project counts from its reactivation day.

## 5. Scheduled jobs

Run by the existing worker container (`process_queue --loop` calls `run_scheduled_jobs()` once a minute; errors are logged and never stop the worker). Manual run: `python manage.py timesheet_jobs`.

| Job | When | What |
|---|---|---|
| Absent check | Every working day at 18:30 | Anyone not seen any time that day up to 18:30 → Absent (no hours) |
| End of day | Each finished day (catches up 14 days after downtime) | Fills weekends, holidays and present days; records any day not yet decided |
| Weekly report | Monday from 08:00 | In-app + push notification to every Reporting Manager, linking to last Monday–Sunday's report |
| Month-end lock | 1st of the month | Lock is date-based (after the last day of the month); the job records it in the audit log |

## 6. Assumptions (please confirm)

1. **Eligible project statuses**: Planwise uses Lead / Proposal Submitted / Active / Under AMC / Completed. Only **Completed** is excluded (the spec's rule taken literally). Spec "Inactive" has no Planwise equivalent.
2. **Assigned** = the person is on the project's People list on **our organisation** side; client-side people and client logins get no timesheet.
3. **Roles** are mapped as in section 2, using existing manager and owner fields.
4. **Weekly report delivery** = Planwise notification + browser push (no email server is configured). The report page has Excel and PDF downloads.
5. **Present** = logged in, or opened any Planwise page, any time that day up to 18:30 that day. A login only after 18:30 does not count. **Last logout** is recorded when they click Sign out; it stays blank if they just close the browser.
6. **Go-live**: days before the timesheet was first switched on are not auto-filled or marked absent.
7. **Future days** can be typed in ahead of time; auto-fill happens when the day arrives.
8. **Concurrent edits**: last save wins, and the second person sees a warning naming who changed it.
9. **Submitted** days can still be edited until the lock; editing returns them to Draft.
10. **Unlock** is admin-only and per person per month.

## 7. Change after review

- **Daily limit is 8 hours per person across all projects** (the spec said 24). The auto-fill still splits 8 hours
  equally; a person may move hours between projects or log fewer, but a day can never total more than 8 —
  for employees, managers and admins alike. The page shows the problem while typing and disables Save / Submit.

- **Attendance window now starts at midnight** (setting `TIMESHEET_WORK_START=00:00`): any login or Planwise use on a
  working day before 18:30 — including before 10:00 — makes the day Present. Not seen by 18:30 → Absent.
