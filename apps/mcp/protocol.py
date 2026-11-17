"""Minimal MCP (Model Context Protocol) over JSON-RPC 2.0, Streamable-HTTP transport.

Only the parts a tools/resources/prompts server needs: `initialize`, `ping`,
`tools/list`, `tools/call`, `resources/list`, `resources/read`, `prompts/list`,
`prompts/get`. Responses are plain `application/json` — this server never opens an
SSE stream, which Claude Code's HTTP transport handles fine.
"""

from . import prompts, resources, tools

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_VERSIONS = {"2025-06-18", "2025-03-26", "2024-11-05"}
SERVER_INFO = {"name": "planwise-mcp", "version": "1.0.0"}

# JSON-RPC 2.0 error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class RpcError(Exception):
    def __init__(self, code, message, data=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def _result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code, message, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": err}


def handle_message(msg, ctx):
    """Process one JSON-RPC message. Returns a response dict, or None for a notification."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
        return _error(msg.get("id") if isinstance(msg, dict) else None, INVALID_REQUEST, "Invalid JSON-RPC request.")

    method = msg["method"]
    params = msg.get("params") or {}
    msg_id = msg.get("id")

    # notifications (no id) — acknowledge without a body
    if msg_id is None:
        return None

    try:
        return _result(msg_id, dispatch(method, params, ctx))
    except RpcError as exc:
        return _error(msg_id, exc.code, exc.message, exc.data)
    except Exception as exc:  # noqa: BLE001 - never leak a traceback over the wire
        return _error(msg_id, INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")


def dispatch(method, params, ctx):
    if method == "initialize":
        return _initialize(params, ctx)
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": tools.list_tools()}
    if method == "tools/call":
        return tools.call_tool(ctx, params.get("name"), params.get("arguments") or {})
    if method == "resources/list":
        return {"resources": resources.list_resources(ctx)}
    if method == "resources/read":
        return {"contents": resources.read_resource(ctx, params.get("uri"))}
    if method == "resources/templates/list":
        return {"resourceTemplates": []}
    if method == "prompts/list":
        return {"prompts": prompts.list_prompts()}
    if method == "prompts/get":
        return prompts.get_prompt(ctx, params.get("name"), params.get("arguments") or {})
    raise RpcError(METHOD_NOT_FOUND, f"Method not found: {method}")


def _initialize(params, ctx):
    asked = params.get("protocolVersion")
    version = asked if asked in SUPPORTED_VERSIONS else PROTOCOL_VERSION
    project = ctx.project
    write = []
    if ctx.token.allow_ticket_write:
        write.append("tickets")
    if ctx.token.allow_sprint_write:
        write.append("sprints/requirements/test-cases")
    scope = f"Write access: {', '.join(write)}." if write else "This link is read-only."
    return {
        "protocolVersion": version,
        "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
        "serverInfo": SERVER_INFO,
        "instructions": (
            f"You are connected to the Planwise project {project.key} — {project.name}. "
            "Everything here is scoped to that one project. Before writing or changing code, "
            "call get_document (tech_stack, dev_instructions, build_plan) and search_docs / "
            "ask_project to ground yourself in the project's standards, BRD and decisions. "
            "Use list_tickets / get_ticket to work a bug, and get_requirement for acceptance "
            "criteria and field-level specs. For UI work, call get_design_workspace for this "
            "project's Penpot file id and URL, then use the separate `penpot` MCP server to "
            "build the design. As you make progress, call push_dev_update (after "
            "list_dev_milestones) so the project's Dev Log tracks what you have done and which "
            "milestone it reaches. " + scope
        ),
    }
