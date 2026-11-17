"""MCP resources — read-only, @-mentionable context surfaces for the project.

Thin wrappers over the same data the tools expose, addressed by URI.
"""

import json

from . import format as fmt
from . import tools

_DOC_KINDS = ["tech_stack", "dev_instructions", "build_plan"]


def _uri(suffix):
    return f"planwise://project/{suffix}"


def list_resources(ctx):
    key = ctx.project.key
    out = [
        {
            "uri": _uri("overview"),
            "name": f"{key} overview",
            "description": "Project status, dates, customer, team, members and document inventory (JSON).",
            "mimeType": "application/json",
        },
        {
            "uri": _uri("brd"),
            "name": f"{key} BRD",
            "description": "The current Business Requirement Document (markdown).",
            "mimeType": "text/markdown",
        },
        {
            "uri": _uri("design"),
            "name": f"{key} design workspace",
            "description": "The project's Penpot design workspace — status, file id, URLs (JSON).",
            "mimeType": "application/json",
        },
    ]
    for kind in _DOC_KINDS:
        out.append(
            {
                "uri": _uri(f"doc/{kind}"),
                "name": f"{key} {kind.replace('_', ' ')}",
                "description": f"The project's {kind.replace('_', ' ')} document (markdown).",
                "mimeType": "text/markdown",
            }
        )
    return out


def read_resource(ctx, uri):
    if not uri or not uri.startswith("planwise://project/"):
        raise tools.ToolError(f"Unknown resource: {uri}")
    suffix = uri[len("planwise://project/") :]

    if suffix == "overview":
        text = json.dumps(fmt.project_overview(ctx.project), indent=2, default=str)
        return [{"uri": uri, "mimeType": "application/json", "text": text}]

    if suffix == "design":
        text = tools.call_tool(ctx, "get_design_workspace", {})["content"][0]["text"]
        return [{"uri": uri, "mimeType": "application/json", "text": text}]

    if suffix == "brd":
        text = tools.call_tool(ctx, "get_document", {"kind": "brd"})["content"][0]["text"]
        return [{"uri": uri, "mimeType": "text/markdown", "text": text}]

    if suffix.startswith("doc/"):
        kind = suffix[4:]
        if kind not in _DOC_KINDS:
            raise tools.ToolError(f"Unknown document: {kind}")
        text = tools.call_tool(ctx, "get_document", {"kind": kind})["content"][0]["text"]
        return [{"uri": uri, "mimeType": "text/markdown", "text": text}]

    raise tools.ToolError(f"Unknown resource: {uri}")
