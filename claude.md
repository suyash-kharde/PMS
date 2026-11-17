# Planwise â€” work session notes (23â€“29 Sep 2026)

A record of everything discussed and changed in this chat: what was asked, what was built, where the code is,
and what is still open. Project folder: `/home/pms/PMS_DEV` Â· live site: https://planwise.augtrans.com

> **Most changes only take effect after a restart** (the server runs with `DEBUG=0` and caches code and templates):
>
> ```bash
> cd /home/pms/PMS_DEV && docker compose -f docker-compose.prod.yml restart web worker
> ```
>
> The restart also runs any pending database migrations. After it, hard-refresh the browser (Ctrl+Shift+R).

---

## 1. DBeaver "Connection refused" (answered, no code change)

- **Cause:** the database container `pms_db` is published only on `127.0.0.1:5432` in
  `docker-compose.prod.yml` (line 45: `"127.0.0.1:${POSTGRES_PORT:-5432}:5432"`), so it is not reachable from
  another computer.
- **Options given:** use DBeaver's SSH tunnel (host `172.16.16.214`, then DB host `localhost`), or change the
  port line to `"${POSTGRES_PORT:-5432}:5432"` and recreate the `db` container (exposes the DB to the network).
- Project status values live in table **`projects_project`**, column **`status`**.

## 2. Recreating a UI from a ticket image (explained)

- Claude does receive ticket screenshots (`get_ticket` in `apps/mcp/tools.py` sends them as images).
- Suggested Penpot approach: place the screenshot as a locked background, measure it (colours, text, boxes), build
  layers on top, export and compare pixel-by-pixel, fix and repeat until the match score is high.

## 3. Project statuses

- **Now:** `Lead â†’ Proposal Submitted â†’ Active â†’ Under AMC â†’ Completed` (keys `lead`, `proposal`, `active`,
  `under_amc`, `completed`). Default for new projects: **Lead**.
- Old statuses (Planning / On hold / Cancelled) were removed. Migration `projects/0009` moved the one remaining
  project (VMS, on Planning) to **Lead**.
- Bulk import defaults unknown statuses to Lead.
- Files: `apps/projects/models.py`, migrations `projects/0007`, `0009`, `apps/bulk/services/validate.py`,
  `apps/bulk/services/specs.py`, badge colours in `static/css/main.css`.

## 4. Purchase order (PO) on projects

- Choosing **Active** on the project form opens a popup for **PO number** and **PO document** (both optional).
- Edit later on the project's **Edit** page â†’ **Edit PO details**. PO shows on the project page as tags; the file
  downloads only for people who can see the project (`/projects/<id>/po/`).
- Files are stored under `media/projects/<id>/po/`.
- Files: `apps/projects/models.py` (`po_number`, `po_document`), migration `projects/0008`, `apps/projects/views.py`
  (`project_po_download`), `templates/projects/project_form.html`, `project_detail.html`.

## 5. Project cards â€” stage stepper

- The Projects page cards show a **stepper** (design 1): ticks for done stages, a ring for the current one,
  grey for upcoming, plus the task-progress %.
- Steps come **from the status list itself** (`PROJECT_STATUS`), so adding/renaming a status updates the stepper.
- Files: `apps/projects/models.py` (`status_track`), `templates/projects/project_list.html`, CSS.

## 6. People page â€” time on Planwise

- Each person card shows **Tasks** and **Worked today** (e.g. `2h 15m`).
- Rule: page loads up to **5 minutes** apart count as continuous work; longer gaps are breaks. Resets at midnight
  (Asia/Kolkata). Setting: `ACTIVE_GAP_SECONDS` in `apps/accounts/middleware.py`.
- Each person's page has **Work on <day>**: pages opened and actions taken, grouped by project, with a date picker.
  Page visits are kept for 90 days.
- Files: `apps/accounts/models.py` (`UserDayActivity`, `PageVisit`), migrations `accounts/0005`, `0006`,
  `apps/accounts/middleware.py`, `apps/accounts/views.py`, `templates/accounts/user_list.html`, `user_detail.html`.

## 7. Documentation page

- Section **02 Managing passwords** now also covers: sign-up with your own password, password rules
  (min 6 characters, not a common password), changing your own password, and the show-password eye button.
- Noted gap (not changed): passwords typed by an admin on New user / Edit are **not** checked against the rules.
- File: `templates/core/docs.html`.

## 8. Timesheet (new sidebar item)

New, self-contained app `apps/timesheet/` at `/timesheet/`. It only **reads** existing data; everything it writes
goes to its own tables. Design notes: `dev_guid/timesheet/TIMESHEET.md` Â· test sheet:
`dev_guid/timesheet/test_cases.xlsx`.

### Rules as they stand now

