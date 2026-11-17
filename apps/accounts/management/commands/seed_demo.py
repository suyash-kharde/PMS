"""Idempotent bootstrap: capabilities, role tree, teams, users, demo projects."""

import os
import random
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Capability, Role, RoleCapability, Team, User
from apps.projects.models import (
    Activity, Label, Milestone, Project, ProjectMembership, Task, Workflow, WorkflowState,
)

CAPABILITIES = [
    ("project.view", "View projects", "projects"),
    ("project.create", "Create projects", "projects"),
    ("project.edit", "Edit projects", "projects"),
    ("project.delete", "Delete projects", "projects"),
    ("task.view", "View tasks", "tasks"),
    ("task.create", "Create tasks", "tasks"),
    ("task.edit", "Edit tasks", "tasks"),
    ("task.delete", "Delete tasks", "tasks"),
    ("task.move", "Move tasks on the board", "tasks"),
    ("task.comment", "Comment on tasks", "tasks"),
    ("customer.view", "View customers", "customers"),
    ("customer.manage", "Create and edit customers", "customers"),
    ("ticket.view", "View tickets", "tickets"),
    ("ticket.create", "Raise tickets (bugs, change requests, …)", "tickets"),
    ("ticket.edit", "Edit ticket details", "tickets"),
    ("ticket.triage", "Triage tickets — status, assignment, priority", "tickets"),
    ("ticket.comment", "Comment on tickets", "tickets"),
    ("ticket.delete", "Delete tickets", "tickets"),
    ("sprint.view", "View sprints, requirements, acceptance criteria and test cases", "sprints"),
    ("sprint.manage", "Manage sprints, requirements, acceptance criteria, field specs, risks, dependencies", "sprints"),
    ("testcase.manage", "Create unit test cases and record execution results", "sprints"),
    ("service.manage", "Manage the external / serviceable services register", "sprints"),
    ("sprint.generate", "Generate the sprint plan from project documents with AI", "sprints"),
    ("timeline.view", "View the project timeline", "timeline"),
    ("timeline.add", "Add timeline entries (notes, files, audio)", "timeline"),
    ("timeline.process", "Reprocess media and rename speakers", "timeline"),
    ("brd.view", "View the Business Requirement Document", "brd"),
    ("brd.generate", "Regenerate the BRD with the LLM", "brd"),
    ("brd.edit", "Hand-edit the BRD", "brd"),
    ("brd.delete", "Delete the BRD (every version, cannot be undone)", "brd"),
    ("user.view", "View people", "people"),
    ("user.create", "Create users", "people"),
    ("user.edit", "Edit users", "people"),
    ("user.delete", "Delete users", "people"),
    ("model.manage", "Configure AI/ML model endpoints", "admin"),
    ("mcp.manage", "Manage Claude Code / MCP access links", "admin"),
    ("penpot.manage", "Manage the project's Penpot design workspace", "admin"),
    ("timesheet.view", "Timesheet management view: see other people's timesheets, attendance and reports (view only)", "timesheet"),
    ("timesheet.tab.week", "Timesheet tab: my weekly timesheet", "timesheet"),
    ("timesheet.tab.people", "Timesheet tab: People", "timesheet"),
    ("timesheet.tab.calendar", "Timesheet tab: Calendar", "timesheet"),
    ("timesheet.tab.holidays", "Timesheet tab: Holidays", "timesheet"),
    ("timesheet.tab.attendance", "Timesheet tab: Attendance", "timesheet"),
    ("timesheet.tab.reports", "Timesheet tab: Reports", "timesheet"),
    ("timesheet.holidays.manage", "Timesheet: add, edit and delete holidays and extra working days", "timesheet"),
    ("timesheet.edit_closed", "Timesheet: edit hours after the month has closed", "timesheet"),
    ("security.manage", "Security settings: set the auto sign-out time", "admin"),
    ("mytasks.view", "My tasks page", "menu"),
    ("aiqueue.view", "AI queue page", "menu"),
    ("docs.view", "Documentation page", "menu"),
    ("devlog.view", "View the Claude Code development log", "devlog"),
    ("devlog.manage", "Add, edit and delete development log entries", "devlog"),
    ("role.manage", "Manage roles & capabilities", "admin"),
    ("team.manage", "Manage teams", "admin"),
    ("report.view", "View reports", "admin"),
    ("project.plan.view", "View the Plan tab (milestones, sub-projects, discussion)", "projects"),
    ("participant.view", "View the People tab (project participants)", "projects"),
    ("techstack.view", "View the Tech Stack document", "projects"),
    ("devinstructions.view", "View the Dev Instructions document", "projects"),
    ("buildplan.view", "View the Build Plan document", "projects"),
    ("activity.view", "View the Activity tab", "projects"),
    ("chat.view", "Use the Ask AI chat tab", "projects"),
]

