"""The chat agent's structured-data tool: turns LLM-written SQL into rows,
safely. Two layers of defense, not one:

1. `rag_reader` is a Postgres role that can only SELECT inside the `rag`
   schema of security-barrier views (apps/rag/migrations/0003) — it has zero
   grants on the app's real tables, so even a query that reads whatever it
   wants can only ever see the curated, already-sanitized view columns.
2. Every view filters on `current_setting('app.rag_project_id')`, which this
   module sets from a real Django `Project` object before running anything —
   the LLM's SQL never states a project, and couldn't override it if it tried.

On top of that: statement-level validation rejects anything but a single
SELECT (sqlparse), and the role itself has a 5s statement_timeout.
"""

import os

import psycopg
import sqlparse

SCHEMA_HELP = """\
You can query these read-only views (schema: rag, already scoped to the current project):

rag.project(key, name, description, status, priority, start_date, due_date, budget, customer_name, owner_name, team_name)
rag.customer(name, industry, website, description, notes, contact_email, contact_phone, account_manager_name, primary_contact_name)
rag.task(task_id, title, description, status, priority, assignee_name, reporter_name, start_date, due_date, estimate_hours, spent_hours, created_at, updated_at)
rag.person(full_name, username, email, phone, title, side, position, role_description, responsibility, remarks, is_primary_contact, allocation, system_role)
rag.timeline_entry(entry_id, kind, title, body, transcript, summary, status, occurred_at, author_name)
rag.brd(version_number, markdown, summary, created_at)

Only SELECT statements are allowed. Unqualified table names (e.g. `task`) already resolve to
the `rag` schema. There is always exactly one project's worth of rows in every view.
"""

MAX_ROWS = 200


class InvalidQuery(Exception):
    pass


def _validate_select_only(sql):
    statements = [s for s in sqlparse.parse(sql) if s.token_first(skip_cm=True)]
    if len(statements) != 1:
        raise InvalidQuery("Send exactly one SQL statement.")
    stmt = statements[0]
    if stmt.get_type() != "SELECT":
        raise InvalidQuery("Only SELECT statements are allowed.")
    upper = sql.upper()
    for banned in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "GRANT", "TRUNCATE", "CALL", "--", "/*"):
        if banned in upper:
            raise InvalidQuery(f"'{banned}' is not allowed in this query.")


def _connection_kwargs():
    return dict(
        host=os.environ.get("POSTGRES_HOST", "db"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=os.environ.get("POSTGRES_DB", "pms"),
        user="rag_reader",
        password=os.environ["RAG_READER_PASSWORD"],
    )


def run_query(project_id, sql):
    """-> list[dict]. Raises InvalidQuery for anything but a single SELECT,
    or psycopg.Error for anything Postgres itself rejects (bad syntax, denied
    table, timeout — all of which are safe to show the agent as-is)."""
    _validate_select_only(sql)

    # SET doesn't accept bind parameters — project_id is always our own trusted int
    # (never LLM-controlled), and the int() cast rejects anything that isn't one.
    safe_project_id = int(project_id)

    with psycopg.connect(**_connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(f"SET LOCAL app.rag_project_id = '{safe_project_id}'")
            cur.execute(sql)
            columns = [c.name for c in cur.description] if cur.description else []
            rows = cur.fetchmany(MAX_ROWS)
    return [dict(zip(columns, row)) for row in rows]
