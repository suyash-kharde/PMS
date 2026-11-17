"""MCP prompts — ready-made workflows a developer can pick in Claude Code."""

import json

from . import format as fmt
from . import tools

_PROMPTS = [
    {
        "name": "fix_bug",
        "description": "Load a bug ticket plus the project's coding standards and start a fix.",
        "arguments": [{"name": "ticket", "description": "Ticket key or number", "required": True}],
    },
    {
        "name": "implement_requirement",
        "description": "Load a requirement with its acceptance criteria and field specs, and start building it.",
        "arguments": [{"name": "requirement", "description": "Requirement key or number", "required": True}],
    },
]


def list_prompts():
    return _PROMPTS


def _message(text):
    return {"role": "user", "content": {"type": "text", "text": text}}


def get_prompt(ctx, name, arguments):
    if name == "fix_bug":
        ticket = tools._get_ticket(ctx, arguments.get("ticket"))
        standards = tools.call_tool(ctx, "get_document", {"kind": "dev_instructions"})["content"][0]["text"]
        body = (
            f"Fix this bug in project {ctx.project.key} ({ctx.project.name}).\n\n"
            f"## Ticket {ticket.key}\n```json\n{json.dumps(fmt.ticket_full(ticket), indent=2, default=str)}\n```\n\n"
            f"## Project development standards\n{standards}\n\n"
            "Steps: reproduce the problem, find the root cause in the codebase, make the smallest "
            "correct change, add or update a test, and summarise the fix. Use search_docs / ask_project "
            "if you need more project context. If you have ticket-write access, post your analysis with "
            "comment_ticket when done."
        )
        return {"description": f"Fix bug {ticket.key}", "messages": [_message(body)]}

    if name == "implement_requirement":
        req = tools._get_requirement(ctx, arguments.get("requirement"))
        standards = tools.call_tool(ctx, "get_document", {"kind": "dev_instructions"})["content"][0]["text"]
        body = (
            f"Implement this requirement in project {ctx.project.key} ({ctx.project.name}).\n\n"
            f"## Requirement {req.key}\n```json\n{json.dumps(fmt.requirement_full(req), indent=2, default=str)}\n```\n\n"
            f"## Project development standards\n{standards}\n\n"
            "Build strictly to the acceptance criteria and field specs above. Add unit tests that cover "
            "each acceptance criterion, and summarise what you changed."
        )
        return {"description": f"Implement {req.key}", "messages": [_message(body)]}

    raise tools.ToolError(f"Unknown prompt: {name}")
