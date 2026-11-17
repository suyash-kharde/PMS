"""Plain-dict serializers for the MCP tools. Kept separate from tools.py so the
tool handlers stay short. Every function returns JSON-serializable primitives.
"""


def _user(u):
    return u.display_name if u else None


def _dt(value):
    return value.isoformat() if value else None


# --------------------------------------------------------------------------- tickets
def ticket_brief(t):
    return {
        "key": t.key,
        "type": t.type,
        "title": t.title,
        "status": t.status,
        "priority": t.get_priority_display(),
        "severity": t.get_severity_display() if t.severity else None,
        "component": t.component or None,
        "assignee": _user(t.assignee),
        "age_days": t.age_days,
        "linked_task": t.linked_task.get_absolute_url() if t.linked_task_id else None,
    }


def ticket_full(t):
    data = ticket_brief(t)
    data.update(
        {
            "description": t.description,
            "steps_to_reproduce": t.steps_to_reproduce,
            "expected_result": t.expected_result,
            "actual_result": t.actual_result,
            "environment": t.environment,
            "resolution": t.resolution,
            "reporter": _user(t.reporter),
            "duplicate_of": t.duplicate_of.key if t.duplicate_of_id else None,
            "linked_task_id": t.linked_task_id,
            "created_at": _dt(t.created_at),
            "updated_at": _dt(t.updated_at),
            "watchers": t.watchers.count(),
            "comments": [
                {"author": _user(c.author), "body": c.body, "at": _dt(c.created_at)}
                for c in t.comments.select_related("author")
            ],
            "history": [
                {"actor": _user(e.actor), "verb": e.verb, "at": _dt(e.created_at)}
                for e in t.events.select_related("actor")[:40]
            ],
            "attachments": [
                {"name": a.original_name or a.file.name, "url": a.file.url}
                for a in t.attachments.all()
            ],
        }
    )
    return data


# --------------------------------------------------------------------------- sprints
def sprint_brief(s):
    return {
        "key": s.key,
        "name": s.name,
        "goal": s.goal,
        "status": s.status,
        "start_date": _dt(s.start_date),
        "end_date": _dt(s.end_date),
        "capacity_points": s.capacity_points,
        "requirement_count": s.requirement_count,
        "points_committed": s.points_committed,
        "progress": s.progress,
    }


def requirement_brief(r):
    return {
        "key": r.key,
        "title": r.title,
        "status": r.status,
        "priority": r.get_priority_display(),
        "estimate_points": r.estimate_points,
        "sprint": r.sprint.key if r.sprint_id else None,
        "assignee": _user(r.assignee),
    }


def requirement_full(r):
    data = requirement_brief(r)
    data.update(
        {
            "description": r.description,
            "user_story": r.user_story,
            "user_story_id": r.user_story_id,
            "reporter": _user(r.reporter),
            "linked_task_id": r.linked_task_id,
            "acceptance_criteria": [
                {
                    "format": c.format,
                    "text": c.text,
                    "is_met": c.is_met,
                    "notes": c.notes,
                }
                for c in r.criteria.all()
            ],
            "field_specs": [
                {
                    "name": f.name,
                    "type": f.get_field_type_display(),
                    "mandatory": f.mandatory,
                    "mandatory_condition": f.mandatory_condition,
                    "max_length": f.max_length,
                    "validation": f.validation,
                    "default_value": f.default_value,
                    "notes": f.notes,
                }
                for f in r.field_specs.all()
            ],
            "risks": [
                {
                    "category": rk.get_category_display(),
                    "title": rk.title,
                    "description": rk.description,
                    "likelihood": rk.get_likelihood_display(),
                    "impact": rk.get_impact_display(),
                    "score": rk.score,
                    "severity": rk.severity,
                    "mitigation": rk.mitigation,
                    "status": rk.status,
                }
                for rk in r.risks.all()
            ],
            "dependencies": [
                {
                    "kind": d.kind,
                    "title": d.title,
                    "description": d.description,
                    "depends_on": d.depends_on.key if d.depends_on_id else None,
                    "external_type": d.external_type,
                    "status": d.status,
                    "due_date": _dt(d.due_date),
                }
                for d in r.dependencies.select_related("depends_on")
            ],
            "test_cases": [test_case_brief(tc) for tc in r.test_cases.all()],
        }
    )
    return data


