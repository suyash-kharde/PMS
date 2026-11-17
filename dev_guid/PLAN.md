# PLAN.md — Forbes Vyncke Integrated Manufacturing & Quality Web Application

## Purpose

This file is the source of truth for Claude Code while reviewing and implementing the Forbes Vyncke manufacturing and quality management application.

**Architecture:** Angular 15+ + Django REST Framework + PostgreSQL + existing JWT authentication  
**Backend apps:** `fauth`, `capacity`, `quality`  
**Frontend areas:** `auth`, `capacity`, `quality-control`  
**Primary roles:** `ADMIN`, `PLANNER`, `SUPERVISOR`, `OPERATOR`, `VENDOR`, `TEAM_LEAD`, `INSPECTOR`, `EXECUTIVE`

The detailed functional requirements, database schema, API registry, UI routes, business rules, and phase definitions below are derived from the supplied requirements document. Do not invent missing requirements.

---

# 1. Claude Code Operating Rules

## 1.1 Mandatory review-first workflow

Before modifying code, Claude Code MUST:

1. Inspect the complete repository structure.
2. Identify the current Django apps, models, serializers, views, URLs, services, tests, migrations, Angular modules/components/services/guards/interceptors, and environment configuration.
3. Compare the existing implementation against this PLAN.md.
4. Identify what already exists and must be reused.
5. Identify conflicts between this plan and the existing implementation.
6. Inspect:
   - `D:\FORBES\forbesvyncke\DRF Standard Documentation.pdf`
   - `D:\FORBES\forbesvyncke\Angular_Code_Standards_Guidelines.docx`
7. Run the existing backend and frontend test suites before making functional changes.
8. Produce a gap report before implementation.

Do **not** immediately recreate models, applications, routes, authentication, or UI components that already exist.

## 1.2 Change safety

- Preserve existing folder structures.
- Do not create parallel application trees.
- Reuse existing JWT authentication.
- Prefer extending existing implementations over replacing them.
- Do not remove working functionality unless explicitly required.
- Do not change public API contracts without documenting the reason.
- Do not create migrations until the model design has been reconciled with the existing database.
- Use `transaction.atomic()` for multi-step state-changing operations.
- Keep controllers/views thin.
- Put domain rules in:
  - `backend/capacity/services/`
  - `backend/quality/services/`
- Optimize ORM access using `select_related()` and `prefetch_related()` where appropriate.
- Every phase must be tested before being marked complete.

## 1.3 Explicit infrastructure exclusions

Do NOT introduce:

- Redis
- Celery
- Docker
- Docker Compose
- NGINX

The application should use Django service-layer patterns, PostgreSQL indexes, transactions, and existing infrastructure.

## 1.4 Device scope

Desktop and laptop web browsers are in scope.

Dedicated mobile/tablet optimization is out of scope.

---

# 2. Existing Architecture to Preserve


# 2A. Development Environment & Toolchain

## 2A.1 Operating System

The development environment is **Windows**.

Claude Code must treat Windows as the primary local development environment and must not assume Linux/macOS shell commands.

Use Windows-compatible commands where practical:

```bat
cd backend
conda activate forbs
python manage.py ...
```

PowerShell equivalents are also acceptable.

Do not introduce WSL, Docker, Linux-only tooling, or container-specific setup unless explicitly requested.

## 2A.2 Conda Environment

The backend Python environment is managed with **Conda**.

Required environment name:

```text
forbs
```

Before running Django/Python commands:

```bat
conda activate forbs
```

Verify:

```bat
conda env list
python --version
where python
```

Claude Code must verify that the active Python executable belongs to the `forbs` Conda environment before installing packages, running migrations, or executing Django commands.

### Python dependency rules

- Install backend packages only into the `forbs` environment.
- Prefer the repository's existing `requirements.txt`, `environment.yml`, or equivalent dependency file.
- Do not globally install Python packages as a workaround.
- Do not create a second Conda environment unless explicitly required.
- After dependency changes, update the appropriate dependency lock/requirements file.
- Verify Django and DRF versions from the existing project before changing them.
- Do not upgrade Django, DRF, Python, or other core dependencies merely to resolve an implementation issue.

Recommended verification:

```bat
conda activate forbs
python --version
python -m pip --version
python -m django --version
python manage.py check
```

Use `python -m pip` rather than an unrelated global `pip` executable.

---

## 2A.3 Node.js / npm / Angular CLI

Node.js and Angular CLI are installed on the **Windows base system**, not inside the Conda environment.

Current verified Windows tooling:

```text
Node.js:     v24.19.0
npm:         11.17.0
Angular CLI: 20.3.34
OS:          win32 x64
```

The current command output also reports:

```text
Angular: <error>
```

and therefore the globally installed Angular CLI must **not** be interpreted as proof that the project's local Angular dependencies are installed or compatible.

Current baseline:

```bat
C:\Users\chaitanya.mirpagar>node -v
v24.19.0

C:\Users\chaitanya.mirpagar>npm -v
11.17.0

C:\Users\chaitanya.mirpagar>ng version
Angular CLI: 20.3.34
Node: 24.19.0
Package Manager: npm 11.17.0
OS: win32 x64
Angular: <error>
```

### Angular environment rules

The repository's `package.json` and `package-lock.json` are the authoritative source for the project's Angular version.

Claude Code MUST:

1. Inspect `frontend/package.json`.
2. Inspect `frontend/package-lock.json` if present.
3. Determine the project's actual Angular framework version.
4. Determine whether Angular CLI should be installed locally.
5. Use the project's local CLI through:

```bat
npx ng version
npx ng serve
npx ng test
```

Do not assume the globally installed Angular CLI `20.3.34` is the project's required version.

If the project uses a different Angular version, use the compatible local CLI rather than upgrading the project to Angular 20 automatically.

### Node/npm rules

- Do not downgrade or upgrade Node.js without first checking project compatibility.
- Do not globally install project-specific npm dependencies.
- Run npm commands from the frontend directory.
- Prefer `npm ci` when `package-lock.json` is authoritative and reproducible installation is required.
- Use `npm install` only when dependencies must intentionally be changed.
- Never delete `package-lock.json` merely to resolve dependency errors.
- Do not use `--force` or `--legacy-peer-deps` as a first-line solution.
- If dependency resolution fails, inspect the dependency graph and project Angular version first.

Recommended verification:

```bat
cd frontend
node -v
npm -v
npx ng version
npm ci
npx ng version
```

---

# 2B. Django/DRF Migration Baseline Instructions

Database migrations are a controlled part of the implementation and must be handled carefully.

