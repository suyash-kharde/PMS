"""Gather everything the generator reads about a project.

Precedence for the three standard documents: the project's own ProjectDocument content
when it is non-empty, else the organisation baseline in
`apps.projects.models.DOC_DEFAULT_CONTENT`, else "".
"""

from apps.aimodels.runtime import config_for
from apps.intake.services.brd import condense, estimate_tokens, stakeholder_block
from apps.projects.models import DOC_DEFAULT_CONTENT, ProjectDocument

TIMELINE_KINDS = ["note", "text", "meeting", "decision", "change_request"]
TICKET_TYPES = ["feature", "change_request"]


def _doc(project, kind):
    row = ProjectDocument.objects.filter(project=project, kind=kind).first()
    content = (row.content or "").strip() if row else ""
    origin = "project"
    if not content and kind in DOC_DEFAULT_CONTENT:
        content = DOC_DEFAULT_CONTENT[kind].strip()
        origin = "baseline"
    return {
        "present": bool(content),
        "frozen": bool(row and row.is_frozen),
        "origin": origin if content else "missing",
        "chars": len(content),
        "content": content,
    }


def _brd(project):
    from apps.intake.models import BRD

    brd = BRD.objects.filter(project=project).first()
    version = brd.current if brd else None
    markdown = version.markdown if version else ""
    return {
        "present": bool(markdown),
        "frozen": bool(brd and brd.is_frozen),
        "version": version.number if version else 0,
        "chars": len(markdown),
        "content": markdown,
    }


def _timeline(project):
    rows = (
        project.timeline.filter(kind__in=TIMELINE_KINDS)
        .exclude(status__in=["draft", "failed", "processing", "queued"])
        .order_by("occurred_at")
    )
    out = []
    for entry in rows:
        body = (entry.summary or entry.body or entry.transcript or "").strip()
        if body:
            out.append({"title": entry.title, "kind": entry.get_kind_display(), "body": body})
    return out


def _tickets(project):
    rows = project.tickets.filter(type__in=TICKET_TYPES).exclude(status__in=["closed", "rejected", "resolved"])
    return [{"key": t.key, "type": t.get_type_display(), "title": t.title, "detail": t.description or ""} for t in rows]


def collect(project):
    """-> dict with everything the generator needs, plus a `meta` summary for the UI."""
    config = config_for("llm")
    budget = int(config.get("context_tokens") or 16000)

    build_plan = _doc(project, "build_plan")
    dev_instructions = _doc(project, "dev_instructions")
    tech_stack = _doc(project, "tech_stack")
    brd = _brd(project)
    roster = stakeholder_block(project)
    timeline = _timeline(project)
    tickets = _tickets(project)

    # Condense the big free-text blocks so the research prompt fits the context window.
    # Each doc gets a slice of ~30% of the window; the rest is left for the reply + schema.
    per_doc = max(1500, int(budget * 0.30))
    for doc, label in ((build_plan, "Build Plan"), (dev_instructions, "Development Instructions"),
                       (tech_stack, "Tech Stack"), (brd, "BRD")):
        text = doc.get("content", "")
        if text and estimate_tokens(text) > per_doc:
            doc["content_full_chars"] = len(text)
            doc["content"] = condense(text, config, label=label)
            doc["condensed"] = True

    timeline_blob = "\n\n".join(f"### {e['title']} ({e['kind']})\n{e['body']}" for e in timeline)
    if timeline_blob and estimate_tokens(timeline_blob) > per_doc:
        timeline_blob = condense(timeline_blob, config, label="Project timeline")

    meta = {
        "build_plan": {k: build_plan[k] for k in ("present", "frozen", "origin", "chars")},
        "dev_instructions": {k: dev_instructions[k] for k in ("present", "frozen", "origin", "chars")},
        "tech_stack": {k: tech_stack[k] for k in ("present", "frozen", "origin", "chars")},
        "brd": {k: brd[k] for k in ("present", "frozen", "version", "chars")},
        "timeline_entries": len(timeline),
        "tickets": len(tickets),
        "roster_present": roster != "No stakeholders recorded yet.",
    }

    return {
        "project": {
            "key": project.key,
            "name": project.name,
            "description": project.description or "",
            "status": project.get_status_display(),
            "priority": project.get_priority_display(),
            "start_date": project.start_date.isoformat() if project.start_date else None,
            "due_date": project.due_date.isoformat() if project.due_date else None,
        },
        "build_plan": build_plan["content"],
        "dev_instructions": dev_instructions["content"],
        "tech_stack": tech_stack["content"],
        "brd": brd["content"],
        "roster": roster,
        "timeline": timeline_blob,
        "tickets": tickets,
        "meta": meta,
    }


def source_quality(meta):
    """A short human verdict for the start page / run summary."""
    have = []
    if meta["build_plan"]["present"]:
        have.append("Build Plan" + ("" if meta["build_plan"]["origin"] == "project" else " (none — using BRD)"))
    if meta["dev_instructions"]["present"]:
        have.append("Dev Instructions" + ("" if meta["dev_instructions"]["origin"] == "project" else " (org baseline)"))
    if meta["tech_stack"]["present"]:
        have.append("Tech Stack" + ("" if meta["tech_stack"]["origin"] == "project" else " (org baseline)"))
    if meta["brd"]["present"]:
        have.append(f"BRD v{meta['brd']['version']}")
    if meta["timeline_entries"]:
        have.append(f"{meta['timeline_entries']} timeline note(s)")
    if meta["tickets"]:
        have.append(f"{meta['tickets']} open ticket(s)")
    return ", ".join(have) or "no usable project data"
