# Planwise MCP tool reference

Every tool is hard-scoped to the one project the link's token belongs to, and runs with the
permissions of the user who created the link. You cannot reach another project from here,
and you don't need to pass a project argument anywhere.

Names may appear prefixed (`mcp__planwise-plat__get_requirement`) depending on how the
server was added.

## Contents

- [Understanding the project](#understanding-the-project)
- [The design contract: requirements](#the-design-contract-requirements)
- [Bugs and change requests: tickets](#bugs-and-change-requests-tickets)
- [Reporting progress: the dev log](#reporting-progress-the-dev-log)
- [Write scopes](#write-scopes)
- [Resources and prompts](#resources-and-prompts)

## Understanding the project

**`project_overview`** — no arguments. Status, dates, customer, owner, team, members, and
counts of tickets/requirements/sprints/tasks, plus which documents exist. Start here; it
tells you what's worth reading next.

**`get_document(kind)`** — `"tech_stack"`, `"dev_instructions"`, `"build_plan"`, or
`"brd"`. Returns full markdown.

**`get_design_workspace`** — no arguments. This project's Penpot design workspace:
`status`, `file_id`, `file_name`, `penpot_project_name`, `workspace_url`, `viewer_url`,
`public_share_url`. Call it to confirm which Penpot file belongs to this project before
connecting the `penpot` MCP — `file_name` is `"<KEY> — UI"` and should match what
`penpot.currentFile` reports. `{"design_workspace": null}` means the project has no
workspace yet (create one from Planwise → Design tab).

Read `tech_stack` and `dev_instructions` **before designing or writing code**. They are the
project's own standards — framework, component library, repository layout, naming, API
conventions. A design or a component that ignores them is rework. If a project hasn't
written its own version, you get the organisation default, marked as such.

**`search_docs(query, limit)`** — semantic search over notes, meeting transcripts, uploaded
documents and the BRD. Returns snippets. Use for *"what was decided about X"*, *"did the
client say anything about Y"*. Cheap; reach for it often.

**`ask_project(question, use_docs, use_data)`** — asks the project's own RAG agent, which
reasons across documents and (optionally) read-only SQL, and returns a synthesised answer
with sources. Use when the answer requires joining several sources, not when one snippet
would do.

**`query_data(sql)`** — one read-only `SELECT` against a locked-down, project-scoped
Postgres view schema. Use for counts and rollups (*"how many requirements are still
draft"*) that would be tedious via list tools. The connection cannot see other projects or
any base table, so you don't need to filter by project.

Rough guide: `search_docs` for text you'd recognise, `ask_project` for questions needing
synthesis, `query_data` for numbers.

## The design contract: requirements

**`list_sprints(status)`** / **`get_sprint(number)`** — sprints with goal, dates, points and
progress. `get_sprint` also returns its committed requirements. Accepts `"PLAT-S2"` or `2`.

**`list_requirements(sprint, status, q, backlog_only, limit)`** — filter by sprint, status
(`draft`, `ready`, `in_progress`, `in_review`, `done`, `deferred`), free text, or
backlog-only. Returns briefs (key, title, status, priority, points, sprint, assignee).

**`get_requirement(number)`** — the one that matters for design. Accepts `"PLAT-R7"` or `7`.
Returns:

| Field | What it's for |
|---|---|
| `description`, `user_story` | intent — hierarchy and layout decisions |
| `acceptance_criteria[]` | `format` (`statement` or `gwt`), `text`, `is_met` — the states the design owes |
| `field_specs[]` | `name`, `type`, `mandatory`, `mandatory_condition`, `max_length`, `validation`, `default_value`, `notes` — the form controls |
| `risks[]` | category, likelihood, impact, score, mitigation |
| `dependencies[]` | internal (another requirement) or external, with status |
| `test_cases[]` | linked unit test cases |

**`list_test_cases(requirement, status, limit)`** / **`get_test_case(number)`** — the 13-field
test spec plus `test_type` (positive, negative, validation, boundary, integration,
error_handling). Negative and validation cases are a good checklist for which error states a
design needs.

**`list_external_services`** — third-party services and accounts the project depends on
(payment provider, SMTP, cloud accounts) and their readiness. Never contains secrets. Worth
checking before designing a screen that assumes an integration exists.

## Bugs and change requests: tickets

**`list_tickets(status, type, assignee, q, open_only, limit)`** — defaults to open items.
Types: bug, change request, feature, question, incident.

**`get_ticket(ref)`** — full detail: description, `steps_to_reproduce`, `expected_result`,
`actual_result`, `environment`, comments, history, attachments. For a UI bug the
`expected_result` is your design target and `actual_result` is what's wrong today.

## Reporting progress: the dev log

**`list_dev_milestones`** — the milestone rollup: every free-text milestone label used so
far, whether it's done, and progress across the set. **Call this before pushing an update**
so you reuse an existing label instead of creating a near-duplicate — the rollup groups on
the exact string, so "Participant screens" and "Participant Screens" split into two.

**`push_dev_update(headline, details, kind, status, milestone, milestone_done, sprint,
requirement, ticket, task, files_changed, branch, commit, pr_url)`** — records progress on
the project's Dev Log tab and Activity feed, and notifies the project audience. No write
scope needed.

- `kind`: feature, bugfix, refactor, test, docs, chore, research, other
- `status`: in_progress, completed, blocked, needs_review
- Link `requirement` / `ticket` so the update lands on the right record
- Put the Penpot file or board URL in `details` so a reviewer can jump to the design

**`update_dev_update(id, status, milestone_done, headline, details, milestone)`** — amend an
update you pushed earlier, typically moving it from in_progress to completed.

**`list_dev_updates(milestone, status, kind, limit)`** / **`get_dev_update(id)`** — read
back what's already been reported. Useful at the start of a session to see where the
previous one left off.

## Write scopes

A link is read-only unless the scope was ticked when it was created (Planwise → project →
**Claude Code** tab). These tools need one:

| Tool | Needs | Also needs capability |
|---|---|---|
| `comment_ticket` | ticket write | `ticket.comment` |
| `set_ticket_status` | ticket write | `ticket.triage` |
| `create_ticket` | ticket write | `ticket.create` |
| `set_requirement_status` | sprint write | `sprint.manage` |
| `record_test_result` | sprint write | `testcase.manage` |
| `add_acceptance_criterion` | sprint write | `sprint.manage` |

Failures come back as a clear message naming what's missing. Report it to the user and move
on — retrying won't help, and the fix is a permissions change someone else makes.

Useful ones in a design workflow:

- **`set_requirement_status(number, status)`** — move to `in_review` when the design is ready
  for someone to look at.
- **`add_acceptance_criterion(requirement_number, statement | given/when/then)`** — when the
  design surfaced a state nobody had specified, add it rather than leaving it implicit.
- **`create_ticket(title, type, description, ...)`** — file the gap or bug you found while
  designing.
- **`record_test_result(number, status, actual_result, comments)`** — after verifying a test
  case against the built UI.

## Resources and prompts

Resources (`@`-mentionable): `planwise://project/overview`,
`planwise://project/brd`, `planwise://project/design`, `planwise://project/doc/tech_stack`,
`.../dev_instructions`, `.../build_plan`.

Prompts: `fix_bug(ticket)` and `implement_requirement(requirement)` — each loads the item
plus the project's development standards and frames the work.

The endpoint is rate-limited to 120 requests/minute per token. Batch reads rather than
looping one call per requirement.