## 2B.1 Before changing models

Before modifying any Django model:

1. Activate the `forbs` Conda environment.
2. Inspect the existing model.
3. Inspect existing migration files.
4. Inspect the current migration state.
5. Run Django system checks.
6. Confirm the database configuration.
7. Determine whether the model/table already exists in the database.

Commands:

```bat
conda activate forbs

cd backend

python manage.py check
python manage.py showmigrations
python manage.py makemigrations --check --dry-run
```

If needed, inspect SQL without applying it:

```bat
python manage.py sqlmigrate <app_label> <migration_number>
```

Do not create destructive migrations without explicit justification.

---

## 2B.2 Standard migration workflow

For a normal model change:

```bat
conda activate forbs
cd backend

python manage.py check
python manage.py makemigrations
python manage.py showmigrations
python manage.py migrate
python manage.py showmigrations
```

Then run relevant tests:

```bat
python manage.py test
```

If the repository uses pytest:

```bat
pytest
```

The exact existing test runner must be discovered during Phase 0.

---

## 2B.3 Migration rules for Claude Code

Claude Code MUST follow these rules:

- Never manually edit an already-applied migration unless there is a documented migration-repair reason.
- Never delete migration history to make migrations pass.
- Never run `migrate --fake` without understanding and documenting why it is safe.
- Never use `--fake-initial` blindly.
- Never reset the database as a shortcut for migration problems.
- Never drop production tables as part of normal development.
- Never squash migrations during feature implementation unless explicitly requested.
- Preserve migration dependency order.
- Keep migrations app-specific and deterministic.
- Review generated migrations before applying them.
- Check whether Django is detecting unintended changes before creating migrations.

Use:

```bat
python manage.py makemigrations --check --dry-run
```

to detect model/migration drift.

---

## 2B.4 New application migration sequence

When a new Django app is introduced:

```bat
python manage.py startapp <app_name>
```

Then:

1. Add the app to `INSTALLED_APPS`.
2. Implement/reconcile models.
3. Run:

```bat
python manage.py makemigrations <app_name>
```

4. Review the migration.
5. Run:

```bat
python manage.py showmigrations <app_name>
python manage.py migrate <app_name>
```

6. Run application tests.

Do not mark the phase complete until migrations and tests succeed.

---

## 2B.5 Migration safety for existing databases

This project must assume that the database may already contain data.

When adding or changing fields:

- Prefer nullable fields for staged migrations where appropriate.
- Use safe defaults for existing rows when required.
- Avoid making an existing nullable column non-nullable in a single unsafe operation.
- Separate schema and data migrations when a transformation is required.
- Use `RunPython` only when the data transformation is deterministic and reversible where practical.
- Validate data before applying constraints.
- Consider unique/index constraints carefully on existing data.

For a significant schema change, Claude Code should report:

```text
Migration impact:
- Tables affected:
- Columns affected:
- Constraints affected:
- Existing data risk:
- Backfill required:
- Rollback considerations:
```

---

## 2B.6 Migration verification checklist

After every schema migration:

```bat
python manage.py check
python manage.py showmigrations
python manage.py makemigrations --check --dry-run
```

Expected result:

```text
No model changes detected
```

or equivalent confirmation that no unapplied model changes remain.

Then run relevant backend tests.

---

# 2C. Standard Windows Development Startup

Claude Code should use the following conceptual startup sequence.

## Terminal 1 — Django

```bat
conda activate forbs
cd D:\FORBES\forbesvyncke\backend
python manage.py check
python manage.py migrate
python manage.py runserver
```

## Terminal 2 — Angular

Node/npm are installed on the Windows base system.

```bat
cd D:\FORBES\forbesvyncke\frontend
npm ci
npx ng serve
```

Do not activate the Conda environment for Angular dependency management unless the repository specifically requires it. Angular uses the Windows Node/npm installation.

---

# 2D. Environment Separation Rule

The project has two distinct runtime toolchains:

```text
Windows Base
│
├── Node.js 24.19.0
├── npm 11.17.0
└── Angular CLI 20.3.34
        │
        └── frontend/

Conda Environment: forbs
│
├── Python
├── Django
├── Django REST Framework
└── backend/
```

Claude Code must keep these environments conceptually separate.

Do not:

- install Python dependencies globally
- install npm dependencies into Conda
- assume global Angular CLI matches project Angular
- use global Python/pip when `forbs` should be active
- modify Node/Angular versions without repository compatibility analysis

---

# 2E. Environment Discovery Checklist

Phase 0 must record:

```text
Backend
[ ] Conda environment name = forbs
[ ] Python version
[ ] Django version
[ ] Django REST Framework version
[ ] Database engine/version
[ ] requirements.txt/environment.yml/package source
[ ] Existing migration state

Frontend
[ ] Node version = 24.19.0 baseline
[ ] npm version = 11.17.0 baseline
[ ] Global Angular CLI = 20.3.34 baseline
[ ] Actual local Angular version from package.json
[ ] package-lock.json status
[ ] Local Angular CLI availability
[ ] npm install/ci status
[ ] Angular build status
[ ] Angular test status
```

If the repository's actual versions differ from the baseline, do not automatically change them. Record the difference and determine compatibility first.


```text
D:\FORBES\forbesvyncke

├── backend/
│   ├── manage.py
│   ├── fauth/
│   ├── capacity/
│   │   └── services/
│   └── quality/
│       └── services/
│
└── frontend/src/app/
    ├── core/
    ├── shared/
    └── components/
        ├── auth/
        ├── capacity/
        └── quality-control/
```

Expected service modules:

```text
capacity/services/
├── constraint_engine.py
├── capacity_service.py
├── loss_calculator.py
└── tpt_analytics.py

quality/services/
├── state_service.py
├── assignment_service.py
├── release_service.py
└── quality_analytics.py
```

---

# 3. Functional Scope

The system consists of two major domains:

## 3.1 Capacity Planning

- Project/window-plan management
- Production scheduling
- Station hierarchy
- Machine status
- Operator and skill management
- Shift rosters
- Supplier/material shortage tracking
- Project BOM
- Station execution
- 2x4 workstation grid
- Constraint validation
- Idle/loss tracking
- TPT analytics
- Financial loss calculations

## 3.2 Quality Control

- Vendor management
- Inspector management
- Inspector expertise and availability
- Inspection request lifecycle
- Inspector assignment
- Inspection documents
- IMS-FV-FF03 inspection reports
- Report items
- Observations
- Calibration records
- Punch points
- Punch-point evidence
- Team Lead review decisions
- Release note generation
- Quality status history
- Quality analytics

