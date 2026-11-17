"""Throwaway dataset for the smoke suites.

Everything it creates is prefixed `smoke` (users) or keyed `SMOKE` (customer/project),
so the suites never depend on — or damage — real data. Call teardown() when done.
"""

from datetime import timedelta

from django.utils import timezone

from apps.accounts.models import Role, Team, User
from apps.projects.models import Customer, Project, ProjectMembership, Task, Workflow

PREFIX = "smoke_"
CUSTOMER_CODE = "SMOKE"
PROJECT_KEY = "SMOKE"

PEOPLE = [
    # username suffix, role slug, first, last, manager suffix, side, position
    ("director", "director", "Dora", "Director", None, "internal", "Delivery Director"),
    ("manager", "manager", "Manny", "Manager", "director", "internal", "Project Manager"),
    ("lead", "lead", "Lee", "Lead", "manager", "internal", "Tech Lead"),
    ("member", "member", "Mia", "Member", "lead", "internal", "Engineer"),
    ("guest", "guest", "Gus", "Guest", "director", "internal", "Observer"),
    ("client", "guest", "Cleo", "Client", None, "client", "Client Product Owner"),
]


def build():
    """Create (or reuse) the scratch dataset. Returns dict of handles."""
    roles = {r.slug: r for r in Role.objects.all()}
    team, _ = Team.objects.get_or_create(name="Smoke Team")
    sub_team, _ = Team.objects.get_or_create(name="Smoke Sub Team", defaults={"parent": team})

    users = {}
    for suffix, role_slug, first, last, _manager, _side, title in PEOPLE:
        username = PREFIX + suffix
        user = User.objects.filter(username=username).first()
        if user is None:
            user = User.objects.create_user(
                username=username, password="smoke1234", first_name=first, last_name=last,
                email=f"{username}@example.com",
            )
        user.role = roles.get(role_slug)
        user.team = sub_team if role_slug in ("lead", "member") else team
        user.title = title
        user.save()
        users[suffix] = user

    for suffix, _role, _f, _l, manager, _side, _t in PEOPLE:
        if manager:
            users[suffix].manager = users[manager]
            users[suffix].save(update_fields=["manager"])

    customer, _ = Customer.objects.get_or_create(
        code=CUSTOMER_CODE,
        defaults={"name": "Smoke Test Customer", "industry": "Testing", "account_manager": users["manager"]},
    )

    today = timezone.localdate()
    project, created = Project.objects.get_or_create(
        key=PROJECT_KEY,
        defaults={
            "name": "Smoke Test Project", "customer": customer, "status": "active", "priority": 2,
            "owner": users["lead"], "team": sub_team, "workflow": Workflow.default(),
            "start_date": today - timedelta(days=10), "due_date": today + timedelta(days=30),
            "description": "Scratch project used by the smoke suites.",
        },
    )

    briefs = {
        "client": ("Primary client contact for the smoke project.", "Signs off scope.", "Prefers written updates."),
        "lead": ("Technical lead on the smoke project.", "Delivery of the agreed scope.", ""),
    }
    for suffix, _role, _f, _l, _m, side, position in PEOPLE:
        role_desc, responsibility, remarks = briefs.get(suffix, (f"{position} on this project.", "Contributes to delivery.", ""))
        ProjectMembership.objects.get_or_create(
            project=project,
            user=users[suffix],
            defaults={
                "role": users[suffix].role, "side": side, "position": position,
                "role_description": role_desc, "responsibility": responsibility, "remarks": remarks,
                "is_primary_contact": suffix in ("client", "lead"),
            },
        )

    if created or not project.tasks.exists():
        states = list(project.states)
        for index, title in enumerate(["Discovery", "Build the thing", "Review the thing", "Ship it"]):
            task = Task.objects.create(
                project=project, title=f"Smoke: {title}", state=states[index % len(states)],
                priority=(index % 4) + 1, assignee=users["member"], reporter=users["lead"],
                order=index, start_date=today - timedelta(days=5),
                due_date=today + timedelta(days=index * 5), estimate_hours=8, spent_hours=index,
            )
            if index == 0:
                Task.objects.create(
                    project=project, parent=task, title="Smoke: sub-task", state=states[0],
                    assignee=users["member"], reporter=users["lead"], priority=3,
                )

    return {"users": users, "customer": customer, "project": project, "team": team}


def teardown():
    Project.objects.filter(key=PROJECT_KEY).delete()
    Customer.objects.filter(code=CUSTOMER_CODE).delete()
    User.objects.filter(username__startswith=PREFIX).delete()
    Team.objects.filter(name__startswith="Smoke ").delete()
