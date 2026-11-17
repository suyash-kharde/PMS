"""The per-project chat agent: one LLM with up to two tools — semantic search over
the project's documents (RAG) and a read-only, project-scoped SQL query tool for
structured facts (task status, people, dates, budget). Either tool can be switched
off per request (the chat UI's two checkboxes); a disabled tool is never registered
on the agent, so the model has no way to call it at all.
"""

import asyncio
from dataclasses import dataclass, field

import httpx2
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from apps.aimodels import runtime

from . import sql_tool
from .retrieve import semantic_search
from .sql_tool import SCHEMA_HELP, InvalidQuery

BASE_PROMPT = """\
You are the project assistant for a project management system. You answer questions about
ONE specific project — its documents, its tasks, its people, its customer, its BRD.
"""

RAG_PROMPT = """\
- search_documents: semantic search over notes, meeting summaries, uploaded document text,
  and the BRD. Use it for "what was discussed", "what does the BRD say about X", open-ended
  or narrative questions. Meeting transcripts attribute lines to whoever that speaker was named
  as at index time — a real name if someone named them, otherwise a raw diarizer label like
  "SPEAKER_01". Never guess who an unnamed label actually is; report it by that label as-is.
"""

SQL_PROMPT = f"""\
- query_database: read-only SQL against structured project data. Use it for facts, counts,
  filters, and anything about status/dates/people/budget — questions a database answers better
  than a text search.

{SCHEMA_HELP}
"""

NO_TOOLS_PROMPT = """\
You have no tools available right now — answer only from the conversation itself, and say
plainly when you don't have enough information rather than guessing.
"""


@dataclass
class ChatDeps:
    project: object  # apps.projects.models.Project
    trace: dict = field(default_factory=dict)  # {"sources": [...], "sql": [...]}


def _build_model():
    cfg = runtime.config_for("llm")
    if not cfg.get("base_url"):
        raise RuntimeError("No LLM provider configured (AI models -> LLM).")
    client = httpx2.AsyncClient(timeout=cfg.get("timeout", 300), **cfg.get("httpx", {}))
    return OpenAIChatModel(
        cfg["model"],
        provider=OpenAIProvider(base_url=cfg["base_url"], api_key=cfg.get("api_key") or "not-required", http_client=client),
    )


def build_agent(use_rag=True, use_sql=True):
    prompt = BASE_PROMPT
    if use_rag or use_sql:
        prompt += "\nYou have the following tool(s):\n\n"
        if use_rag:
            prompt += RAG_PROMPT
        if use_sql:
            prompt += SQL_PROMPT
        prompt += (
            "\nUse whichever tool(s) the question actually needs. If nothing turns up an answer, "
            "say so plainly rather than guessing. Keep answers concise and direct."
        )
    else:
        prompt += "\n" + NO_TOOLS_PROMPT

    agent = Agent(_build_model(), deps_type=ChatDeps, system_prompt=prompt)

    if use_rag:
        @agent.tool
        async def search_documents(ctx: RunContext[ChatDeps], query: str) -> str:
            """Semantic search over this project's notes, transcripts, uploaded documents and BRD."""
            hits = await asyncio.to_thread(semantic_search, ctx.deps.project, query)
            ctx.deps.trace.setdefault("sources", []).extend(hits)
            if not hits:
                return "No matching documents found."
            return "\n\n".join(f"[{h['source_type']} · {h['label']}]\n{h['text']}" for h in hits)

    if use_sql:
        @agent.tool
        async def query_database(ctx: RunContext[ChatDeps], sql: str) -> str:
            """Run a single read-only SELECT against the project's structured data (see schema above)."""
            ctx.deps.trace.setdefault("sql", []).append(sql)
            try:
                rows = await asyncio.to_thread(sql_tool.run_query, ctx.deps.project.id, sql)
            except InvalidQuery as exc:
                return f"Query rejected: {exc}"
            except Exception as exc:  # noqa: BLE001 - surface DB errors to the model so it can retry
                return f"Query failed: {type(exc).__name__}: {exc}"
            if not rows:
                return "No rows."
            return str(rows[:50])

    return agent


def _build_prompt(question, history):
    recent = list(history)[-6:]
    if not recent:
        return question
    transcript = "\n".join(f"{role}: {content}" for role, content in recent)
    return f"Recent conversation:\n{transcript}\n\nNew question: {question}"


def ask(project, question, history=(), use_rag=True, use_sql=True):
    """-> (answer_text, trace) where trace = {"sources": [...], "sql": [...]}"""
    agent = build_agent(use_rag=use_rag, use_sql=use_sql)
    deps = ChatDeps(project=project)
    result = agent.run_sync(_build_prompt(question, history), deps=deps)
    return result.output, deps.trace


async def ask_stream_events(project, question, history=(), use_rag=True, use_sql=True):
    """Async generator: yields {"type": "delta", "text": ...} chunks as they arrive,
    then one final {"type": "done", "answer": ..., "trace": ...} (or {"type": "error", ...})."""
    deps = ChatDeps(project=project)
    try:
        agent = await asyncio.to_thread(build_agent, use_rag=use_rag, use_sql=use_sql)
        async with agent.run_stream(_build_prompt(question, history), deps=deps) as result:
            async for chunk in result.stream_text(delta=True):
                if chunk:
                    yield {"type": "delta", "text": chunk}
            answer = await result.get_output()
        yield {"type": "done", "answer": answer, "trace": deps.trace}
    except Exception as exc:  # noqa: BLE001 - surface to the chat UI instead of a bare 500
        yield {"type": "error", "error": f"{type(exc).__name__}: {exc}"}