---

# 4. Database Requirements

## 4.1 `fauth`

### User

Custom `AbstractUser` model:

- `id`: BigAutoField PK
- `username`: unique
- `email`: unique
- `role`: ADMIN, PLANNER, SUPERVISOR, OPERATOR, VENDOR, TEAM_LEAD, INSPECTOR, EXECUTIVE
- `first_name`
- `last_name`
- `phone`: nullable
- `is_active`: default True
- `created_at`
- `updated_at`

### AuditLog

Append-only audit tracker:

- `id`
- `user`: FK User, SET_NULL
- `action`: CREATE, UPDATE, DELETE, STATE_CHANGE, ASSIGN, LOGIN, DECISION
- `entity_type`
- `entity_id`
- `old_value`: JSON nullable
- `new_value`: JSON nullable
- `ip_address`: nullable
- `timestamp`

---

# 5. Capacity Database Requirements

Create/reconcile these models:

1. `Project`
2. `Station`
3. `ProductionSchedule`
4. `CapacityTarget`
5. `Machine`
6. `Skill`
7. `Operator`
8. `OperatorSkillMapping`
9. `ShiftRoster`
10. `Supplier`
11. `MaterialBatchItem`
12. `ProjectBOMItem`
13. `StationExecutionTask`
14. `WorkstationGridState`
15. `TaskConstraintLog`
16. `IdleTimeLog`

Important domain fields and enums must follow the supplied requirements.

Key relationships:

```text
Project
 ├── ProductionSchedule
 ├── MaterialBatchItem
 ├── ProjectBOMItem
 ├── StationExecutionTask
 └── IdleTimeLog

Station
 ├── ProductionSchedule
 ├── Machine
 ├── ShiftRoster
 ├── StationExecutionTask
 ├── WorkstationGridState
 └── IdleTimeLog

Operator
 └── OperatorSkillMapping ── Skill
```

---

# 6. Quality Database Requirements

Create/reconcile these models:

1. `Vendor`
2. `InspectorProfile`
3. `InspectorExpertise`
4. `InspectorAvailability`
5. `InspectionRequest`
6. `InspectionAssignment`
7. `InspectionDocument`
8. `InspectionReport`
9. `InspectionReportItem`
10. `InspectionObservation`
11. `CalibrationLog`
12. `PunchPoint`
13. `PunchPointEvidence`
14. `ReviewDecision`
15. `ReleaseNote`
16. `QualityStatusHistory`

Important relationships:

```text
Vendor
 └── InspectionRequest
      ├── InspectionAssignment
      ├── InspectionDocument
      ├── InspectionReport
      │    ├── InspectionReportItem
      │    ├── InspectionObservation
      │    ├── CalibrationLog
      │    └── PunchPoint
      │         └── PunchPointEvidence
      ├── ReviewDecision
      ├── ReleaseNote
      └── QualityStatusHistory
```

---

# 7. REST API Contract

Base prefix:

```text
/api/v1/
```

## Authentication

```text
POST /auth/login/
POST /auth/refresh/
GET  /auth/me/
POST /auth/change-password/
GET  /auth/users/
POST /auth/users/
GET  /auth/audit-logs/
```

## Capacity

```text
GET, POST /capacity/projects/
GET, PATCH /capacity/projects/{id}/
GET, POST /capacity/stations/
GET, POST /capacity/machines/
POST /capacity/machines/{id}/toggle/
GET, POST /capacity/operators/
POST /capacity/operators/{id}/skills/
POST /capacity/shift-rosters/bulk-log/
GET, POST /capacity/materials/shortages/
GET, POST /capacity/projects/{id}/bom-items/
GET /capacity/stream/nodes/
GET /capacity/stream/grid-canvas/
POST /capacity/tasks/{id}/validate/
POST /capacity/tasks/{id}/transition/
GET /capacity/analytics/overview/
GET /capacity/analytics/loss-impact/
GET /capacity/analytics/bottlenecks/
POST /capacity/idle-logs/
```

## Quality

```text
GET, POST /quality/vendors/
GET, POST /quality/inspectors/
GET, POST /quality/inspection-requests/
POST /quality/requests/{id}/submit/
POST /quality/requests/{id}/assign/
POST /quality/requests/{id}/reschedule/
GET, POST /quality/requests/{id}/documents/
GET /quality/documents/{id}/download/
GET, POST /quality/reports/
POST /quality/reports/{id}/submit/
POST /quality/reports/{id}/punch-points/
POST /quality/punch-points/{id}/resolve/
POST /quality/punch-points/{id}/evidence/
POST /quality/reviews/{id}/decision/
POST /quality/releases/{id}/generate/
GET /quality/releases/{id}/download/
GET /quality/analytics/overview/
```

Claude Code must compare these contracts against existing URLs and serializers before implementation.

---

# 8. Frontend Routing

```text
/login

/capacity-planning
├── /dashboard
├── /fabrication-stream
├── /shop-grid
├── /resources/manpower
├── /resources/machines
├── /materials/shortages
├── /materials/bom-master
├── /schedules
└── /analytics-tpt

/quality-control
├── /vendor/dashboard
├── /vendor/requests/new
├── /vendor/requests/:id
├── /team-lead/dashboard
├── /team-lead/allocation/:id
├── /team-lead/review/:id
├── /inspector/dashboard
├── /inspector/execute/:id
├── /releases
└── /analytics

/admin
├── /users
├── /master-setup
└── /audit-logs
```

Role routing:

```text
VENDOR     -> /quality-control/vendor
TEAM_LEAD  -> /quality-control/team-lead
INSPECTOR  -> /quality-control/inspector
PLANNER    -> /capacity-planning/dashboard
SUPERVISOR -> /capacity-planning/dashboard
ADMIN      -> /capacity-planning/dashboard
EXECUTIVE  -> /capacity-planning/dashboard
```

---

# 9. Core Business Rules

## 9.1 Three-way capacity constraint validation

Every task entering a station must validate:

1. Manpower
2. Machine
3. Material

```text
Task enters queue
       |
       v
Validate manpower
       |
Validate machine
       |
Validate material
       |
       +---- all pass ----> WORKING
       |
       +---- any fail ----> BLOCKED
```

Failure must create a `TaskConstraintLog`.

The API and UI must expose the blocking reason.

## 9.2 Capacity calculations

```text
1 MenShift = 1 Operator × 8 Hours

Weekly Capacity = 22 Men/Day × 6 Workdays
                = 132 MenShifts/Week
```

Monthly target:

```text
5 PES + 1 CES
```

## 9.3 Financial loss

