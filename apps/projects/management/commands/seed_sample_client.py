"""Create one realistic sample customer, project and set of people (safe to re-run).

    python manage.py seed_sample_client

Uses whichever internal staff already exist (matched by username) and adds two
new client-side contacts. Everything is get_or_create, so re-running is a no-op
once it exists.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Role, Team, User
from apps.intake.models import BRD, TimelineEntry
from apps.projects.models import (
    Activity, Customer, Label, Milestone, Project, ProjectMembership, Task, Workflow,
)

INTERNAL_TEAM = [
    # username, position on this project, role description, responsibility, allocation%, primary
    ("prafull", "Quality Control Head",
     "Owns inspection quality standards and final sign-off for this account.",
     "Accountable for AQL compliance and inspection report accuracy.", 40, True),
    ("saudagar", "Floor Manager & Capacity Planning",
     "Coordinates factory floor access, production schedules and capacity for inspections.",
     "Aligns inspection windows with production capacity across the contracted factories.", 30, False),
    ("anarwade", "QC Executive",
     "Executes on-site inspections and defect documentation.",
     "Performs pre-production, mid-line and final inspections; logs defects with photo evidence.", 80, False),
    ("amaan", "Business Development Associate",
     "Manages the commercial relationship and contract renewals.",
     "Owns pricing, SOW changes and escalation on commercial terms.", 15, False),
    ("sawant", "Business Development",
     "Supports account growth and coordinates onboarding of new factories under this account.",
     "Sources and vets additional factory capacity as the account scales.", 10, False),
    ("chaitanya", "Software Engineer",
     "Maintains the client reporting portal integration for this account.",
     "Builds and maintains automated inspection-report exports to the client's system.", 10, False),
]

CLIENT_CONTACTS = [
    # username, first, last, title, position, role description, responsibility, remarks, allocation%, primary
    ("emma.fischer", "Emma", "Fischer", "Merchandising Manager", "Merchandising Manager",
     "Client-side point of contact for order specs, fabric approvals and shipment schedules.",
     "Approves fabric/trim submissions and confirms ship dates.",
     "Prefers a weekly status call on Wednesdays.", 20, True),
    ("liam.brooks", "Liam", "Brooks", "QA Director", "QA Director",
     "Client technical authority for quality standards and AQL requirements.",
     "Sets AQL levels, reviews inspection reports, approves corrective actions.",
     "Requires defect photos attached to every inspection entry.", 15, False),
]

TASKS = [
    # title, assignee, state_name, priority, est_hours, spent_hours, labels
    ("Factory audit — Unit 3 (Meridian)", "anarwade", "Done", 2, 16, 16, ["inspection"]),
    ("Draft AQL 2.5 inspection checklist for SS26", "prafull", "Done", 1, 8, 8, ["documentation"]),
    ("Train floor QC staff on new checklist", "saudagar", "In review", 2, 6, 5, ["documentation"]),
    ("Pre-production sample approval — Fabric lot A12", "anarwade", "In progress", 1, 6, 3, ["inspection"]),
    ("Mid-line inspection — Spring collection, Line 2", "anarwade", "To do", 2, 12, 0, ["inspection"]),
    ("Set up automated inspection report export to client portal", "chaitanya", "In progress", 3, 20, 6, []),
    ("Corrective action: stitching defect on style MQ-204", "prafull", "In progress", 1, 4, 2, ["corrective-action"]),
    ("Schedule final random inspection — Batch 12", "saudagar", "Backlog", 2, 3, 0, ["inspection"]),
    ("Renew SOW pricing for Q3 factory additions", "amaan", "Backlog", 3, 4, 0, ["client-request"]),
    ("Onboard 4th factory — capacity assessment", "sawant", "To do", 3, 10, 0, ["client-request"]),
]

MILESTONES = [
    ("Factory onboarding & audit sign-off", 10, True),
    ("Pre-production sample approval", 25, False),
    ("Mid-line inspection complete", 50, False),
    ("Final random inspection & shipment release", 70, False),
]

# title, kind, days_ago, author username, body, include_in_brd, brd_applied
TIMELINE_ENTRIES = [
    (
        "Kick-off call with Meridian Apparel", "meeting", 20, "prafull",
        "Liam Brooks (Client, QA Director) opened by setting AQL 2.5, General Inspection Level II as the "
        "standard across all three contracted factories, with zero tolerance (0 AQL) on critical safety "
        "defects.\nEmma Fischer (Client, Merchandising Manager) asked for a weekly status call every "
        "Wednesday and wants ship-date risk flagged at least 10 days ahead of any slip.\nPrafull Baheti "
        "(our side) proposed three inspection checkpoints per style: pre-production sample, mid-line, and "
        "final random inspection, with photo evidence attached to every logged defect.\nSaudagar Jadhav "
        "(our side) confirmed floor access has been arranged at all three factories starting next week.",
        True, True,
    ),
    (
        "Decision: AQL 2.5 standard adopted", "decision", 18, "prafull",
        "Agreed with Liam Brooks (Client, QA Director): all inspections on this account will use ANSI/ASQ "
        "Z1.4, AQL 2.5 for major defects and AQL 4.0 for minor defects, General Inspection Level II. "
        "Critical defects (safety, mislabeling, prohibited materials) carry 0 AQL and trigger an automatic "
        "hold. This supersedes the factories' own house standards for Meridian orders.",
        True, True,
    ),
    (
        "Factory audit report — Unit 3", "text", 10, "anarwade",
        "Completed the onboarding audit at Unit 3. Findings: stitching stations 4-6 need needle-detection "
        "recalibration before pre-production sampling; metal detector logs were incomplete for the last two "
        "weeks. Corrective action requested with a 5-day turnaround; re-audit scheduled once closed out. "
        "No critical findings — factory cleared to proceed with pre-production samples in the meantime.",
        True, False,
    ),
    (
        "Change request: onboard a 4th factory", "change_request", 5, "sawant",
        "Emma Fischer (Client, Merchandising Manager) requested adding a 4th factory to cover overflow "
        "capacity for the SS26 reorder. Victor scope note: same AQL 2.5 standard applies, same reporting "
        "cadence. Pending: updated SOW pricing from our side before floor access is arranged. This is in "
        "addition to, not a replacement for, the three factories already contracted.",
        True, False,
    ),
]

BRD_MARKDOWN = """# Meridian QA Program — Business Requirement Document