# role slug -> (name, parent slug, rank, color, {capability: scope})
ROLES = {
    "guest": ("Guest", None, 500, "#94a3b8", {
        "project.view": "own", "task.view": "own", "timeline.view": "own", "brd.view": "own",
        "ticket.view": "own", "ticket.create": "all", "ticket.comment": "all",
        "sprint.view": "own", "devlog.view": "own",
        "project.plan.view": "own", "participant.view": "own", "techstack.view": "own",
        "devinstructions.view": "own", "buildplan.view": "own", "activity.view": "own", "chat.view": "own",
    }),
    "member": ("Member", "guest", 400, "#0ea5e9", {
        "task.create": "own", "task.edit": "own", "task.move": "own", "task.comment": "own",
        "user.view": "team", "timeline.add": "own", "timeline.process": "own", "brd.view": "team",
        "customer.view": "own",
        "ticket.view": "team", "ticket.create": "all", "ticket.edit": "own", "ticket.comment": "all",
        "sprint.view": "team", "testcase.manage": "own", "devlog.view": "team",
    }),
    "lead": ("Team Lead", "member", 300, "#8b5cf6", {
        "project.view": "team", "task.view": "team", "task.edit": "team", "task.delete": "team",
        "task.move": "team", "task.create": "team", "project.edit": "team", "report.view": "team",
        "timeline.view": "team", "timeline.add": "team", "timeline.process": "team",
        "brd.generate": "team", "brd.edit": "team", "customer.view": "team",
        "ticket.edit": "team", "ticket.triage": "team", "ticket.delete": "team",
        "sprint.view": "team", "sprint.manage": "team", "testcase.manage": "team", "service.manage": "team",
        "sprint.generate": "team", "mcp.manage": "team", "devlog.manage": "team",
        "penpot.manage": "team",
        "project.plan.view": "team", "participant.view": "team", "techstack.view": "team",
        "devinstructions.view": "team", "buildplan.view": "team", "activity.view": "team", "chat.view": "team",
    }),
    "manager": ("Project Manager", "lead", 200, "#f59e0b", {
        "project.create": "all", "project.view": "all", "project.edit": "team",
        "task.view": "all", "user.view": "all", "report.view": "all",
        "timeline.view": "all", "timeline.add": "all", "brd.view": "all",
        "brd.generate": "all", "brd.edit": "all", "customer.view": "all",
        "ticket.view": "all", "ticket.triage": "all",
        "sprint.view": "all", "sprint.manage": "all", "testcase.manage": "all", "service.manage": "all",
        "sprint.generate": "all", "mcp.manage": "all", "devlog.view": "all", "devlog.manage": "all",
        "penpot.manage": "all",
        "project.plan.view": "all", "participant.view": "all", "techstack.view": "all",
        "devinstructions.view": "all", "buildplan.view": "all", "activity.view": "all", "chat.view": "all",
    }),
    "director": ("Director", "manager", 100, "#ef4444", {
        "project.edit": "all", "project.delete": "all", "user.create": "all", "user.edit": "team",
        "team.manage": "all", "customer.manage": "all", "ticket.delete": "all",
    }),
    "admin": ("Administrator", "director", 10, "#111827", {
        "role.manage": "all", "user.edit": "all", "user.delete": "all", "task.delete": "all", "model.manage": "all",
        "timesheet.holidays.manage": "all", "timesheet.edit_closed": "all",
    }),
}