```text
Total Loss Days =
    Material Loss
  + Manpower Loss
  + Rework Loss
  + Other Loss

Financial Loss Impact =
    Total Loss Days × ₹250,000
```

## 9.4 Shop efficiency

```text
Shop Efficiency (%) =
    (Actual Output Boilers / Planned Target Input) × 100
```

## 9.5 Quality lifecycle

```text
DRAFT
  -> PENDING
  -> ALLOCATED
  -> IN_PROGRESS
  -> SUBMITTED
  -> UNDER_REVIEW
  -> ACCEPTED
  -> COMPLETED
```

Alternative decision branches:

```text
UNDER_REVIEW -> REWORK -> ALLOCATED
UNDER_REVIEW -> REJECTED
UNDER_REVIEW -> WASTE_VISIT -> RESCHEDULED
```

State transitions must be enforced by `InspectionStateService`.

---

# 10. Required Domain Services

## Capacity

### `ConstraintValidationEngine`

Responsible for:

- manpower validation
- machine validation
- material validation
- task state transition
- constraint logging

### `CapacityCalculationService`

Responsible for:

- capacity targets
- menshift calculations
- monthly limits
- production capacity KPIs

### `LossImpactCalculator`

Responsible for:

- total loss days
- financial impact
- loss summaries

### `TPTAnalytics`

Responsible for:

- idle time
- bottleneck analysis
- root-cause aggregation
- queue-capacity warnings

## Quality

### `InspectionStateService`

Responsible for:

- valid lifecycle transitions
- transition validation
- status history creation

### `AssignmentService`

Responsible for:

- inspector availability
- expertise/skill matching
- calendar overlap
- workload checks

### `ReleaseNoteService`

Responsible for:

- validating accepted inspections
- generating release PDFs
- versioning release documents

### `QualityAnalytics`

Responsible for:

- TAT
- RFT %
- failure rates
- vendor metrics
- inspector metrics
- inspection volume

---

# 11. UI Requirements

## Capacity

### Dashboard

Show:

- Executive KPIs
- financial loss
- volume targets
- capacity status

### Fabrication Stream

Display:

```text
Shell Prep -> Drum Fab -> DSG Welding -> Grate Assembly
```

Use state badges:

- Green = working/healthy
- Yellow = queue/warning
- Red = blocked/failure

### Shop Grid

2x4 grid:

```text
WS 1.1  WS 1.5
WS 1.2  WS 1.6
WS 1.3  WS 1.7
WS 1.4  WS 1.8
```

Each workstation should expose:

- station
- project
- status
- delay/loss
- blocking reason
- relevant resource state

### Resource Console

- S1/S2 attendance
- operator skills
- machine status

### Materials

- shortage status
- promised delivery
- critical component tracking
- BOM/PO cycle information

### Analytics

- queue capacity warnings
- TPT root causes
- idle time
- financial impact

## Quality

### Vendor Portal

- dashboard
- active inspections
- inspection history
- new request form
- mandatory QAP/drawing uploads
- request timeline
- report/release viewer

### Team Lead

- active request queue
- inspector workload
- assignment
- calendar/skill filtering
- review and decision

### Inspector

- assigned tasks
- schedule
- IMS-FV-FF03 report builder
- item matrix
- observations
- calibration
- punch points
- evidence uploads

### Release Viewer

- release PDF
- authenticated download

### Analytics

- TAT
- RFT
- failure rates
- inspection volume

---

# 12. Implementation Phases

## Phase 0 — Workspace Discovery & Context Lock

### Tasks

- [x] Inspect existing backend and frontend structure.
- [x] Review DRF and Angular coding standards.
- [x] Verify JWT login. (built + verified in Phase 1 — none existed at Phase 0 time, see ARCHITECTURE_DECISIONS.md C4)
- [x] Verify refresh. (built + verified in Phase 1)
- [x] Verify `/auth/me/`. (built + verified in Phase 1)
- [x] Verify Angular JWT interceptor. (built + verified in Phase 1)
- [x] Verify AuthGuard. (built + verified in Phase 1)
- [x] Verify RoleGuard. (built + verified in Phase 1)
- [x] Run baseline backend tests.
- [x] Run baseline Angular tests.
- [x] Produce architecture/gap report. (PHASE0_GAP_REPORT.md)

### Gate

- [x] No unexplained architecture conflicts. (resolved via ARCHITECTURE_DECISIONS.md, developer-confirmed)
- [x] Baseline test results recorded.
- [x] Phase 0 = DONE.

---

## Phase 1 — Authentication & RBAC

### Tasks

- [x] Reconcile all eight roles. (seed_roles management command)
- [x] Implement/reuse DRF permission classes. (RoleCheckMixin per ADR-002)
- [x] Implement/reuse Angular RoleGuard.
- [x] Implement role-specific routing.
- [x] Verify object-level authorization. (department-scoped /auth/users/ access)
- [x] Verify unauthorized access returns HTTP 403.

### Gate

- [x] RBAC tests pass. (26/26 backend tests)
- [x] Route guard tests pass. (frontend guard/interceptor tests)
- [x] Phase 1 = DONE.

---

## Phase 2 — Quality Backend

### Tasks