## Overview
Meridian Apparel Group has contracted Forbesvyncke to provide third-party quality assurance and
inspection services across its Spring/Summer 2026 collection, produced at three factories in South Asia
(a fourth is under discussion — see Open Items).

## Stakeholders
| Name | Side | Role |
| --- | --- | --- |
| Emma Fischer | Client | Merchandising Manager — order specs, fabric approvals, ship dates |
| Liam Brooks | Client | QA Director — quality standards, AQL requirements, corrective actions |
| Prafull Baheti | Forbesvyncke | Quality Control Head — inspection standards and sign-off |
| Saudagar Jadhav | Forbesvyncke | Floor Manager & Capacity Planning |
| Anarwade | Forbesvyncke | QC Executive — on-site inspections |

## Quality standard
- ANSI/ASQ Z1.4, AQL 2.5 for major defects, AQL 4.0 for minor defects, General Inspection Level II.
- Critical defects (safety, mislabeling, prohibited materials): 0 AQL, automatic hold.
- Every logged defect must include photo evidence.

## Inspection checkpoints
1. **Pre-production sample approval** — one sample per style/colorway before bulk cutting starts.
2. **Mid-line inspection** — once a production line reaches 20–80% completion.
3. **Final random inspection** — before shipment release, per AQL sampling tables.

## Reporting
- Weekly status call every Wednesday with Emma Fischer.
- Ship-date risk flagged at least 10 days ahead of any anticipated slip.
- Inspection reports delivered within 24 hours of each checkpoint, with photos attached.

## Assumptions
- Factory floor access is arranged directly with Meridian's nominated factories.
- Corrective actions are tracked to closure before the next checkpoint on the same style.

## Open items
- Onboarding a 4th factory for overflow capacity — pending updated SOW pricing (raised by Emma Fischer).
- Unit 3 needle-detection recalibration and metal-detector log gap — corrective action in progress.

