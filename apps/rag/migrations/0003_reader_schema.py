"""Creates a `rag` schema of security-barrier views — one per thing the chat
agent's SQL tool is allowed to see, already flattened and stripped of any
sensitive column (password hashes, API keys, secrets, tokens never appear
here). A dedicated `rag_reader` role can only SELECT within this schema, never
the app's real tables. The agent scopes every query to one project by setting
`app.rag_project_id` on its connection before running any LLM-written SQL —
the views filter on that, so the LLM's SQL never needs to (and can't override
it, since it can't see or touch the setting from inside the view).
"""

import os

from django.db import migrations

CREATE_VIEWS_SQL = """
CREATE SCHEMA IF NOT EXISTS rag;

CREATE VIEW rag.project WITH (security_barrier = true) AS
SELECT p.key, p.name, p.description, p.status,
       CASE p.priority WHEN 1 THEN 'Critical' WHEN 2 THEN 'High' WHEN 3 THEN 'Medium' WHEN 4 THEN 'Low' END AS priority,
       p.start_date, p.due_date, p.budget,
       c.name AS customer_name, (ow.first_name || ' ' || ow.last_name) AS owner_name, tm.name AS team_name
FROM projects_project p
LEFT JOIN projects_customer c ON c.id = p.customer_id
LEFT JOIN accounts_user ow ON ow.id = p.owner_id
LEFT JOIN accounts_team tm ON tm.id = p.team_id
WHERE p.id = current_setting('app.rag_project_id', true)::int;

CREATE VIEW rag.customer WITH (security_barrier = true) AS
SELECT c.name, c.industry, c.website, c.description, c.notes, c.contact_email, c.contact_phone,
       (am.first_name || ' ' || am.last_name) AS account_manager_name,
       (pc.first_name || ' ' || pc.last_name) AS primary_contact_name
FROM projects_customer c
JOIN projects_project p ON p.customer_id = c.id
LEFT JOIN accounts_user am ON am.id = c.account_manager_id
LEFT JOIN accounts_user pc ON pc.id = c.primary_contact_id
WHERE p.id = current_setting('app.rag_project_id', true)::int;

CREATE VIEW rag.task WITH (security_barrier = true) AS
SELECT t.id AS task_id, t.title, t.description, ws.name AS status,
       CASE t.priority WHEN 1 THEN 'Critical' WHEN 2 THEN 'High' WHEN 3 THEN 'Medium' WHEN 4 THEN 'Low' END AS priority,
       (au.first_name || ' ' || au.last_name) AS assignee_name,
       (ru.first_name || ' ' || ru.last_name) AS reporter_name,
       t.start_date, t.due_date, t.estimate_hours, t.spent_hours, t.created_at, t.updated_at
FROM projects_task t
LEFT JOIN projects_workflowstate ws ON ws.id = t.state_id
LEFT JOIN accounts_user au ON au.id = t.assignee_id
LEFT JOIN accounts_user ru ON ru.id = t.reporter_id
WHERE t.project_id = current_setting('app.rag_project_id', true)::int;

CREATE VIEW rag.person WITH (security_barrier = true) AS
SELECT (u.first_name || ' ' || u.last_name) AS full_name, u.username, u.email, u.phone, u.title,
       m.side, m.position, m.role_description, m.responsibility, m.remarks, m.is_primary_contact,
       m.allocation, r.name AS system_role
FROM projects_projectmembership m
JOIN accounts_user u ON u.id = m.user_id
LEFT JOIN accounts_role r ON r.id = u.role_id
WHERE m.project_id = current_setting('app.rag_project_id', true)::int;

CREATE VIEW rag.timeline_entry WITH (security_barrier = true) AS
SELECT e.id AS entry_id, e.kind, e.title, e.body, e.transcript, e.summary, e.status, e.occurred_at,
       (au.first_name || ' ' || au.last_name) AS author_name
FROM intake_timelineentry e
LEFT JOIN accounts_user au ON au.id = e.author_id
WHERE e.project_id = current_setting('app.rag_project_id', true)::int;

CREATE VIEW rag.brd WITH (security_barrier = true) AS
SELECT v.number AS version_number, v.markdown, v.summary, v.created_at
FROM intake_brdversion v
JOIN intake_brd b ON b.id = v.brd_id
WHERE b.project_id = current_setting('app.rag_project_id', true)::int
ORDER BY v.number DESC;
"""

DROP_VIEWS_SQL = """
DROP VIEW IF EXISTS rag.brd, rag.timeline_entry, rag.person, rag.task, rag.customer, rag.project;
DROP SCHEMA IF EXISTS rag;
"""


def create_reader_role(apps, schema_editor):
    password = os.environ.get("RAG_READER_PASSWORD")
    if not password:
        raise RuntimeError("RAG_READER_PASSWORD must be set in .env before this migration can run.")

    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'rag_reader'")
        if cursor.fetchone() is None:
            cursor.execute("CREATE ROLE rag_reader LOGIN")
        cursor.execute("ALTER ROLE rag_reader PASSWORD %s", [password])
        cursor.execute("ALTER ROLE rag_reader SET search_path = rag")
        cursor.execute("ALTER ROLE rag_reader SET statement_timeout = '5s'")
        cursor.execute("REVOKE ALL ON SCHEMA public FROM rag_reader")
        cursor.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM rag_reader")
        cursor.execute("GRANT USAGE ON SCHEMA rag TO rag_reader")
        cursor.execute("GRANT SELECT ON ALL TABLES IN SCHEMA rag TO rag_reader")


def drop_reader_role(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        # migration operations reverse in reverse order, so this runs *before* the RunSQL
        # above has dropped the views -- Postgres refuses to drop a role that still holds
        # privileges on live objects, so revoke first regardless of what still exists
        cursor.execute("SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'rag_reader'")
        if cursor.fetchone() is None:
            return
        cursor.execute("REVOKE ALL ON ALL TABLES IN SCHEMA rag FROM rag_reader")
        cursor.execute("REVOKE ALL ON SCHEMA rag FROM rag_reader")
        cursor.execute("DROP ROLE IF EXISTS rag_reader")


class Migration(migrations.Migration):
    dependencies = [("rag", "0002_initial")]
    operations = [
        migrations.RunSQL(sql=CREATE_VIEWS_SQL, reverse_sql=DROP_VIEWS_SQL),
        migrations.RunPython(create_reader_role, reverse_code=drop_reader_role),
    ]
