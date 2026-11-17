"""The per-project MCP tool registry.

Every handler receives a `Ctx` (project + bound user + token) and the call
arguments, and returns either a string (sent verbatim as text) or a
JSON-serializable object (sent as pretty JSON). Two hard rules for every handler:

  * scope: query through `visible_*(ctx.user)` **and** `.filter(project=ctx.project)`
  * writes: gated by the token's scope flag **and** the bound user's capability
"""

import base64
import json

from django.db.models import Max, Q
from django.utils import timezone

from apps.accounts.permissions import has_cap
from apps.notify import services as notify
from apps.projects.models import Activity
from apps.projects.selectors import visible_tasks
from apps.sprints.models import REQUIREMENT_STATUS, TEST_STATUS
from apps.sprints.selectors import (
    visible_requirements,
    visible_services,
    visible_sprints,
    visible_test_cases,
)
from apps.tickets.models import CLOSED_STATUSES, TICKET_STATUS, TICKET_TYPES
from apps.tickets.selectors import visible_tickets

from . import format as fmt


class Ctx:
    def __init__(self, project, user, token):
        self.project = project
        self.user = user
        self.token = token


class ToolError(Exception):
    """A clean, user-facing failure — returned as an MCP tool error, not a transport error."""


# --------------------------------------------------------------------------- registry
_TOOLS = []


def tool(name, description, schema):
    def deco(fn):
        _TOOLS.append({"name": name, "description": description, "inputSchema": schema, "handler": fn})
        return fn

    return deco