DELIVERY_RESPONSIBILITIES = {
    "dana": "Owns the commercial relationship and signs off every BRD revision.",
    "marco": "Runs the programme: scope, schedule, and the client-facing reporting line.",
    "aisha": "Accountable for platform architecture and backend delivery.",
    "tom": "Accountable for the mobile applications on both stores.",
    "nora": "Owns the design system and all user-facing flows.",
}

# (username, position, role description, responsibility, remarks)
CLIENT_BRIEFS = [
    ("priya", "Product Owner",
     "Primary client contact. Prioritises the backlog and approves requirements.",
     "Final say on functional scope; signs off acceptance criteria.",
     "Prefers written confirmation of every scope change; available Tue–Thu."),
    ("victor", "CTO",
     "Client technical authority for architecture and security review.",
     "Approves integration design, data residency and non-functional targets.",
     "Escalation point for anything touching the payments provider."),
    ("mei", "Operations Lead",
     "Represents the day-to-day operations team who will use the system.",
     "Signs off operational workflows, reporting and the training plan.",
     "Wants a two-week parallel run before any cutover."),
]

WORKFLOWS = {
    "Standard board": [
        ("Backlog", "todo", "#94a3b8", 0),
        ("To do", "todo", "#0ea5e9", 1),
        ("In progress", "active", "#f59e0b", 2),
        ("In review", "active", "#8b5cf6", 3),
        ("Done", "done", "#22c55e", 4),
    ],
    "Simple": [
        ("Open", "todo", "#0ea5e9", 0),
        ("Doing", "active", "#f59e0b", 1),
        ("Closed", "done", "#22c55e", 2),
    ],
}