| Rule | Behaviour |
|---|---|
| Daily hours | **8 hours per person per day**, auto-filled and split equally across assigned, non-Completed projects (remainder to the first project) |
| Maximum | **At most 8 hours a day in total** across all projects (changed from the spec's 24). Over 8 is refused; the page warns and disables Save/Submit |
| Whole numbers | Yes, no decimals, no negatives |
| Weekends & holidays | **8 hours by default**, split the same way, no login needed â€” also for past days before go-live and for upcoming holidays |
| Attendance window | **10:00 â€“ 18:30** (settings `TIMESHEET_WORK_START` / `TIMESHEET_WORK_END`) |
| Present | Logged in or used Planwise inside the window â†’ Present at once, hours filled |
| Absent | On a working day, anyone **not logged in yet shows as Absent**; at 18:30 it becomes final. Absent days get no hours |
| First login | The **first visit of each day** is saved as first login (even if the person was already signed in). Last logout = when they click Sign out |
| Day | Midnight to midnight, Asia/Kolkata |
| Working days before go-live | Not tracked (no login records exist); type hours manually if needed |
| Lock | A month locks after its last day. Employees can't edit; admins and reporting managers can |
| Draft / Submit | Save draft, then Submit week; editing a submitted day returns it to Draft |
| Holidays | Only admins and reporting managers can add / edit / delete (others get 403); duplicates refused |
| Roles | Admin = Administrator role Â· Reporting manager = has people reporting to them Â· Project lead = project owner |
| Audit | Changes are still recorded in the background (hours, attendance overrides, holidays, locks); the Audit log page was removed |

### Tabs

- **Timesheet** â€” one person's week: rows = projects, Monâ€“Sun boxes, reset button per row, Save draft / Submit week.
  Admins also see three plain cards: **Present today**, **Absent today**, **Fully submitted this week**
  (Present/Absent open a name list with a name filter).
- **People** ("Team timesheet", admins and reporting managers) â€” one row per person, hours per day, total,
  submitted bar, filters (date, person, project, status, search), 15 per page.
- **Calendar** â€” month grid with purple hour pills, amber holidays, weekends shaded, today outlined.
- **Holidays** â€” add bar + table with inline Edit and Delete; shows this year onwards.
- **Attendance** (admins and reporting managers) â€” one day: Present / Absent / Total cards, first login, last
  logout, override with reason, â‹® menu.
- **Reports** â€” weekly report per employee Ã— project Ã— day, with **Excel** and **PDF** downloads.

### Scheduled jobs

Run inside the existing worker (`process_queue --loop` calls them once a minute; errors never stop the worker):
18:30 absent check, end-of-day fill, Monday 08:00 weekly report notification to reporting managers, month-end
lock record. Manual run: `python manage.py timesheet_jobs`.

### Removed at your request

Audit log tab/page Â· Timesheet settings panel (leftover hours, employment dates, unlock) Â· People-tab summary cards,
subtitle and colour legend Â· Working today card Â· extra parts of the Present/Absent list pages.

### Files

`apps/timesheet/` (models, rules, services, permissions, views, urls, exports, signals, middleware, tests,
migration `0001_initial`, command `timesheet_jobs`), `templates/timesheet/*.html`, `ts-` styles at the end of
`static/css/main.css`, one sidebar line in `templates/base.html`, app + middleware in `config/settings.py`,
URL include in `config/urls.py`, a guarded hook in `apps/intake/management/commands/process_queue.py`.

## 9. Error pages

- New friendly pages instead of the plain "Server Error (500)": `templates/500.html`, `404.html`, `403.html`
  (Go back / Try again / Go to dashboard).
- The two 500s seen during the session:
  1. **page 3 of Team timesheet** â€” the server was running a mix of old and new code (changed after a restart);
     fixed by restarting.
  2. **Absent today** â€” a real bug: people with **no role** crashed the name/role line. Fixed in
     `people_today.html` and `people.html`, with a regression test.

## 10. Default role = Member

- Staff accounts saved without a role now get **Member** (People form, bulk import, project "create person",
  sign-up). Client-side logins (linked to a customer) are left without a role.
- Migration `accounts/0007` gave the one role-less user (**surjeet**) the Member role.
- File: `apps/accounts/models.py` (`User.save`).

## 11. Other outputs

- **Project list** exported to Excel: `media/exports/Planwise_Projects.xlsx`
  (https://planwise.augtrans.com/media/exports/Planwise_Projects.xlsx, login required). One-time snapshot.
- Project **P01 â€“ Project01** was deleted by someone between the two exports.

---

## Checks done

- Timesheet test suite: **44 tests, all passing** (`apps/timesheet/tests.py`).
- Other apps' existing tests give the same results with and without the timesheet (their few failures pre-date
  this work: an audit-log foreign-key issue under the SQLite test setup).
- All migrations apply cleanly in order (`projects 0007â€“0009`, `accounts 0005â€“0007`, `timesheet 0001`).
- Every timesheet page was loaded against the **live database** (read-only, all changes rolled back) as an admin
  and as an employee.

## Open items / to decide

1. **Restart** the app so all of the above goes live (command at the top).
2. The Excel project export is a snapshot; a **Download Excel** button on the Projects page was offered.
3. Admin-set passwords are not validated against the password rules (offered to enforce).
4. A late login after 18:30 keeps the day **Absent** â€” confirm, or change so any login makes it Present.
5. The row **reset** button sets hours to 0; it could instead restore the automatic 8-hour split.
6. Many earlier changes in the project folder are **uncommitted** in git; commit when ready.