def test_case_brief(tc):
    return {
        "key": tc.key,
        "title": tc.title,
        "test_type": tc.get_test_type_display(),
        "status": tc.status,
        "requirement": tc.requirement.key if tc.requirement_id else None,
    }


def test_case_full(tc):
    data = test_case_brief(tc)
    data.update(
        {
            "preconditions": tc.preconditions,
            "input_data": tc.input_data,
            "steps": tc.steps,
            "expected_result": tc.expected_result,
            "actual_result": tc.actual_result,
            "comments": tc.comments,
            "developer": _user(tc.developer),
            "executed_on": _dt(tc.executed_on),
            "story_id": tc.story_id,
        }
    )
    return data


def dev_update_brief(u):
    return {
        "id": u.id,
        "headline": u.headline,
        "kind": u.kind,
        "status": u.status,
        "milestone": u.milestone or None,
        "milestone_done": u.milestone_done,
        "created_by": _user(u.created_by),
        "source": u.source,
        "created_at": _dt(u.created_at),
        "links": {
            "sprint": u.sprint.key if u.sprint_id else None,
            "requirement": u.requirement.key if u.requirement_id else None,
            "ticket": u.ticket.key if u.ticket_id else None,
            "task_id": u.task_id,
        },
    }


def dev_update_full(u):
    data = dev_update_brief(u)
    data.update(
        {
            "details": u.details,
            "files_changed": u.files_changed,
            "branch": u.branch,
            "commit": u.commit,
            "pr_url": u.pr_url,
            "updated_at": _dt(u.updated_at),
        }
    )
    return data


def service_brief(s):
    return {
        "category": s.get_category_display(),
        "name": s.name,
        "description": s.description,
        "provided_by": s.get_provided_by_display(),
        "status": s.status,
        "owner": _user(s.owner) or s.owner_note or None,
        "expected_date": _dt(s.expected_date),
        "received_date": _dt(s.received_date),
        "details": s.details,
        "requirements": [r.key for r in s.requirements.all()],
    }


# --------------------------------------------------------------------------- project
def project_overview(project):
    from apps.projects.models import DOC_KINDS

    docs = {d.kind: {"updated_at": _dt(d.updated_at), "frozen": d.is_frozen} for d in project.documents.all()}
    brd = getattr(project, "brd", None)
    return {
        "key": project.key,
        "name": project.name,
        "description": project.description,
        "status": project.get_status_display(),
        "priority": project.get_priority_display(),
        "start_date": _dt(project.start_date),
        "due_date": _dt(project.due_date),
        "budget": str(project.budget),
        "progress": project.progress,
        "customer": (
            {"name": project.customer.name, "industry": project.customer.industry}
            if project.customer_id
            else None
        ),
        "owner": _user(project.owner),
        "team": project.team.name if project.team_id else None,
        "counts": {
            "open_tickets": project.tickets.exclude(
                status__in=["resolved", "closed", "rejected"]
            ).count(),
            "requirements": project.requirements.count(),
            "sprints": project.sprints.count(),
            "tasks": project.tasks.count(),
        },
        "members": [
            {
                "name": _user(m.user),
                "side": m.get_side_display(),
                "position": m.position,
                "responsibility": m.responsibility,
            }
            for m in project.memberships.select_related("user")
        ],
        "documents": {
            kind: docs.get(kind, {"updated_at": None, "frozen": False}) for kind, _ in DOC_KINDS
        },
        "brd": {"version": brd.current.number, "updated_at": _dt(brd.current.created_at)}
        if brd is not None and brd.current
        else None,
    }


# --------------------------------------------------------------------------- design
def design_workspace(ws):
    """The project's Penpot design workspace, or None if it has none."""
    if ws is None:
        return None
    return {
        "status": ws.status,
        "phase": ws.phase or None,
        "ready": ws.is_ready,
        "file_id": ws.file_id or None,
        "file_name": ws.file_name or None,
        "penpot_project_id": ws.penpot_project_id or None,
        "penpot_project_name": ws.penpot_project_name or None,
        "team_name": ws.team_name or None,
        "workspace_url": ws.workspace_url or None,   # file editor (needs a Penpot login)
        "viewer_url": ws.viewer_url or None,
        "public_share_url": ws.share_url or None,    # no-login, only if a link was minted
        "provisioned_at": _dt(ws.provisioned_at),
        "synced_at": _dt(ws.synced_at),
        "last_error": (ws.last_error or "")[:500] or None,
    }