class Command(BaseCommand):
    help = "Seed capabilities, roles, teams and demo content (safe to re-run)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--config-only",
            action="store_true",
            help="only seed capabilities, roles, workflows and the superuser — no demo content",
        )
        parser.add_argument(
            "--reseed-roles",
            action="store_true",
            help="with --config-only: re-create the built-in roles and their default permissions even though roles "
                 "already exist (otherwise a role you deleted, or a permission you removed, stays gone)",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        caps = {}
        for code, label, category in CAPABILITIES:
            cap, _ = Capability.objects.get_or_create(
                code=code, defaults={"label": label, "category": category}
            )
            caps[code] = cap

        # The container start-up runs this with --config-only on every boot. Roles are only (re)built on a fresh
        # database, or when asked for; otherwise whatever an admin created, deleted, re-parented or re-granted on the
        # Roles page is left alone. The full demo load (no --config-only) always fills in its roles.
        if options.get("config_only") and Role.objects.exists() and not options.get("reseed_roles"):
            roles = {role.slug: role for role in Role.objects.all()}
            self.stdout.write(f"roles left as they are ({len(roles)}); capabilities: {len(caps)}")
        else:
            roles = {}
            for slug, (name, parent, rank, color, grants) in ROLES.items():
                role, _ = Role.objects.get_or_create(
                    slug=slug, defaults={"name": name, "rank": rank, "color": color, "is_system": True}
                )
                roles[slug] = role
            for slug, (_, parent, _, _, grants) in ROLES.items():
                role = roles[slug]
                role.parent = roles[parent] if parent else None
                role.save(update_fields=["parent"])
                for code, scope in grants.items():
                    RoleCapability.objects.get_or_create(
                        role=role, capability=caps[code], defaults={"scope": scope}
                    )
            self.stdout.write(self.style.SUCCESS(f"roles: {len(roles)}, capabilities: {len(caps)}"))

        workflows = {}
        for name, states in WORKFLOWS.items():
            wf, _ = Workflow.objects.get_or_create(
                name=name, defaults={"is_default": name == "Standard board"}
            )
            workflows[name] = wf
            for state_name, category, color, order in states:
                WorkflowState.objects.get_or_create(
                    workflow=wf, name=state_name,
                    defaults={"category": category, "color": color, "order": order},
                )

        # superuser
        su_name = os.environ.get("DJANGO_SUPERUSER_USERNAME", "admin")
        if not User.objects.filter(username=su_name).exists():
            User.objects.create_superuser(
                su_name,
                os.environ.get("DJANGO_SUPERUSER_EMAIL", "admin@example.com"),
                os.environ.get("DJANGO_SUPERUSER_PASSWORD", "admin123"),
                first_name="Site", last_name="Admin", role=roles.get("admin"),
            )
            self.stdout.write(self.style.SUCCESS(f"superuser '{su_name}' created"))

        if options.get("config_only"):
            self.stdout.write(self.style.SUCCESS("config seeded (no demo content)"))
            return

        # everything below is get_or_create, so re-running only fills in what is missing
        projects_exist = Project.objects.filter(key="APOLLO").exists()

        teams = {}
        for name, parent in [
            ("Company", None), ("Engineering", "Company"), ("Platform", "Engineering"),
            ("Mobile", "Engineering"), ("Design", "Company"), ("Delivery", "Company"),
        ]:
            teams[name], _ = Team.objects.get_or_create(
                name=name, defaults={"parent": teams.get(parent)}
            )

        people = [
            ("dana", "Dana", "Whitfield", "director", "Company", None, "Head of Delivery"),
            ("marco", "Marco", "Reyes", "manager", "Delivery", "dana", "Programme Manager"),
            ("aisha", "Aisha", "Khan", "lead", "Platform", "marco", "Platform Lead"),
            ("tom", "Tom", "Nguyen", "lead", "Mobile", "marco", "Mobile Lead"),
            ("lena", "Lena", "Ortiz", "member", "Platform", "aisha", "Backend Engineer"),
            ("sam", "Sam", "Fisher", "member", "Platform", "aisha", "SRE"),
            ("ivy", "Ivy", "Chen", "member", "Mobile", "tom", "iOS Engineer"),
            ("raj", "Raj", "Patel", "member", "Mobile", "tom", "Android Engineer"),
            ("nora", "Nora", "Berg", "member", "Design", "dana", "Product Designer"),
            ("guest1", "Grace", "Holt", "guest", "Company", "dana", "External Stakeholder"),
            # client side
            ("priya", "Priya", "Nair", "guest", "Company", None, "Client Product Owner"),
            ("victor", "Victor", "Alvarez", "guest", "Company", None, "Client CTO"),
            ("mei", "Mei", "Tan", "guest", "Company", None, "Client Operations Lead"),
        ]
        created = {}
        for username, first, last, role_slug, team, manager, title in people:
            user = User.objects.filter(username=username).first()
            if user is None:
                user = User.objects.create_user(
                    username=username, password="demo1234", first_name=first, last_name=last,
                    email=f"{username}@example.com", role=roles[role_slug], team=teams[team], title=title,
                    color=roles[role_slug].color,
                )
            created[username] = user
        for username, *_rest in people:
            manager = _rest[4]
            if manager:
                created[username].manager = created[manager]
                created[username].save(update_fields=["manager"])
        teams["Platform"].lead = created["aisha"]
        teams["Platform"].save()
        teams["Mobile"].lead = created["tom"]
        teams["Mobile"].save()

        for name, color in [("bug", "#ef4444"), ("feature", "#22c55e"), ("chore", "#64748b"), ("urgent", "#f97316")]:
            Label.objects.get_or_create(name=name, project=None, defaults={"color": color})
        labels = list(Label.objects.filter(project__isnull=True))

        today = timezone.localdate()
        programme, _ = Project.objects.get_or_create(
            key="APOLLO",
            defaults=dict(
                name="Apollo Programme", description="Umbrella programme for the 2026 platform rebuild.",
                status="active", priority=1, owner=created["dana"], team=teams["Company"],
                workflow=workflows["Standard board"], start_date=today - timedelta(days=60),
                due_date=today + timedelta(days=180), budget=750000, color="#6366f1",
            ),
        )
        specs = [
            ("PLAT", "Platform Rebuild", "Platform", "aisha", "#0ea5e9", 1),
            ("MOB", "Mobile App v3", "Mobile", "tom", "#8b5cf6", 2),
            ("DS", "Design System", "Design", "nora", "#f59e0b", 3),
        ]
        titles = [
            "Set up CI pipeline", "Design database schema", "Build authentication flow",
            "Implement audit log", "Write API documentation", "Migrate legacy records",
            "Add rate limiting", "Refactor billing module", "Fix flaky integration tests",
            "Improve dashboard load time", "Accessibility audit", "Onboarding walkthrough",
            "Set up error monitoring", "Localise date formats", "Draft rollout plan",
        ]
        for key, name, team, owner, color, priority in specs:
            project, _ = Project.objects.get_or_create(
                key=key,
                defaults=dict(
                    name=name, description=f"{name} workstream inside the Apollo programme.",
                    parent=programme, status="active", priority=priority, owner=created[owner],
                    team=teams[team], workflow=workflows["Standard board"], color=color,
                    start_date=today - timedelta(days=30), due_date=today + timedelta(days=90),
                    budget=random.choice([80000, 120000, 200000]),
                ),
            )
            members = [created[owner], created["marco"], created["dana"]] + [
                u for u in created.values() if u.team_id == teams[team].pk
            ]
            for user in set(members):
                ProjectMembership.objects.get_or_create(
                    project=project,
                    user=user,
                    defaults={
                        "role": user.role,
                        "side": "internal",
                        "position": user.title,
                        "role_description": f"{user.title} on {project.key}.",
                        "responsibility": DELIVERY_RESPONSIBILITIES.get(
                            user.username, "Delivery of the agreed scope for this workstream."
                        ),
                        "remarks": "",
                        "is_primary_contact": user.pk == created[owner].pk,
                    },
                )
            for username, position, role_desc, responsibility, remark in CLIENT_BRIEFS:
                ProjectMembership.objects.get_or_create(
                    project=project,
                    user=created[username],
                    defaults={
                        "role": created[username].role,
                        "side": "client",
                        "position": position,
                        "role_description": role_desc,
                        "responsibility": responsibility,
                        "remarks": remark,
                        "is_primary_contact": username == "priya",
                        "allocation": 30,
                    },
                )

            if projects_exist:
                continue  # tasks and milestones already generated on an earlier run

            for offset, milestone in enumerate(["Discovery complete", "Beta release", "GA launch"]):
                Milestone.objects.create(
                    project=project, name=milestone, due_date=today + timedelta(days=25 * (offset + 1)),
                    completed=offset == 0,
                )

            states = list(project.states)
            assignees = [m for m in set(members)]
            for index in range(random.randint(9, 13)):
                state = random.choice(states)
                start = today - timedelta(days=random.randint(0, 25))
                task = Task.objects.create(
                    project=project, title=random.choice(titles), state=state,
                    priority=random.randint(1, 4), assignee=random.choice(assignees),
                    reporter=created[owner], order=index, start_date=start,
                    due_date=start + timedelta(days=random.randint(3, 30)),
                    estimate_hours=random.choice([4, 8, 16, 24, 40]),
                    spent_hours=random.choice([0, 2, 6, 12]),
                    description="Auto-generated demo task.",
                )
                task.labels.add(random.choice(labels))
                if index % 4 == 3:
                    Task.objects.create(
                        project=project, parent=task, title=f"Sub-task of {task.title[:30]}",
                        state=states[0], assignee=random.choice(assignees), reporter=created[owner],
                        priority=3, order=index,
                    )
            Activity.log(created[owner], f"created project {project.key}", project=project)

        self.seed_timeline(Project.objects.get(key="PLAT"), created, today)
        self.seed_tickets(created)
        self.seed_sprints(created, today)
        self.stdout.write(self.style.SUCCESS("demo data seeded (all demo users password: demo1234)"))

    def seed_tickets(self, created):
        """A spread of raised items per workstream: bugs, change requests, questions."""
        from apps.tickets.models import Ticket, TicketComment, TicketEvent

        # project key -> list of (type, title, status, priority, reporter, assignee, extra)
        rows = {
            "PLAT": [
                ("bug", "Checkout 500s under load", "in_progress", 1, "mei", "lena",
                 {"severity": 1, "component": "Checkout",
                  "steps_to_reproduce": "1. Add 3 items to the basket\n2. Apply a promo code\n3. Submit payment",
                  "expected_result": "Order confirmation page", "actual_result": "HTTP 500, basket cleared",
                  "environment": "Chrome 128 / Windows 11 / staging"}),
                ("change_request", "Add SSO against client Entra ID tenant", "accepted", 2, "victor", "aisha",
                 {"component": "Auth"}),
                ("other", "Which timezone are audit-log timestamps stored in?", "new", 3, "priya", None, {}),
                ("bug", "Password reset email links expire immediately", "resolved", 2, "guest1", "sam",
                 {"severity": 2, "component": "Auth", "resolution": "Token TTL was 0s in staging config; set to 3600s."}),
                ("bug", "Nightly data migration job stalled at 02:14", "closed", 1, "sam", "sam",
                 {"severity": 1, "resolution": "Disk full on the batch host. Cleared and re-ran; added a disk alert."}),
            ],
            "MOB": [
                ("bug", "App crashes on cold start (Android 13)", "triaged", 1, "raj", "raj",
                 {"severity": 1, "component": "Startup", "environment": "Pixel 6 / Android 13"}),
                ("feature", "Offline mode for the order list", "new", 3, "mei", None, {"component": "Orders"}),
                ("change_request", "Move the phased-rollout toggle into Settings", "in_progress", 3, "dana", "ivy", {}),
            ],
            "DS": [
                ("change_request", "Dark-mode contrast fails WCAG AA on secondary buttons", "accepted", 2, "nora", "nora",
                 {"component": "Buttons"}),
                ("task", "Publish the icon set to the shared package", "in_progress", 3, "nora", "nora", {}),
                ("other", "Do we support right-to-left layouts in v3?", "new", 4, "priya", None, {}),
            ],
        }
        for key, tickets in rows.items():
            project = Project.objects.filter(key=key).first()
            if not project:
                continue
            for type_, title, status, priority, reporter, assignee, extra in tickets:
                if Ticket.objects.filter(project=project, title=title).exists():
                    continue
                ticket = Ticket(
                    project=project, type=type_, title=title, status=status, priority=priority,
                    reporter=created.get(reporter), assignee=created.get(assignee),
                    description=f"Raised during the {project.name} workstream.",
                    **extra,
                )
                if status == "resolved":
                    ticket.resolved_at = timezone.now()
                if status in ("closed", "rejected"):
                    ticket.closed_at = timezone.now()
                ticket.save()
                for person in (created.get(reporter), created.get(assignee)):
                    if person:
                        ticket.watchers.add(person)
                TicketEvent.log(ticket, created.get(reporter), f"raised this {ticket.get_type_display().lower()}")
                if assignee:
                    TicketEvent.log(ticket, created.get("marco"), f"assigned to {created[assignee].display_name}")
                if status in ("resolved", "closed"):
                    TicketComment.objects.create(
                        ticket=ticket, author=created.get(assignee) or created["marco"],
                        body=ticket.resolution or "Handled — see linked work.",
                    )

    def seed_timeline(self, project, created, today):
        """Two text entries so the BRD has something to build from without any audio."""
        from django.utils import timezone as tz

        from apps.intake.models import TimelineEntry

        entries = [
            (
                "Kick-off workshop notes",
                "note",
                7,
                created["marco"],
                "Priya Nair (Client, Product Owner) opened: the current platform cannot handle the "
                "December peak — 4x traffic, and last year checkout queued for 40 seconds.\n"
                "Victor Alvarez (Client, CTO) required SSO against their Entra ID tenant and all "
                "personal data to stay in the EU region.\n"
                "Mei Tan (Client, Operations Lead) asked for a two-week parallel run and an operations "
                "dashboard showing order state in near real time.\n"
                "Aisha Khan (our side) proposed splitting the rebuild into platform, mobile and design "
                "system workstreams with a shared API contract agreed in week 3.",
            ),
            (
                "Change request: phased rollout",
                "change_request",
                2,
                created["dana"],
                "Client asked to move from a single cutover to a phased rollout: internal users first, "
                "then 10% of customers, then the rest over three weeks. Priya Nair confirmed the "
                "December freeze still applies. Victor Alvarez wants a documented rollback path per "
                "phase. This supersedes the single cutover assumed at kick-off.",
            ),
        ]
        for title, kind, days_ago, author, body in entries:
            TimelineEntry.objects.get_or_create(
                project=project,
                title=title,
                defaults={
                    "kind": kind,
                    "body": body,
                    "author": author,
                    "occurred_at": tz.now() - timedelta(days=days_ago),
                    "status": "ready",
                    "include_in_brd": True,
                    "processing_log": "[seed] created as demo content",
                },
            )

    def seed_sprints(self, created, today):
        """A worked Sprint -> Requirement -> Acceptance Criteria example on PLAT."""
        from apps.sprints.models import (
            AcceptanceCriterion, Dependency, ExternalService, FieldSpec, Requirement,
            RequirementRisk, Sprint, UnitTestCase,
        )

        project = Project.objects.filter(key="PLAT").first()
        if not project:
            return

        sprint, _ = Sprint.objects.get_or_create(
            project=project, name="Sprint 1 — Participant Management",
            defaults=dict(
                goal="Deliver participant creation with full validation and field-level rules.",
                status="active", start_date=today - timedelta(days=5),
                end_date=today + timedelta(days=9), capacity_points=21, owner=created["aisha"],
            ),
        )

        backlog_req, _ = Requirement.objects.get_or_create(
            project=project, title="Authentication API for participant screens",
            defaults=dict(
                description="Token-based auth endpoints the participant UI depends on.",
                user_story="As a developer I want a stable auth API so that participant screens can call it.",
                user_story_id="PLAT-STORY-40", priority=2, status="in_progress",
                estimate_points=8, assignee=created["lena"], reporter=created["aisha"], order=1,
            ),
        )

        req, created_req = Requirement.objects.get_or_create(
            project=project, title="Add Participant",
            defaults=dict(
                sprint=sprint,
                description="An operator can create a participant record with validated personal details.",
                user_story="As an operator I want to add a participant so that they can take part in the programme.",
                user_story_id="PLAT-STORY-41", priority=1, status="in_progress",
                estimate_points=5, assignee=created["lena"], reporter=created["priya"], order=0,
            ),
        )
        if not created_req:
            return  # already seeded — keep idempotent

        statements = [
            "User can add participant.",
            "User can enter first name.",
            "User can enter middle name.",
            "User can enter last name.",
            "Mandatory fields must be validated.",
            "Invalid data must display an appropriate validation message.",
            "Duplicate participant records shall be handled according to defined business rules.",
            "Participant shall be successfully saved after valid data entry.",
        ]
        for i, text in enumerate(statements):
            AcceptanceCriterion.objects.create(requirement=req, order=i, format="statement", statement=text)
        AcceptanceCriterion.objects.create(
            requirement=req, order=len(statements), format="gwt",
            given_text="a valid user is logged in",
            when_text='the user selects "Add Participant"',
            then_text="the participant creation form should be displayed",
        )

        fields = [
            ("First Name", "text", "yes", "", 50, "Alphabetic / configured length"),
            ("Middle Name", "text", "no", "", 50, "Configured length"),
            ("Last Name", "text", "yes", "", 50, "Alphabetic / configured length"),
            ("Email", "email", "depends", "Required when notifications are enabled", 254, "Valid email format"),
            ("Mobile Number", "phone", "depends", "Required when SMS is enabled", 20, "Configured phone validation"),
        ]
        for i, (name, ftype, mandatory, cond, maxlen, validation) in enumerate(fields):
            FieldSpec.objects.create(
                requirement=req, order=i, name=name, field_type=ftype, mandatory=mandatory,
                mandatory_condition=cond, max_length=maxlen, validation=validation,
            )

        UnitTestCase.objects.create(
            project=project, requirement=req, title="Save participant with valid data", test_type="positive",
            preconditions="Operator is logged in; participant form is open.",
            input_data="First=Jane, Middle=A, Last=Doe, Email=jane@example.com, Mobile=+15551234567",
            steps="1. Fill all fields with valid data\n2. Submit the form",
            expected_result="Participant is saved and a success message is shown.",
            status="pass", developer=created["lena"], executed_on=today - timedelta(days=1),
        )
        UnitTestCase.objects.create(
            project=project, requirement=req, title="Missing mandatory first name shows validation message",
            test_type="validation",
            preconditions="Operator is logged in; participant form is open.",
            input_data="First=(empty), Last=Doe",
            steps="1. Leave First Name empty\n2. Submit the form",
            expected_result="Form blocks submit and shows 'First Name is required'.",
            status="not_run",
        )

        RequirementRisk.objects.create(
            requirement=req, category="third_party_integration",
            title="Payment provider may not support the required subscription flow",
            description="Participant onboarding may trigger a Stripe subscription that needs extra configuration.",
            likelihood=3, impact=4, mitigation="Spike the Stripe subscription API in the first two days.",
            owner=created["aisha"], status="open", target_date=today + timedelta(days=3),
        )
        RequirementRisk.objects.create(
            requirement=req, category="client_dependency",
            title="Client must provide the participant import file",
            description="Bulk participant data cannot be loaded until the client delivers the agreed CSV.",
            likelihood=4, impact=3, mitigation="Chase Priya for the file; build against a sample in the meantime.",
            owner_note="Priya Nair (Client PO)", status="mitigating", target_date=today + timedelta(days=4),
        )

        Dependency.objects.create(
            requirement=req, kind="internal", title="Auth API must be available before participant UI",
            description="The Add Participant screen calls the token-based auth endpoints.",
            depends_on=backlog_req, owner=created["lena"], due_date=today + timedelta(days=2),
            status="in_progress",
        )
        Dependency.objects.create(
            requirement=req, kind="external", title="Client must provide SMTP credentials",
            description="Confirmation email to new participants needs a working mail relay.",
            external_type="SMTP credentials", owner_note="Victor Alvarez (Client CTO)",
            due_date=today + timedelta(days=6), status="pending",
        )

        services = [
            ("stripe", "Stripe account (live + test keys)", "client", "requested",
             "Needed for participant subscription billing."),
            ("smtp", "SMTP relay credentials", "client", "identified",
             "Host, port, username and password for transactional email."),
            ("cloud_account", "AWS account for the staging environment", "internal", "configured",
             "Account 1234-5678-9012; access via the platform SSO role."),
        ]
        for category, name, provided_by, status, description in services:
            svc, made = ExternalService.objects.get_or_create(
                project=project, name=name,
                defaults=dict(
                    category=category, provided_by=provided_by, status=status, description=description,
                    owner=created["aisha"] if provided_by == "internal" else None,
                    owner_note="" if provided_by == "internal" else "Priya Nair (Client PO)",
                    expected_date=today + timedelta(days=5),
                ),
            )
            if made and category in ("stripe", "smtp"):
                svc.requirements.add(req)