## Out of scope
- In-line production management and scheduling remain Meridian's and the factories' responsibility.
- Compliance/social audits are handled under a separate SOW.
"""


class Command(BaseCommand):
    help = "Seed one realistic sample customer, project, milestones, tasks and people."

    @transaction.atomic
    def handle(self, *args, **options):
        guest_role, _ = Role.objects.get_or_create(slug="guest", defaults={"name": "Guest", "rank": 500})
        team = Team.objects.order_by("pk").first()

        missing = [u for u, *_ in INTERNAL_TEAM if not User.objects.filter(username=u).exists()]
        if missing:
            self.stderr.write(self.style.WARNING(
                f"Skipping missing internal users (not seeded): {', '.join(missing)}"
            ))
        internal = {u.username: u for u in User.objects.filter(username__in=[r[0] for r in INTERNAL_TEAM])}
        owner = internal.get("prafull") or next(iter(internal.values()), None)
        if owner is None:
            self.stderr.write(self.style.ERROR("No matching internal users found — nothing to assign as owner."))
            return

        customer, created = Customer.objects.get_or_create(
            name="Meridian Apparel Group",
            defaults=dict(
                industry="Apparel Manufacturing & Retail",
                website="https://www.meridianapparel.example",
                account_manager=internal.get("amaan"),
                primary_contact=owner,
                contact_email="sourcing@meridianapparel.example",
                contact_phone="+1 415 555 0138",
                color="#0ea5e9",
                description="Mid-size US apparel importer sourcing from three contracted factories in South Asia.",
                notes=(
                    "Tier-1 account. Requires AQL 2.5 inspections across pre-production, mid-line and final "
                    "stages. Contract runs through December 2026, renewed annually."
                ),
            ),
        )
        self.stdout.write(self.style.SUCCESS(f"{'created' if created else 'exists'}: customer {customer.name} ({customer.code})"))

        client_users = {}
        for username, first, last, title, *_ in CLIENT_CONTACTS:
            user, u_created = User.objects.get_or_create(
                username=username,
                defaults=dict(
                    first_name=first, last_name=last, email=f"{username}@meridianapparel.example",
                    role=guest_role, title=title, customer=customer, color="#f59e0b",
                ),
            )
            if u_created:
                user.set_password("demo1234")
                user.save(update_fields=["password"])
            client_users[username] = user
        self.stdout.write(self.style.SUCCESS(f"client contacts ready: {', '.join(client_users)}"))

        today = timezone.localdate()
        workflow = Workflow.default()
        project, created = Project.objects.get_or_create(
            key="MER-QA",
            defaults=dict(
                name="Meridian QA Program – Spring/Summer 2026",
                description=(
                    "Third-party quality assurance and inspection program covering pre-production, "
                    "mid-line and final random inspections for Meridian Apparel Group's SS26 collection "
                    "across three contracted factories."
                ),
                customer=customer, status="active", priority=2, owner=owner, team=team, workflow=workflow,
                start_date=today - timedelta(days=15), due_date=today + timedelta(days=75),
                budget=18000, color="#f59e0b",
            ),
        )
        self.stdout.write(self.style.SUCCESS(f"{'created' if created else 'exists'}: project {project.key} · {project.name}"))

        for username, position, role_desc, responsibility, allocation, primary in INTERNAL_TEAM:
            user = internal.get(username)
            if not user:
                continue
            ProjectMembership.objects.get_or_create(
                project=project, user=user,
                defaults=dict(
                    role=user.role, side="internal", position=position, role_description=role_desc,
                    responsibility=responsibility, is_primary_contact=primary, allocation=allocation,
                ),
            )
        for username, _first, _last, _title, position, role_desc, responsibility, remarks, allocation, primary in CLIENT_CONTACTS:
            user = client_users[username]
            ProjectMembership.objects.get_or_create(
                project=project, user=user,
                defaults=dict(
                    role=user.role, side="client", position=position, role_description=role_desc,
                    responsibility=responsibility, remarks=remarks, is_primary_contact=primary,
                    allocation=allocation,
                ),
            )
        self.stdout.write(self.style.SUCCESS(f"memberships ready: {project.memberships.count()}"))

        for title, kind, days_ago, username, body, include_in_brd, brd_applied in TIMELINE_ENTRIES:
            author = internal.get(username)
            TimelineEntry.objects.get_or_create(
                project=project, title=title,
                defaults=dict(
                    kind=kind, body=body, author=author,
                    occurred_at=timezone.now() - timedelta(days=days_ago),
                    status="ready", include_in_brd=include_in_brd, brd_applied=brd_applied,
                    processing_log="[seed] created as sample content",
                ),
            )
        self.stdout.write(self.style.SUCCESS(f"timeline entries ready: {project.timeline.count()}"))

        brd, _ = BRD.objects.get_or_create(project=project, defaults={"title": f"{project.name} BRD"})
        if not brd.current:
            brd.add_version(
                markdown=BRD_MARKDOWN, summary="Initial BRD from kick-off notes and the AQL decision.",
                created_by=owner, model_name="manual",
            )
            self.stdout.write(self.style.SUCCESS("BRD v1 created"))
        else:
            self.stdout.write("BRD already has a version — left untouched")

        if not created:
            self.stdout.write("project already existed — skipping milestones/tasks/labels")
            return

        for name, offset, completed in MILESTONES:
            Milestone.objects.get_or_create(
                project=project, name=name,
                defaults={"due_date": today + timedelta(days=offset), "completed": completed},
            )

        labels = {}
        for name, color in [
            ("inspection", "#0ea5e9"), ("corrective-action", "#ef4444"),
            ("documentation", "#8b5cf6"), ("client-request", "#f59e0b"),
        ]:
            labels[name], _ = Label.objects.get_or_create(name=name, project=project, defaults={"color": color})

        states = {s.name: s for s in project.states}
        for index, (title, username, state_name, priority, est, spent, label_names) in enumerate(TASKS):
            assignee = internal.get(username)
            task, t_created = Task.objects.get_or_create(
                project=project, title=title,
                defaults=dict(
                    state=states.get(state_name), priority=priority, assignee=assignee, reporter=owner,
                    order=index, start_date=today - timedelta(days=10 - index),
                    due_date=today + timedelta(days=5 * (index + 1)),
                    estimate_hours=est, spent_hours=spent,
                    description="Sample task for the Meridian QA program.",
                ),
            )
            if t_created:
                for label_name in label_names:
                    task.labels.add(labels[label_name])

        Activity.log(owner, f"created project {project.key}", project=project)
        self.stdout.write(self.style.SUCCESS("sample customer, project and people seeded"))