def list_tools():
    return [{"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]} for t in _TOOLS]


def call_tool(ctx, name, arguments):
    entry = next((t for t in _TOOLS if t["name"] == name), None)
    if entry is None:
        return _text(f"Unknown tool: {name}", is_error=True)
    try:
        out = entry["handler"](ctx, arguments or {})
    except ToolError as exc:
        return _text(str(exc), is_error=True)
    except Exception as exc:  # noqa: BLE001 - surface to the model, don't 500 the transport
        return _text(f"{type(exc).__name__}: {exc}", is_error=True)
    if isinstance(out, dict) and isinstance(out.get("content"), list):
        return out  # handler already built a full MCP content response (e.g. get_ticket's images)
    if not isinstance(out, str):
        out = json.dumps(out, indent=2, default=str, ensure_ascii=False)
    return _text(out)


def _text(text, is_error=False):
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


_MAX_INLINE_IMAGES = 6
_MAX_INLINE_IMAGE_BYTES = 8 * 1024 * 1024
# Explicit map, not mimetypes.guess_type(): that function depends on the host's system
# mime.types file, which on at least this server doesn't know .webp at all (returns None)
# even though it's a perfectly normal image type — silently dropping the image with no error.
_IMAGE_EXT_MIMES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
}


def _ticket_content(ticket):
    """get_ticket's response: the usual JSON as text, plus each raster-image attachment
    (screenshots, UI/design references) inlined as a real viewable image — not just its
    filename and a link — so the model can actually see what was attached, up to a few
    images and a size cap per image so one huge upload can't blow up the response."""
    content = [{"type": "text", "text": json.dumps(fmt.ticket_full(ticket), indent=2, default=str, ensure_ascii=False)}]
    embedded = 0
    for a in ticket.attachments.all():
        if embedded >= _MAX_INLINE_IMAGES or not a.is_image:
            continue
        name = (a.original_name or a.file.name).lower()
        ext = name[name.rfind("."):] if "." in name else ""
        mime = _IMAGE_EXT_MIMES.get(ext)
        if mime is None:
            continue  # e.g. .svg — vector, not safe to assume every MCP client renders it
        try:
            if a.file.size > _MAX_INLINE_IMAGE_BYTES:
                continue
            with a.file.open("rb") as f:
                raw = f.read()
        except Exception:  # noqa: BLE001 - a bad/missing file shouldn't break the whole read
            continue
        content.append({"type": "image", "data": base64.b64encode(raw).decode("ascii"), "mimeType": mime})
        embedded += 1
    return {"content": content}


# --------------------------------------------------------------------------- helpers
_OBJ = {"type": "object", "properties": {}, "additionalProperties": False}


def _schema(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


def _clamp(value, default, lo, hi):
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _parse_number(ref, *strip_letters):
    s = str(ref).strip().upper()
    if "-" in s:
        s = s.rsplit("-", 1)[1]
    for letters in strip_letters:
        if s.startswith(letters):
            s = s[len(letters):]
            break
    try:
        return int(s)
    except ValueError:
        raise ToolError(f"Could not read an item number from {ref!r}.")


def _require_write(ctx, kind):
    ok = {"ticket": ctx.token.allow_ticket_write, "sprint": ctx.token.allow_sprint_write}[kind]
    if not ok:
        raise ToolError(
            f"This MCP link is read-only for {kind} data. Turn on '{kind} writes' "
            "on the project's Claude Code tab to allow it."
        )


def _require_cap(ctx, code, obj):
    if not has_cap(ctx.user, code, obj):
        raise ToolError(f"Permission denied: your account lacks '{code}' on this project.")


def _get_ticket(ctx, ref):
    t = visible_tickets(ctx.user).filter(project=ctx.project, number=_parse_number(ref)).first()
    if t is None:
        raise ToolError(f"Ticket {ref} not found in {ctx.project.key}.")
    return t


def _get_requirement(ctx, ref):
    r = (
        visible_requirements(ctx.user)
        .filter(project=ctx.project, number=_parse_number(ref, "R"))
        .prefetch_related("criteria", "field_specs", "risks", "dependencies__depends_on", "test_cases")
        .first()
    )
    if r is None:
        raise ToolError(f"Requirement {ref} not found in {ctx.project.key}.")
    return r


def _get_test_case(ctx, ref):
    tc = visible_test_cases(ctx.user).filter(project=ctx.project, number=_parse_number(ref, "TC")).first()
    if tc is None:
        raise ToolError(f"Test case {ref} not found in {ctx.project.key}.")
    return tc


def _sprint_by_ref(ctx, ref):
    s = visible_sprints(ctx.user).filter(project=ctx.project, number=_parse_number(ref, "S")).first()
    if s is None:
        raise ToolError(f"Sprint {ref} not found in {ctx.project.key}.")
    return s


def _get_task(ctx, ref):
    try:
        task_id = int(str(ref).strip())
    except ValueError:
        raise ToolError(f"task must be a numeric id, got {ref!r}.")
    t = visible_tasks(ctx.user).filter(project=ctx.project, id=task_id).first()
    if t is None:
        raise ToolError(f"Task {ref} not found in {ctx.project.key}.")
    return t


def _get_dev_update(ctx, update_id):
    from apps.devlog.models import DevUpdate

    try:
        pk = int(update_id)
    except (TypeError, ValueError):
        raise ToolError(f"id must be numeric, got {update_id!r}.")
    u = DevUpdate.objects.filter(project=ctx.project, pk=pk).first()
    if u is None:
        raise ToolError(f"Dev update #{update_id} not found in {ctx.project.key}.")
    return u


# =========================================================================== context / RAG
@tool(
    "project_overview",
    "Snapshot of this project: status, dates, customer, team, members, counts, and which "
    "documents (tech stack, dev instructions, build plan, BRD) exist. Start here.",
    _OBJ,
)
def _project_overview(ctx, args):
    return fmt.project_overview(ctx.project)


@tool(
    "search_docs",
    "Semantic search over this project's notes, meeting transcripts, uploaded documents and "
    "the BRD (the same index used by Ask AI). Use for open-ended 'what was decided / discussed / "
    "what does the BRD say about X' questions. Returns the most relevant snippets.",
    _schema(
        {
            "query": {"type": "string", "description": "What to look for"},
            "limit": {"type": "integer", "description": "Max snippets (1-15, default 6)"},
        },
        ["query"],
    ),
)
def _search_docs(ctx, args):
    from apps.rag.services.retrieve import semantic_search

    query = (args.get("query") or "").strip()
    if not query:
        raise ToolError("Provide a 'query'.")
    hits = semantic_search(ctx.project, query, top_k=_clamp(args.get("limit"), 6, 1, 15))
    return {"results": hits}


@tool(
    "ask_project",
    "Ask the project's Ask AI agent a natural-language question. It runs RAG over the project's "
    "documents and, optionally, read-only SQL over its structured data, and returns a synthesized "
    "answer with its sources. Use for questions that need reasoning across several sources.",
    _schema(
        {
            "question": {"type": "string"},
            "use_docs": {"type": "boolean", "description": "Allow document search (default true)"},
            "use_data": {"type": "boolean", "description": "Allow read-only SQL over structured data (default true)"},
        },
        ["question"],
    ),
)
def _ask_project(ctx, args):
    from apps.rag.services.agent import ask

    question = (args.get("question") or "").strip()
    if not question:
        raise ToolError("Provide a 'question'.")
    answer, trace = ask(
        ctx.project,
        question,
        use_rag=args.get("use_docs", True),
        use_sql=args.get("use_data", True),
    )
    return {
        "answer": answer,
        "sources": [{"source_type": s["source_type"], "label": s["label"]} for s in trace.get("sources", [])],
        "sql": trace.get("sql", []),
    }


def _sql_description():
    from apps.rag.services.sql_tool import SCHEMA_HELP

    return (
        "Run one read-only SELECT against this project's structured data. The connection is a "
        "locked-down Postgres role already scoped to this project — you cannot see other projects "
        "or any base table.\n\n" + SCHEMA_HELP
    )


@tool("query_data", _sql_description(), _schema({"sql": {"type": "string"}}, ["sql"]))
def _query_data(ctx, args):
    from apps.rag.services.sql_tool import InvalidQuery, run_query

    sql = (args.get("sql") or "").strip()
    if not sql:
        raise ToolError("Provide 'sql'.")
    try:
        rows = run_query(ctx.project.id, sql)
    except InvalidQuery as exc:
        raise ToolError(f"Query rejected: {exc}")
    return {"rows": rows, "row_count": len(rows)}


@tool(
    "get_document",
    "Return the full markdown of one project document: 'tech_stack', 'dev_instructions', "
    "'build_plan', or 'brd'. Read tech_stack and dev_instructions before writing any code — "
    "they define this project's stack and coding standards.",
    _schema(
        {"kind": {"type": "string", "enum": ["tech_stack", "dev_instructions", "build_plan", "brd"]}},
        ["kind"],
    ),
)
def _get_document(ctx, args):
    kind = (args.get("kind") or "").strip()
    if kind == "brd":
        brd = getattr(ctx.project, "brd", None)
        if brd is None or not brd.markdown:
            raise ToolError("No BRD has been generated for this project yet.")
        return brd.markdown

    from apps.projects.models import DOC_DEFAULT_CONTENT, DOC_KINDS

    if kind not in {k for k, _ in DOC_KINDS}:
        raise ToolError("kind must be one of: tech_stack, dev_instructions, build_plan, brd.")
    doc = ctx.project.documents.filter(kind=kind).first()
    if doc and doc.content:
        return doc.content
    if DOC_DEFAULT_CONTENT.get(kind):
        return (
            DOC_DEFAULT_CONTENT[kind]
            + "\n\n> _Org default — no project-specific version has been written yet._"
        )
    raise ToolError(f"The {kind} document has not been written for this project yet.")


@tool(
    "get_design_workspace",
    "This project's Penpot design workspace: whether it is provisioned, the design file id "
    "and name, the Penpot project, and the URLs to open it. Use this to find the file to "
    "connect the Penpot MCP server to — `file_id` / `workspace_url` identify which design "
    "belongs to this project. Returns {\"design_workspace\": null} if the project has none.",
    _OBJ,
)
def _get_design_workspace(ctx, args):
    from apps.penpot.models import PenpotWorkspace

    ws = PenpotWorkspace.objects.filter(project=ctx.project).first()
    return {"design_workspace": fmt.design_workspace(ws)}


# =========================================================================== tickets
@tool(
    "list_tickets",
    "List tickets (bugs, change requests, incidents, …) on this project. Defaults to open items only.",
    _schema(
        {
            "status": {"type": "string", "enum": [s for s, _ in TICKET_STATUS]},
            "type": {"type": "string", "enum": [t for t, _ in TICKET_TYPES]},
            "assignee": {"type": "string", "description": "Name or username substring"},
            "q": {"type": "string", "description": "Text search over title / description / component"},
            "open_only": {"type": "boolean", "description": "Default true"},
            "limit": {"type": "integer", "description": "1-100, default 30"},
        }
    ),
)
def _list_tickets(ctx, args):
    qs = visible_tickets(ctx.user).filter(project=ctx.project)
    if args.get("open_only", True):
        qs = qs.exclude(status__in=CLOSED_STATUSES)
    if args.get("status"):
        qs = qs.filter(status=args["status"])
    if args.get("type"):
        qs = qs.filter(type=args["type"])
    if args.get("q"):
        term = args["q"]
        qs = qs.filter(Q(title__icontains=term) | Q(description__icontains=term) | Q(component__icontains=term))
    if args.get("assignee"):
        a = args["assignee"]
        qs = qs.filter(
            Q(assignee__username__icontains=a)
            | Q(assignee__first_name__icontains=a)
            | Q(assignee__last_name__icontains=a)
        )
    rows = list(qs.order_by("-created_at")[: _clamp(args.get("limit"), 30, 1, 100)])
    return {"count": len(rows), "tickets": [fmt.ticket_brief(t) for t in rows]}


@tool(
    "get_ticket",
    "Full detail for one ticket by key or number (e.g. 'PLAT-14' or 14): description, "
    "reproduction steps, expected vs actual, environment, comments, history and attachments. "
    "Image attachments (screenshots, UI/design references) are sent as viewable images, not "
    "just a filename and link.",
    _schema({"ref": {"type": "string", "description": "Ticket key or number"}}, ["ref"]),
)
def _get_ticket_tool(ctx, args):
    return _ticket_content(_get_ticket(ctx, args.get("ref")))


@tool(
    "comment_ticket",
    "Post a comment on a ticket (e.g. a proposed fix, an analysis, a status note). "
    "Requires ticket-write on this link.",
    _schema({"ref": {"type": "string"}, "body": {"type": "string"}}, ["ref", "body"]),
)
def _comment_ticket(ctx, args):
    _require_write(ctx, "ticket")
    body = (args.get("body") or "").strip()
    if not body:
        raise ToolError("Provide a non-empty 'body'.")
    ticket = _get_ticket(ctx, args.get("ref"))
    _require_cap(ctx, "ticket.comment", None)

    from apps.tickets.models import TicketComment, TicketEvent

    comment = TicketComment.objects.create(ticket=ticket, author=ctx.user, body=body)
    ticket.watchers.add(ctx.user)
    TicketEvent.log(ticket, ctx.user, "commented via Claude Code")
    notify.ticket_commented(ticket, comment)
    return {"ok": True, "ticket": ticket.key, "comment_id": comment.id}


@tool(
    "set_ticket_status",
    "Move a ticket to a new status, optionally with a resolution note. Requires ticket-write "
    "and the 'ticket.triage' capability.",
    _schema(
        {
            "ref": {"type": "string"},
            "status": {"type": "string", "enum": [s for s, _ in TICKET_STATUS]},
            "resolution": {"type": "string"},
        },
        ["ref", "status"],
    ),
)
def _set_ticket_status(ctx, args):
    _require_write(ctx, "ticket")
    status = args.get("status")
    if status not in dict(TICKET_STATUS):
        raise ToolError(f"Unknown status. One of: {', '.join(s for s, _ in TICKET_STATUS)}.")
    ticket = _get_ticket(ctx, args.get("ref"))
    _require_cap(ctx, "ticket.triage", ticket)

    from apps.tickets.models import TicketEvent

    old = ticket.get_status_display()
    ticket.apply_status(status)
    if args.get("resolution"):
        ticket.resolution = args["resolution"].strip()
    ticket.save()
    TicketEvent.log(ticket, ctx.user, f"moved status from {old} to {ticket.get_status_display()} via Claude Code")
    notify.ticket_status_changed(ticket, ctx.user, old)
    return {"ok": True, "ticket": ticket.key, "status": ticket.status}


@tool(
    "create_ticket",
    "Raise a new ticket on this project (e.g. file a bug you found while working). "
    "Requires ticket-write and the 'ticket.create' capability.",
    _schema(
        {
            "title": {"type": "string"},
            "type": {"type": "string", "enum": [t for t, _ in TICKET_TYPES], "description": "Default 'bug'"},
            "description": {"type": "string"},
            "steps_to_reproduce": {"type": "string"},
            "expected_result": {"type": "string"},
            "actual_result": {"type": "string"},
            "environment": {"type": "string"},
            "component": {"type": "string"},
            "severity": {"type": "integer", "description": "1 Blocker … 4 Trivial"},
            "priority": {"type": "integer", "description": "1 Critical … 4 Low (default 3)"},
        },
        ["title"],
    ),
)
def _create_ticket(ctx, args):
    _require_write(ctx, "ticket")
    _require_cap(ctx, "ticket.create", None)
    title = (args.get("title") or "").strip()
    if not title:
        raise ToolError("Provide a 'title'.")
    ttype = args.get("type") or "bug"
    if ttype not in dict(TICKET_TYPES):
        raise ToolError(f"Unknown type. One of: {', '.join(t for t, _ in TICKET_TYPES)}.")
    severity = args.get("severity")
    if severity not in (None, 1, 2, 3, 4):
        raise ToolError("severity must be 1-4 or omitted.")
    priority = args.get("priority") or 3
    if priority not in (1, 2, 3, 4):
        raise ToolError("priority must be 1-4.")

    from apps.tickets.models import Ticket, TicketEvent

    ticket = Ticket.objects.create(
        project=ctx.project,
        reporter=ctx.user,
        title=title,
        type=ttype,
        description=args.get("description", ""),
        steps_to_reproduce=args.get("steps_to_reproduce", ""),
        expected_result=args.get("expected_result", ""),
        actual_result=args.get("actual_result", ""),
        environment=args.get("environment", ""),
        component=args.get("component", ""),
        severity=severity,
        priority=priority,
    )
    ticket.watchers.add(ctx.user)
    TicketEvent.log(ticket, ctx.user, f"raised this {ticket.get_type_display().lower()} via Claude Code")
    notify.ticket_raised(ticket)
    return {"ok": True, "ticket": ticket.key, "url": ticket.get_absolute_url()}


# =========================================================================== sprints
@tool(
    "list_sprints",
    "List this project's sprints with goal, dates, committed points and progress.",
    _schema({"status": {"type": "string", "enum": ["planning", "active", "completed", "cancelled"]}}),
)
def _list_sprints(ctx, args):
    qs = visible_sprints(ctx.user).filter(project=ctx.project)
    if args.get("status"):
        qs = qs.filter(status=args["status"])
    return {"sprints": [fmt.sprint_brief(s) for s in qs]}


@tool(
    "get_sprint",
    "One sprint by number or key (e.g. 'PLAT-S1' or 1), including its committed requirements.",
    _schema({"number": {"type": "string"}}, ["number"]),
)
def _get_sprint(ctx, args):
    n = _parse_number(args.get("number"), "S")
    s = visible_sprints(ctx.user).filter(project=ctx.project, number=n).first()
    if s is None:
        raise ToolError(f"Sprint {args.get('number')} not found in {ctx.project.key}.")
    data = fmt.sprint_brief(s)
    data["requirements"] = [fmt.requirement_brief(r) for r in s.requirements.all()]
    return data


@tool(
    "list_requirements",
    "List requirements on this project. Filter by sprint, status, free text, or backlog-only.",
    _schema(
        {
            "sprint": {"type": "string", "description": "Sprint number or key"},
            "status": {"type": "string", "enum": [s for s, _ in REQUIREMENT_STATUS]},
            "q": {"type": "string"},
            "backlog_only": {"type": "boolean", "description": "Only requirements not in any sprint"},
            "limit": {"type": "integer", "description": "1-200, default 50"},
        }
    ),
)
def _list_requirements(ctx, args):
    qs = visible_requirements(ctx.user).filter(project=ctx.project)
    if args.get("backlog_only"):
        qs = qs.filter(sprint__isnull=True)
    if args.get("sprint"):
        qs = qs.filter(sprint__number=_parse_number(args["sprint"], "S"))
    if args.get("status"):
        qs = qs.filter(status=args["status"])
    if args.get("q"):
        term = args["q"]
        qs = qs.filter(Q(title__icontains=term) | Q(description__icontains=term) | Q(user_story__icontains=term))
    rows = list(qs[: _clamp(args.get("limit"), 50, 1, 200)])
    return {"count": len(rows), "requirements": [fmt.requirement_brief(r) for r in rows]}


@tool(
    "get_requirement",
    "Full detail for one requirement by number or key (e.g. 'PLAT-R7' or 7): description, user "
    "story, acceptance criteria, field-level specs (types, mandatory rules, validation, lengths), "
    "risks, dependencies and its unit test cases. The field specs and acceptance criteria are the "
    "contract to build against.",
    _schema({"number": {"type": "string"}}, ["number"]),
)
def _get_requirement_tool(ctx, args):
    return fmt.requirement_full(_get_requirement(ctx, args.get("number")))


@tool(
    "list_test_cases",
    "List unit test cases on this project, optionally for one requirement or by status.",
    _schema(
        {
            "requirement": {"type": "string"},
            "status": {"type": "string", "enum": [s for s, _ in TEST_STATUS]},
            "limit": {"type": "integer", "description": "1-200, default 50"},
        }
    ),
)
def _list_test_cases(ctx, args):
    qs = visible_test_cases(ctx.user).filter(project=ctx.project)
    if args.get("requirement"):
        qs = qs.filter(requirement__number=_parse_number(args["requirement"], "R"))
    if args.get("status"):
        qs = qs.filter(status=args["status"])
    rows = list(qs[: _clamp(args.get("limit"), 50, 1, 200)])
    return {"count": len(rows), "test_cases": [fmt.test_case_brief(tc) for tc in rows]}


@tool(
    "get_test_case",
    "Full detail for one unit test case by number or key (e.g. 'PLAT-TC3' or 3).",
    _schema({"number": {"type": "string"}}, ["number"]),
)
def _get_test_case_tool(ctx, args):
    return fmt.test_case_full(_get_test_case(ctx, args.get("number")))


@tool(
    "list_external_services",
    "The external services / accounts / credentials this project depends on (Stripe, SMTP, cloud "
    "accounts, client data, …) and their readiness. Never contains the secrets themselves.",
    _OBJ,
)
def _list_external_services(ctx, args):
    qs = visible_services(ctx.user).filter(project=ctx.project)
    return {"services": [fmt.service_brief(s) for s in qs]}


@tool(
    "set_requirement_status",
    "Move a requirement to a new status (draft, ready, in_progress, in_review, done, deferred). "
    "Requires sprint-write and the 'sprint.manage' capability.",
    _schema(
        {"number": {"type": "string"}, "status": {"type": "string", "enum": [s for s, _ in REQUIREMENT_STATUS]}},
        ["number", "status"],
    ),
)
def _set_requirement_status(ctx, args):
    _require_write(ctx, "sprint")
    status = args.get("status")
    if status not in dict(REQUIREMENT_STATUS):
        raise ToolError(f"Unknown status. One of: {', '.join(s for s, _ in REQUIREMENT_STATUS)}.")
    r = _get_requirement(ctx, args.get("number"))
    _require_cap(ctx, "sprint.manage", ctx.project)
    old = r.get_status_display()
    r.status = status
    r.save(update_fields=["status", "updated_at"])
    Activity.log(
        ctx.user,
        f"moved requirement {r.key} from {old} to {r.get_status_display()} via Claude Code",
        project=ctx.project,
    )
    return {"ok": True, "requirement": r.key, "status": r.status}


@tool(
    "record_test_result",
    "Record the outcome of a unit test case (pass, fail, blocked, not_run) with an optional "
    "actual-result note. Requires sprint-write and the 'testcase.manage' capability.",
    _schema(
        {
            "number": {"type": "string"},
            "status": {"type": "string", "enum": [s for s, _ in TEST_STATUS]},
            "actual_result": {"type": "string"},
            "comments": {"type": "string"},
        },
        ["number", "status"],
    ),
)
def _record_test_result(ctx, args):
    _require_write(ctx, "sprint")
    status = args.get("status")
    if status not in dict(TEST_STATUS):
        raise ToolError(f"Unknown status. One of: {', '.join(s for s, _ in TEST_STATUS)}.")
    tc = _get_test_case(ctx, args.get("number"))
    _require_cap(ctx, "testcase.manage", ctx.project)
    tc.status = status
    if args.get("actual_result") is not None:
        tc.actual_result = args["actual_result"]
    if args.get("comments") is not None:
        tc.comments = args["comments"]
    tc.developer = ctx.user
    if not tc.executed_on:
        tc.executed_on = timezone.localdate()
    tc.save(update_fields=["status", "actual_result", "comments", "developer", "executed_on", "updated_at"])
    Activity.log(
        ctx.user, f"recorded {tc.get_status_display()} for unit test {tc.key} via Claude Code", project=ctx.project
    )
    if tc.status in ("fail", "blocked"):
        notify.test_case_result(tc, ctx.user)
    return {"ok": True, "test_case": tc.key, "status": tc.status}


@tool(
    "add_acceptance_criterion",
    "Add an acceptance criterion to a requirement — either a plain 'statement' or a structured "
    "given / when / then triple. Requires sprint-write and the 'sprint.manage' capability.",
    _schema(
        {
            "requirement_number": {"type": "string"},
            "statement": {"type": "string"},
            "given": {"type": "string"},
            "when": {"type": "string"},
            "then": {"type": "string"},
        },
        ["requirement_number"],
    ),
)
def _add_acceptance_criterion(ctx, args):
    _require_write(ctx, "sprint")
    r = _get_requirement(ctx, args.get("requirement_number"))
    _require_cap(ctx, "sprint.manage", ctx.project)

    from apps.sprints.models import AcceptanceCriterion

    order = (r.criteria.aggregate(m=Max("order"))["m"] or 0) + 1
    statement = (args.get("statement") or "").strip()
    if statement:
        crit = AcceptanceCriterion(requirement=r, order=order, format="statement", statement=statement)
    else:
        g, w, t = (args.get("given") or "").strip(), (args.get("when") or "").strip(), (args.get("then") or "").strip()
        if not (g and w and t):
            raise ToolError("Provide either 'statement' or all of 'given', 'when' and 'then'.")
        crit = AcceptanceCriterion(
            requirement=r, order=order, format="gwt", given_text=g, when_text=w, then_text=t
        )
    crit.clean()
    crit.save()
    Activity.log(ctx.user, f"added an acceptance criterion to {r.key} via Claude Code", project=ctx.project)
    return {"ok": True, "requirement": r.key, "criterion": crit.text}


# =========================================================================== dev log
_DEV_KINDS = ["feature", "bugfix", "refactor", "test", "docs", "chore", "research", "other"]
_DEV_STATUS = ["in_progress", "completed", "blocked", "needs_review"]


def _resolve_dev_links(ctx, args):
    """-> dict of the FK objects named in args, each scoped to this project."""
    links = {}
    if args.get("sprint"):
        links["sprint"] = _sprint_by_ref(ctx, args["sprint"])
    if args.get("requirement"):
        links["requirement"] = _get_requirement(ctx, args["requirement"])
    if args.get("ticket"):
        links["ticket"] = _get_ticket(ctx, args["ticket"])
    if args.get("task"):
        links["task"] = _get_task(ctx, args["task"])
    return links


@tool(
    "push_dev_update",
    "Record a development-progress update on this project — what you did, its status, and the "
    "milestone it moves toward. Shows on the project's Dev Log tab and Activity feed. Call "
    "list_dev_milestones first so milestone labels stay consistent.",
    _schema(
        {
            "headline": {"type": "string", "description": "Short summary of what was done"},
            "details": {"type": "string", "description": "Longer description (markdown)"},
            "kind": {"type": "string", "enum": _DEV_KINDS, "description": "Default 'feature'"},
            "status": {"type": "string", "enum": _DEV_STATUS, "description": "Default 'in_progress'"},
            "milestone": {"type": "string", "description": "Free-text milestone this work is progressing toward"},
            "milestone_done": {"type": "boolean", "description": "True if this update completes that milestone"},
            "sprint": {"type": "string", "description": "Sprint key/number to link"},
            "requirement": {"type": "string", "description": "Requirement key/number to link"},
            "ticket": {"type": "string", "description": "Ticket key/number to link"},
            "task": {"type": "string", "description": "Task id to link"},
            "files_changed": {"type": "array", "items": {"type": "string"}, "description": "Paths touched"},
            "branch": {"type": "string"},
            "commit": {"type": "string"},
            "pr_url": {"type": "string"},
        },
        ["headline"],
    ),
)
def _push_dev_update(ctx, args):
    from apps.devlog.models import DevUpdate

    headline = (args.get("headline") or "").strip()
    if not headline:
        raise ToolError("Provide a 'headline'.")
    kind = args.get("kind") or "feature"
    status = args.get("status") or "in_progress"
    if kind not in _DEV_KINDS:
        raise ToolError(f"kind must be one of: {', '.join(_DEV_KINDS)}.")
    if status not in _DEV_STATUS:
        raise ToolError(f"status must be one of: {', '.join(_DEV_STATUS)}.")
    files = args.get("files_changed") or []
    if not isinstance(files, list) or any(not isinstance(f, str) for f in files):
        raise ToolError("files_changed must be a list of strings.")

    update = DevUpdate.objects.create(
        project=ctx.project,
        created_by=ctx.user,
        source="mcp",
        via_token=ctx.token,
        headline=headline[:240],
        details=args.get("details", ""),
        kind=kind,
        status=status,
        milestone=(args.get("milestone") or "").strip()[:200],
        milestone_done=bool(args.get("milestone_done")),
        files_changed=files,
        branch=(args.get("branch") or "")[:200],
        commit=(args.get("commit") or "")[:120],
        pr_url=args.get("pr_url", ""),
        **_resolve_dev_links(ctx, args),
    )
    Activity.log(ctx.user, f"logged a dev update via Claude Code: “{update.headline}”", project=ctx.project)
    notify.notify_many(
        notify.project_audience(ctx.project).exclude(pk=ctx.user.pk),
        title=f"{ctx.project.key}: dev update",
        body=f"{update.headline} — {update.get_status_display()}"[:300],
        url=update.get_absolute_url(),
        kind="devlog",
        project=ctx.project,
    )
    return {"ok": True, "id": update.id, "headline": update.headline, "url": update.get_absolute_url()}


@tool(
    "update_dev_update",
    "Amend a dev update you pushed earlier — typically to move it from in_progress to completed "
    "or to flag its milestone done.",
    _schema(
        {
            "id": {"type": "integer"},
            "status": {"type": "string", "enum": _DEV_STATUS},
            "milestone_done": {"type": "boolean"},
            "headline": {"type": "string"},
            "details": {"type": "string"},
            "milestone": {"type": "string"},
        },
        ["id"],
    ),
)
def _update_dev_update(ctx, args):
    update = _get_dev_update(ctx, args.get("id"))
    fields = []
    if args.get("status"):
        if args["status"] not in _DEV_STATUS:
            raise ToolError(f"status must be one of: {', '.join(_DEV_STATUS)}.")
        update.status = args["status"]
        fields.append("status")
    if "milestone_done" in args:
        update.milestone_done = bool(args["milestone_done"])
        fields.append("milestone_done")
    if args.get("headline"):
        update.headline = args["headline"].strip()[:240]
        fields.append("headline")
    if args.get("details") is not None:
        update.details = args["details"]
        fields.append("details")
    if args.get("milestone") is not None:
        update.milestone = args["milestone"].strip()[:200]
        fields.append("milestone")
    if not fields:
        raise ToolError("Nothing to change — pass at least one field.")
    update.save(update_fields=fields + ["updated_at"])
    return {"ok": True, "id": update.id, "changed": fields}


@tool(
    "list_dev_updates",
    "List development-progress updates on this project, newest first.",
    _schema(
        {
            "milestone": {"type": "string"},
            "status": {"type": "string", "enum": _DEV_STATUS},
            "kind": {"type": "string", "enum": _DEV_KINDS},
            "limit": {"type": "integer", "description": "1-100, default 30"},
        }
    ),
)
def _list_dev_updates(ctx, args):
    from apps.devlog.selectors import visible_dev_updates

    qs = visible_dev_updates(ctx.user).filter(project=ctx.project)
    if args.get("milestone"):
        qs = qs.filter(milestone=args["milestone"])
    if args.get("status"):
        qs = qs.filter(status=args["status"])
    if args.get("kind"):
        qs = qs.filter(kind=args["kind"])
    rows = list(qs[: _clamp(args.get("limit"), 30, 1, 100)])
    return {"count": len(rows), "updates": [fmt.dev_update_brief(u) for u in rows]}


@tool(
    "get_dev_update",
    "Full detail for one dev update by id.",
    _schema({"id": {"type": "integer"}}, ["id"]),
)
def _get_dev_update_tool(ctx, args):
    return fmt.dev_update_full(_get_dev_update(ctx, args.get("id")))


@tool(
    "list_dev_milestones",
    "The milestone rollup for this project's dev log: each free-text milestone Claude Code has "
    "named, whether it is done, and how far along the set is. Call before push_dev_update to "
    "reuse existing milestone labels.",
    _OBJ,
)
def _list_dev_milestones(ctx, args):
    from apps.devlog.selectors import milestone_progress, visible_dev_updates

    return milestone_progress(visible_dev_updates(ctx.user).filter(project=ctx.project))