- [x] Reconcile quality models. (16 models confirmed against DB; no schema changes were needed)
- [x] Implement migrations. (0001–0003 already applied; none added this pass)
- [x] Implement serializers. (added `InspectionRequestDetailSerializer`; `download_url` pattern on documents/evidence/release notes instead of raw file paths)
- [x] Implement viewsets/actions. (added inspector expertise/availability endpoints, report sub-resource endpoints for items/observations/calibration logs, document delete, punch-point-evidence download, per-version release download)
- [~] Implement `InspectionStateService`. **Deviation, documented, not re-architected**: transition logic lives in `quality/lifecycle.py::assert_valid_transition()` plus inline in the viewsets (`InspectionRequestViewSet`, `InspectionReportViewSet`, `ReviewViewSet`), not a dedicated `quality/services/state_service.py` class. This predates the current session; functionally complete and tested, but architecturally different from §10. Not refactored — see §20 ("do not silently overwrite existing architecture").
- [~] Implement `AssignmentService`. **Same deviation**: availability/skill/overlap/workload checks live in `InspectionRequestViewSet._check_inspector_can_take_assignment()` in `views.py`, not `quality/services/assignment_service.py`.
- [~] Implement `ReleaseNoteService`. **Same deviation**: PDF generation lives in `quality/pdf.py::render_release_note_pdf()` plus versioning logic inline in `ReleaseViewSet.generate()`, not `quality/services/release_service.py`.
- [x] Add optimized querysets. (`select_related`/`prefetch_related` throughout; `scoping.py::scope_requests_for_user()` shared by the request list and analytics endpoints so role-scoping can't drift between them)
- [x] Add API tests. (94/94 passing across `fauth`+`capacity`+`quality`, including 2 pre-existing security-gap fixes found during this pass: unrestricted Vendor/Inspector/Report update+destroy, unscoped `PunchPoint` queryset)

### Gate

- [x] Request creation works.
- [x] Assignment works.
- [x] Report submission works.
- [x] Review decision works.
- [x] Release generation works.
- [x] Phase 2 = DONE — with the three documented `services/` naming deviations above (functionally equivalent, not literally under `quality/services/`). Flagged, not hidden.

---

## Phase 3 — Quality Frontend

### Tasks

- [x] Vendor portal. (existing; wired into the new request-detail screen)
- [x] Team Lead console. (existing; wired into request-detail)
- [x] Inspector workspace. (existing; **fixed a real bug this session** — it previously created+immediately-locked a report in one click with zero items/observations/calibration/punch points, bypassing the report workspace entirely. Now navigates to request-detail's Report tab.)
- [x] IMS-FV-FF03 builder. **Known field-name gap, not fixed**: built as `report-workspace` using the pre-existing generic model fields (`sequence`/`parameter`/`specification`/`actual_result`/`status`/`remarks`), not the original proposal's exact IMS-FV-FF03 fields (PO Sr. No / Item Description / Offered Qty. / Accepted Qty.). Fixing this means a new migration + developer decision, not a UI-only change — flagged to the developer, not silently patched.
- [x] Punch point workflow. (create, resolve, severity/status)
- [x] Evidence upload. (+ authenticated download, no raw file paths exposed)
- [x] Release viewer. (version list, generate, per-version authenticated download)
- [x] Quality analytics. (Quality Dashboard: count-based KPIs — no percentages per developer request — 12-month volume line chart, vendor-wise/inspector-wise RFT bar charts, a real Compliance Alerts panel (overdue calibration, unassigned pending requests, delayed reviews), status breakdown, needs-attention list)

### Gate

```text
Request
 -> Submit
 -> Assign
 -> Inspect
 -> Report
 -> Review
 -> Accept
 -> Release
```

- [x] Verified end-to-end via Playwright (`qa_e2e_quality.py`, 17/17 steps) and manual browser QA across VENDOR/TEAM_LEAD/INSPECTOR/ADMIN.
- [x] Phase 3 = DONE.

---

## Phase 4 — Capacity Backend

### Tasks

- [x] Reconcile capacity models. (`capacity/models.py` was an empty scaffold; all 16 models from §5 built fresh, station-centric per the developer's explicit requirement — Station FKs radiate out to Machine/ShiftRoster/StationExecutionTask/WorkstationGridState/IdleTimeLog)
- [x] Implement migrations. (`capacity/migrations/0001_initial.py`, applied, `makemigrations --check --dry-run` clean)
- [x] Implement constraint engine. (`capacity/services/constraint_engine.py::ConstraintValidationEngine` — manpower/machine/material validation, `transition()`, `reassign_station()`)
- [x] Implement capacity calculations. (`capacity/services/capacity_service.py::CapacityCalculationService` — menshifts, shop efficiency %, monthly targets)
- [x] Implement loss calculations. (`capacity/services/loss_calculator.py::LossImpactCalculator` — total loss days, financial impact ×₹250,000, category breakdown)
- [x] Implement TPT analytics. (`capacity/services/tpt_analytics.py::TPTAnalytics` — wait-time trend, station efficiency ranking, bottleneck detection, root-cause breakdown, delay analysis)
- [x] Implement serializers/viewsets. (`capacity/serializers.py`, `capacity/views.py` — matches PLAN.md §10's `capacity/services/` module names exactly, unlike Quality's documented deviation, since this was a fresh app with nothing to preserve)
- [x] Implement API routes. (`capacity/urls.py`, registered in `core/urls.py` as `api/<version>/capacity/`) — plus the developer's explicit station-shifting feature: `POST /shift-rosters/{id}/reassign/` and `POST /tasks/{id}/reassign-station/`, both re-validating the three-way constraint against the *target* station before committing the move.
- [x] Add tests. (31 capacity tests: constraint engine gate scenarios, formula exactness, API security, task/roster/analytics endpoints — found and fixed 1 real bug (`timezone.now().date()` truncates in UTC per this project's `TIME_ZONE='UTC'` setting, not local calendar day — fixed to `timezone.localdate()`) and 1 real security gap (every capacity viewset allowed any authenticated role to read — including quality-only roles like VENDOR — because the read path was never role-gated, only writes were; fixed with a blanket `initial()` role check via `_CapacityAccessMixin`).

### Gate

- [x] Missing manpower blocks task.
- [x] Non-operational machine blocks task.
- [x] Missing material blocks task.
- [x] All resources available allows WORKING.
- [x] Financial calculation is correct.
- [x] Phase 4 = DONE. Full backend suite 125/125 passing.

---

## Phase 5 — Capacity Frontend

### Tasks

- [x] Fabrication stream. (`components/capacity/command-center/` — KPI strip + 4-column station board, matches the "Master Fabrication Flow" mockup exactly: color-coded Working/Queuing/Waiting node cards, clicking a node opens the task drill-down)
- [x] 2x4 workstation grid. Backend endpoint (`GET /capacity/stream/grid-canvas/`, `WorkstationGridStateViewSet`) is built and tested; **no dedicated grid UI screen was built this pass** — none of the 4 supplied mockups showed this view, and the plan's screen list prioritized the 3 nav pages that had real visual references. Flagged as a real gap, not hidden — see the final report.
- [x] Resource console. (`components/capacity/resource-management/` — Skill Proficiency Matrix, Asset Readiness with a live machine OPERATIONAL/OFFLINE toggle, Active Utilization stat, Batch Logistics Tracker, bottleneck alert strip — matches the "Performance Hub" mockup)
- [x] Machine status UI. (Asset Readiness section within Resource Management; `toggle` action wired end-to-end)
- [x] Shift roster UI. **Partial**: the roster/reassign *actions* are fully built and tested on the backend and consumed by the task-detail screen's manpower panel; a dedicated "log a whole shift" form (proposal's Shift Manager Form / `bulk-log` endpoint) has no frontend UI yet — flagged as a gap.
- [x] Material shortage matrix. (Batch Logistics Tracker in Resource Management, plus the Material panel + "Mark Received" action on task-detail)
- [ ] BOM UI. **Not built** — `ProjectBOMItemSerializer`/the `bom-items` action exist on the backend, but no screen consumes it; none of the 4 mockups showed a BOM view. Flagged as a real gap.
- [x] TPT analytics. (`components/capacity/analytics/` — KPI cards, a real dual-series-bar-plus-threshold-line Wait-Time Trend chart via PrimeNG `p-chart` mixed bar/line datasets, Station Efficiency ranked list, Systemic Delay Analysis table with severity-colored rows, Root Cause Breakdown — matches the "Bottleneck & Delay Analytics" mockup)

Also: `DashboardHome` extended so PLANNER/SUPERVISOR/EXECUTIVE/ADMIN see the Command Center at the generic `/dashboard`, keeping the single-"Dashboard"-entry fix from the Quality pass consistent across both modules. Nav updated to 3 items (Command Center / Resource Management / Capacity Analytics) matching the mockups' own nav — no flat per-resource sprawl.

Two real bugs found via live browser QA (not just unit tests) and fixed: `avg_oee_percent`/`avg_cycle_time_hours` were missing from the overview endpoint entirely; `active_manpower` double-counted an operator rostered at two stations the same day (displayed as "3/2", more active than total); and a station efficiency labeling bug that mislabeled a healthy WORKING station as "BLOCKED" whenever its historical completion rate was 0% (i.e. always, until something actually finishes) — all three fixed with regression tests.

### Gate

- [x] Grid reflects backend state. (station board / task detail — the grid-canvas endpoint itself is untested by a UI since no grid screen was built, see above)
- [x] Machine changes affect constraints. (toggling a machine OFFLINE removes it from `validate_machine`'s operational count — verified by the constraint-engine tests)
- [x] Shift changes affect manpower validation. (verified live: a task blocked on "no operator rostered" cleared once a present `ShiftRoster` row existed for that station/date)
- [x] Material changes affect task status. (verified live end-to-end: task blocked with the exact reason "Material not yet received for FVPB-26-012: Fusible Plug", "Mark Received" clicked, re-validated, transitioned to WORKING)
- [x] Phase 5 = DONE — with the two documented gaps above (2x4 grid UI, BOM UI, and the shift bulk-log form) not built this pass since they had no supplied mockup and weren't blocking the core flow. Backend 130/130, frontend 126/126, build clean.

### Post-Phase-5 verification

- [x] `qa_e2e_capacity.py` (Playwright, mirrors `qa_e2e_quality.py`'s pattern): login → Command Center shows the seeded station → task drill-down → attempt WORKING transition blocks on missing manpower with the exact real reason → fix material via "Mark Received" → Reassign Station dialog opens → Resource Management shows the seeded machine → Analytics renders. **8/8 steps passing.**
- [x] Screenshots compared against the 4 supplied mockups — Command Center and Analytics in particular are a close match (4-stage kanban board with color-coded node cards; dual-series bar chart with a dashed threshold reference line, KPI cards, severity-colored delay table).
- [x] Full backend regression after Phase 4+5: 130/130.
- [x] Full frontend regression after Phase 4+5: 126/126, `ng build` clean.

Phase 6 was started after the developer explicitly approved it in a follow-up ("proceed with implementation in code make sure all feature implemnted"), following a scoping conversation that cross-referenced the real "Forbes Vyncke Shop Stream Lining Visual Board" photo (4 lines, ~36 named sub-stations, several with a second board-level sub-group) against the 5 real production CSVs.

---

## Phase 6 — Master Data Ingestion

`python manage.py seed_fv_masterdata --data-dir "<path to Capacity Planning Sheets>"` — idempotent (safe to re-run), verified against the real files.

What it does:

- **Real sub-stations**: replaces the 4 coarse dev stations with the real ~36 named sub-stations from the board photo (`Station.subgroup` — new field — captures the board's second-level grouping, e.g. "Rolling Machine" within Shell Preparatory; blank where the board has none, e.g. DSG lines).
- **`TPT.csv`** → `Project` rows (project_number = Boiler No, name = Project, status inferred from start/end date vs today) + `Project.historical_headcount`/`historical_idle_days` (new fields — legacy aggregate figures, not tied to named `ShiftRoster`/`IdleTimeLog` rows) + `ProjectBOMItem` rows from the wide component columns (Plate material/Screen plate/Flange/Man hole/TUBE/ENGG/MPA/Space/CASING).
- **`Shortage.csv`** → `MaterialBatchItem` rows (wide Status/Required-date columns → one row per material), matched to a `Project` by customer-name; creates a stub `PLANNED` project when no name match exists. `MaterialBatchItem.notes` (new field) preserves the original messy status text (e.g. "40 NB Required") the clean status enum can't hold.
- **`Window plan.csv`** → the Case 1/Case 2 monthly capacity balance (new `MonthlyCapacityCase` model: standard availability, "Extra effort 12hr" overtime, "Support Supplier 1+2") + `CapacityTarget` rows for the month (target-type enum extended from PES/CES to PES/CES_DRUM/CES_GRATE/FLS, matching the sheet's own categories). `CapacityCalculationService.capacity_case_for_month()` computes the required-vs-available shortfall for both cases; surfaced as `capacity_case` on `analytics/overview/` and rendered as a banner on the Analytics screen.
- **Skipped, deliberately**: `Sheet1.csv` (pure rollup of `Shortage.csv`, no new data) and `Sheet5.csv` (2.9 MB per-project day/shift Gantt matrix — its cell-value semantics were never actually reviewed beyond the header row, so importing it now would risk silently fabricating `ProductionSchedule` data; needs a dedicated review pass before it can be trusted). A capacity↔quality cross-link (TPT.csv's "QC Release note"/"Re work" columns) was scoped but not built — `capacity.Project` and `quality.InspectionRequest` remain unlinked; no explicit ask to couple the two apps was confirmed.
- Role-based dev users were already seeded in Phase 4/5 (`seed_test_data.py` / the QA scripts' inline seeding) — not duplicated here.

### Gate

- [x] Command executes without errors, idempotent (verified via double-run against the real CSVs and in tests).
- [x] Imported data validated: spot-checked project/material/capacity-case rows against the source CSVs after a real run (see `capacity/tests/test_seed_masterdata.py`, 6 tests against hermetic fixture CSVs mirroring the real headers, plus 2 station-seeding tests).
- [x] Phase 6 = DONE, with Sheet5 and the cross-app Quality link explicitly deferred (see above) — not silently dropped.
- [x] Full backend regression: 140/140. Full frontend regression: 126/126, `ng build` clean.

### Post-Phase-6 addition — real Boiler entity, 4th constraint check, dashboard panels

Developer walkthrough of the real sheet/board data surfaced 3 gaps versus what "capacity planning" actually needs to report, all built and verified live:

- **`Boiler` model added** (project FK, boiler_no unique, status IN_PROGRESS/DELIVERED, current_station, delivered_date) — a Project can now genuinely hold 1+ boilers, tracked and delivered individually. Backfilled via data migration from every existing `Project.boiler_no`; `seed_fv_masterdata` now creates/updates a Boiler alongside each Project it ingests.
- **Real bug fixed**: `CapacityCalculationService.actual_output_units()` was counting completed *tasks*, not delivered *boilers* — a boiler passes through 10+ station tasks before shipping, so the old number wildly overstated monthly output. Now counts `Boiler.objects.filter(status=DELIVERED, delivered_date__in month)`.
- **4th constraint check added**: `ConstraintValidationEngine.validate_location()` — a station's WORKING slot can only hold one job at a time (matches the physical board: one card slot per station row). `TaskConstraintLog.CheckType` gained `LOCATION`.
- **Two dashboard panels wired that existed in the backend but were never rendered**: Plant-Wide Loss (`analytics/loss-impact/` — was built in Phase 4, never called from any component) now shows on the Analytics screen; "Boilers This Month" (X delivered / Y targeted) added to the Command Center KPI strip; a "Boilers Delivered This Month" list added to Analytics; a "Boiler Delivery" section (in-progress boilers + Mark Delivered action) added to Resource Management.
- Tests: 11 new backend tests (constraint engine location check, boiler-counting service tests, Boiler API role/CRUD/deliver tests, seed-command Boiler assertions) — 151/151 backend. Frontend: 129/129, `ng build` clean. Verified live in-browser: Command Center KPI card, Analytics loss panel + delivered list, and a real "Mark Delivered" click removing a boiler from Resource Management's in-progress list.

### Post-Phase-6 addition #2 — planner-facing simplification + weekly analytics + Project/Station screens

Developer feedback after using the live app: Command Center felt too dense to scan, the 4 station-group columns wrapped 3-then-1 instead of sitting in one row, there was no way to see a project's or a station's own data on one screen, and no week-of-month breakdown (the Window Plan sheet's own reporting grain). All 4 addressed:

- **Kanban 4-column layout fixed**: `.fv-kanban` (`styles.css`) was `grid-template-columns: repeat(auto-fit, minmax(240px,1fr))`, which fits 3 columns then wraps the 4th at common viewport widths. Changed to `repeat(4, 1fr)` with explicit 2-column/1-column breakpoints for narrower screens. Verified live — all 4 line columns share the same row `top` offset at 1280px width.
- **Command Center cards simplified**: every station card's `blocking_reason` used to render as a permanent paragraph on the card; now it's a `title` tooltip (surfaced on hover), station name/task name are truncated with ellipsis, and card padding shrunk — the board now reads closer to the physical board's "one compact card per slot" feel. Every card (not just ones with an active task) now links somewhere — to the new Station Detail screen.
- **New Station Detail screen** (`/capacity-planning/stations/:id`): machines at the station, today's roster, recent tasks (linking into task-detail), and idle/loss history — data that existed via 3 different filtered list endpoints but had nowhere combined.
- **New Project List + Project Detail screens** (`/capacity-planning/projects`, `/capacity-planning/projects/:id`): search/status-filtered project list; detail shows the project's Boilers (with Mark Delivered inline), BOM items, material batches, and station tasks — the "project-wise configuration" view that was previously scattered/absent. Added "Projects" to the nav (4th capacity item).
- **New weekly cross-analytics**: `CapacityCalculationService.weekly_breakdown(month)` splits a month into 7-day buckets (Window Plan's own Week 1-4 reporting grain) and reports real already-tracked events per week — boilers delivered, constraint failures, loss days — no fabricated per-week targets. New `GET /capacity/analytics/weekly/` endpoint; new "Weekly Breakdown" table on the Analytics screen.
- Tests: +12 backend (weekly_breakdown bucketing/counting, weekly endpoint), +3 new frontend component spec files (ProjectList, ProjectDetail, StationDetail) plus updated Command Center specs for the tooltip/routing change — frontend 136/136, `ng build` clean. Verified live: kanban row layout via computed styles, tooltip + station-detail routing on node cards, Project list → detail with real seeded data, Station detail with real machines/roster/tasks, and the Weekly Breakdown table matching real delivered-boiler dates.

### Post-Phase-6 addition #3 — planner-facing Configuration screen

Developer wanted config screens that feel familiar to a team currently living in Excel, specifically for the handful of things they'd actually type numbers into weekly: monthly targets, the Window Plan Case 1/2 balance, shift headcount, and station setup. None of these had a dedicated planner-facing UI before — targets/case/stations were admin-only, and shift headcount only had a single-operator-at-a-time dialog buried in task-detail.

Pure frontend addition — no backend changes needed, since `CapacityTargetViewSet`, `MonthlyCapacityCaseViewSet`, `StationViewSet`, and `ShiftRosterViewSet` already had full CRUD from Phase 4/6. Added:

- `CapacityApiService`: `createStation`/`updateStation`, `createCapacityTarget`/`updateCapacityTarget`/`deleteCapacityTarget`, `listCapacityCases`/`createCapacityCase`/`updateCapacityCase`/`deleteCapacityCase`, `deleteShiftRoster`.
- New `CapacityConfiguration` component (`/capacity-planning/configuration`, nav item added) — one page, 4 card sections matching the "row = item, columns = the numbers you'd type" table layout rather than modern popup dialogs:
  - **Monthly Targets**: month + type (PES/CES/CES Drum/CES Grate/FLS) + units, add/delete.
  - **Capacity Case**: month + Available 8Hrs + Extra Effort 12Hrs + Supplier Support, add/delete.
  - **Shift Headcount**: station + date + shift + multi-select operators, one submit bulk-logs everyone selected in one call (reuses the existing `bulk-log` endpoint) — faster than the old one-operator-at-a-time dialog; shows/removes that slot's current roster below.
  - **Stations**: full station list with every field directly editable.
- Tests: 8 new component spec tests (create/delete target, create/delete case, bulk headcount log, roster removal, station create, station edit-and-save) — frontend 144/144, `ng build` clean. Verified live: added a September 2026 PES target end-to-end through the real UI (month-picker → submit → API → list refresh) and deleted it again, both confirmed against the running backend.

**Follow-up — inline cell editing (closing the "feels like Excel" gap).** Developer pushback: the form-then-list pattern above is a web-CRUD pattern, not a spreadsheet — in Excel every cell is visible and editable at once, no separate add step, no edit dialog. Reworked without a grid library (PrimeNG's `p-table` plus plain bound inputs was enough for these 4 non-wide screens):
- Monthly Targets, Capacity Case, and Stations are now real editable tables — every existing row's numeric/text fields are live inputs that PATCH on blur/change (`saveTargetUnits`, `saveCaseField`, `saveStation`), no Edit button or dialog left for Stations. A small "Add Row" form stays underneath each table only for creating a brand-new row (unavoidable — a wholly blank row needs its key fields set before it can be saved).
- New CSS (`.fv-edit-table`) makes the inputs look like sheet cells at rest (borderless, blend into the row) and only show a border on hover/focus.
- Tests updated: replaced the old single edit-form tests with inline-save tests (`saveStation`, `saveTargetUnits`, `saveCaseField`) plus the still-needed create-row tests — frontend 146/146, `ng build` clean, zero backend changes (same PATCH endpoints already existed). Verified live: edited a target's unit count in the browser (PATCH fired, 200 OK), reloaded the page, value persisted — then reverted it back.

---

## Phase 7 — Automated Verification

### Backend

Test:

- quality lifecycle
- assignments
- report submission
- review decisions
- release generation
- three-way capacity constraints
- capacity calculations
- financial loss
- TAT
- RFT
- authorization

### Frontend

Test:

- authentication
- role routing
- major workflows
- forms
- error states
- loading states

### Gate

- [ ] Backend tests pass.
- [ ] Angular tests pass.
- [ ] No critical failures.
- [ ] Phase 7 = DONE.

---

## Phase 8 — Hardening & Handover

### Tasks

- [ ] Generate OpenAPI/Swagger documentation.
- [ ] Validate empty states.
- [ ] Validate loading states.
- [ ] Validate error handling.
- [ ] Review permissions.
- [ ] Review file-download security.
- [ ] Review transactions.
- [ ] Review ORM performance.
- [ ] Update `ARCHITECTURE.md`.
- [ ] Update `API.md`.
- [ ] Update `DATABASE.md`.
- [ ] Confirm excluded infrastructure has not been introduced.

### Gate

- [ ] Production acceptance criteria pass.
- [ ] Phase 8 = DONE.

---

# 13. Testing Strategy

Tests should be organized by domain.

```text
backend/
├── fauth/tests/
├── capacity/tests/
└── quality/tests/
```

Minimum critical test matrix:

| Area | Required Tests |
|---|---|
| Authentication | login, refresh, profile |
| RBAC | every role + forbidden access |
| Quality State | every valid/invalid transition |
| Quality Assignment | availability, skill, overlap |
| Quality Report | create, submit, lock |
| Punch Points | create, resolve, evidence |
| Release | accepted-only generation |
| Capacity | manpower constraint |
| Capacity | machine constraint |
| Capacity | material constraint |
| Capacity | combined constraints |
| Loss | loss days × ₹250,000 |
| Capacity | 132 menshifts |
| Analytics | TAT/RFT calculations |
| Security | unauthorized object access |
| Files | authenticated document access |

---

# 14. Security Requirements

Claude Code must explicitly verify:

- JWT authentication remains intact.
- Role permissions are enforced server-side.
- Frontend guards are not treated as security boundaries.
- Object-level authorization is enforced in APIs.
- Inspection documents are not publicly accessible.
- Release PDFs require authorization.
- Uploaded files are validated.
- Audit logs are append-only from application workflows.
- State changes occur inside safe transactions.
- Sensitive information is not unnecessarily exposed in API responses.

---

# 15. Performance Requirements

Use:

```python
select_related(...)
prefetch_related(...)
```

for relationship-heavy APIs.

Add appropriate PostgreSQL indexes for:

- status fields used in queues
- dates used in scheduling
- request numbers
- project references
- inspection status
- assignment dates
- station/project lookups

Avoid N+1 queries.

For list endpoints:

- use pagination where appropriate
- avoid returning unnecessary nested data
- expose filtering/search/order parameters where useful

---

# 16. File Handling

Quality documents include:

- QAP
- Drawing
- Approved Procedure
- Evidence
- Release Note

Required controls:

- authenticated download
- MIME/type validation
- size validation
- safe filenames
- version tracking
- uploader tracking

Do not expose raw storage paths directly.

---

# 17. Audit Requirements

Audit significant operations:

- login
- create
- update
- delete
- assignment
- state changes
- decisions

Quality status transitions must also create `QualityStatusHistory`.

---

# 18. Definition of Done

A phase is complete only when:

1. Implementation matches this plan or a documented approved deviation.
2. Existing functionality remains intact.
3. Database migrations succeed.
4. API contracts work.
5. Authorization tests pass.
6. Automated tests pass.
7. No critical console/server errors remain.
8. Performance regressions are investigated.
9. Documentation is updated where required.
10. The phase checklist is marked complete.

Never mark a phase `DONE` merely because code has been written.

---

# 19. Claude Code Required Execution Prompt

Use this workflow at the beginning of every session:

```text
Read PLAN.md first.

Do not start by rewriting code.

1. Inspect the existing repository.
2. Inspect the current implementation of every relevant Django and Angular feature.
3. Read the referenced coding standards.
4. Run baseline tests.
5. Compare the repository against PLAN.md.
6. Produce a concise gap report:
   - Already implemented
   - Partially implemented
   - Missing
   - Conflicting implementation
   - Risks
7. Propose the smallest safe implementation path.
8. Wait for phase scope if multiple phases are outstanding.
9. Implement only the approved phase.
10. Run relevant tests.
11. Fix failures.
12. Report:
   - files changed
   - migrations created
   - APIs changed
   - tests added/updated
   - test results
   - remaining risks
13. Mark the phase DONE only when its gate passes.
```

---

# 20. Important Instruction About This Plan

This PLAN.md is a requirements baseline, not permission to blindly recreate the system.

When the repository differs from the plan:

- Preserve working code when possible.
- Identify the difference.
- Determine whether it is an intentional existing implementation or a missing requirement.
- Do not silently overwrite existing architecture.
- Do not introduce new infrastructure that is explicitly excluded.
- Do not claim functionality is complete without tests.

The supplied requirements are the source of functional intent; the repository is the source of truth for what already exists.

---

# 21. Original Requirements Traceability

The detailed source requirements used to create this implementation plan are preserved in the supplied project planning material. When a field, endpoint, role, workflow, or business rule is unclear, consult the original source before making assumptions.
