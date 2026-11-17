# Planwise — Remediation Plan

Consolidated fix plan for issues found during the 2026-09-04 review. Topology assumed:
**Claude Code runs on a developer's remote laptop; Penpot and Planwise both run on the
server `172.16.16.214`.** The Penpot MCP is therefore a *remote service on the server*,
not a `localhost` process.

Status legend: ✅ done · 🔧 in progress · ⬜ not started

---

## Phase 0 — done during the review

| Item | State | Residual |
|---|---|---|
| Postgres connection exhaustion (site-wide HTTP 500s) | ✅ `CONN_MAX_AGE=0`, Postgres `max_connections=200` + `superuser_reserved_connections=5`, `LOGGING` config added. Verified: 0×500, ~3 backends under a 50-request burst (was 100/100). | Watch `pg_stat_activity` for a few days. Consider psycopg pool later. |
| `pyannote.audio` ImportError (`torchaudio.AudioMetaData` gone) | ✅ `pyannote.audio==4.0.7` baked into `services/asr/Dockerfile`; `load_diarizer` uses `token=` not `use_auth_token=`. Image rebuilt, `pms_asr` healthy, `import pyannote.audio` → 4.0.7 OK, diarizer load now fails cleanly on the HF gate instead of ImportError. | Real diarization still needs `HF_TOKEN` — Phase 6.4. |
| `import httpx2` in `apps/rag/services/agent.py` | ✅ Not a bug — `httpx2` is Pydantic's httpx fork and a declared transitive dep of `pydantic-ai-slim`/`openai`; pinned `httpx2==2.12.0` explicitly in `requirements.txt`. | — |

**0.1 ⬜** Snapshot/commit the changed files: `config/settings.py`, `docker-compose.prod.yml`,
`services/asr/Dockerfile`, `services/asr/app.py`, `requirements.txt`, `development_plan.md`,
this file.

---

## Phase 1 — Fix the Penpot-instance split and re-provision all workspaces  (do first)

**Root cause (found while executing 1.1):** `pms_web` and `pms_worker` disagree on which
Penpot they talk to.
- `.env` and `pms_web`: `PENPOT_BASE_URL=http://172.16.16.214:9001` — the local Penpot
  (`penpot-penpot-*` on this host; node prefix `0255a780-3726-…`). **This is the intended one.**
- `pms_worker` (container env, never recreated after `.env` changed): `http://172.16.15.76:9001`
  — a **different Penpot on another box** (node prefix `cf13002f-ab37-…`).

So the worker provisions every bot + team + project + file on `172.16.15.76`, and the Design
tab / share / invite / sync (run by `pms_web`) then talk to `172.16.16.214`, where none of
those objects exist → auth failures everywhere. All 4 workspaces are affected:

| ws | project | status | provisioned on |
|----|---------|--------|----------------|
| 7 | DDMS (24) | failed (after 1.1 attempt) | 172.16.15.76 (stale) |
| 8 | ePTW (25) | ready (broken) | 172.16.15.76 (stale) |
| 9 | Koel (26) | ready (broken) | 172.16.15.76 (stale) |
| 10 | Rajpal-event (27) | ready (broken) | 172.16.15.76 (stale) |

All 4 bot emails (`penpot+{ddms,e,koel,r}@planwise.local`) are **free on the intended
Penpot** (`172.16.16.214`), so a clean re-provision works.

**1.1 ✅** Cleared workspace #7's remote pointers + `bot_password`, re-queued. (This surfaced
the root cause: the worker retried against `172.16.15.76`.)

**1.2 ✅** `docker compose -f docker-compose.prod.yml up -d --force-recreate worker` →
`pms_worker` now has `PENPOT_BASE_URL=http://172.16.16.214:9001`. (`web` already had the
correct value; not recreated.)

**1.3 ✅** Reset all 4 workspaces (cleared remote fields + `bot_password`, `status=queued`).

**1.4 ✅** Worker re-provisioned all 4 against `172.16.16.214:9001` — logs show
`[penpot {DDMS,E,koel,R}] ready → http://172.16.16.214:9001/#/workspace/0255a780-…`.

**1.5 ✅** Verified on the intended Penpot (`penpot-penpot-postgres-1`):
- 4 bots: `penpot+{ddms,e,koel,r}@planwise.local`
- 4 files: `DDMS — UI`, `E — UI`, `koel — UI`, `R — UI` (ids match the workspace rows)
- 4 teams: `DDMS · …`, `E · ePTW`, `koel · Koel Power Gen`, `R · Rajpal-event`
- 4 access tokens: `planwise-{ddms,e,koel,r}`
- All 4 `PenpotWorkspace` rows `status=ready`, file_id prefix `0255a780-3726`.
- `services.sync(DDMS)` (the web-side path that was `PenpotAuthError`-ing) now returns the
  file cleanly.

**1.6 ⬜** Manually open each project's Design tab in a browser, click **Open in Penpot** —
confirm the file loads. *(needs a logged-in session; do from the UI.)*

**1.7 ⬜** Decommission the stale Penpot `172.16.15.76`: confirm nothing else points at it;
optionally delete the 4 orphaned bots + teams there. Durable guard: after any `.env` change,
always `up -d` (not `restart`) **all** affected containers together — or add a startup check
that `pms_web` and `pms_worker` resolve the same `PENPOT_BASE_URL`. (See `development_plan.md`
§12: "`.env` changes need `--force-recreate` on the affected containers.")

---

## Phase 2 — Expose the Penpot MCP as a remote service  ✅ (already exposed by Penpot)

**Finding:** the `penpotapp/frontend:2.16` image *already* proxies the MCP container. Its
nginx ships `/etc/nginx/overrides/server.d/mcp-locations.conf` (part of the `enable-mcp`
support):
```
/mcp/ws     -> http://penpot-mcp:4402      (browser-plugin bridge, WebSocket)
/mcp/stream -> http://penpot-mcp:4401/mcp  (Streamable HTTP MCP endpoint)  <-- Claude Code uses this
/mcp/sse    -> http://penpot-mcp:4401/sse  (legacy SSE)
```
The frontend is published `9001:8080`, so the MCP is reachable at
`http://172.16.16.214:9001/mcp/stream` with **no compose change**. Publishing 4401/4402
directly is unnecessary.

**2.1 — n/a** (frontend nginx already proxies; no `docker-compose.yaml` edit).

**2.2 ⬜ (optional, deferred)** TLS. Everything currently works over plain HTTP on `:9001`:
Claude Code → `http://…/mcp/stream`, and the browser plugin → `ws://172.16.16.214:9001/mcp/ws`
same-origin from the HTTP Penpot UI (no mixed content). If Penpot is later fronted with TLS,
`PENPOT_MCP_URL` just changes to the `https://…/mcp/stream` form.

**2.3 ✅** Verified externally:
- `POST http://172.16.16.214:9001/mcp/stream` `initialize` → `serverInfo {name: "penpot"}`,
  returns an `mcp-session-id`.
- `tools/list` with the session → `execute_code`, `high_level_overview`, `penpot_api_info`,
  `export_shape`.
- (`GET /mcp/stream` without a session → 400; that optional server→client SSE channel isn't
  needed — POST request/response works fully.)

**2.4 ⬜** Manual: open a design file at `http://172.16.16.214:9001/#/workspace/…` →
**Plugins → Connect to MCP server** and confirm the plugin connects (it uses
`/mcp/ws` same-origin automatically).

**2.5 ✅** Done in Phase 3 — `PENPOT_MCP_URL=http://172.16.16.214:9001/mcp/stream` set in
`.env`, `pms_web` recreated, Design tab renders the real `.mcp.json`. No `localhost:4401`
anywhere except the skill (3.5) and `DEPLOY.md`.

---

## Phase 3 — Planwise Design tab + settings for the remote model

**3.1 ✅** `config/settings.py`: removed `PENPOT_MCP_VERSION`; added
`PENPOT_MCP_URL = os.environ.get("PENPOT_MCP_URL", "").rstrip("/")`.

**3.2 ✅** `.env`: replaced `PENPOT_MCP_VERSION=2.16` with `PENPOT_MCP_URL=` (blank until
Phase 2 publishes the MCP; comment says how to set it). `.env.example`: same, plus fixed the
stale `PENPOT_BASE_URL=http://172.16.15.76:9001` → `http://penpot.example.com:9001` and added
a "recreate BOTH web+worker after changing this" warning (this was the Phase 1 root cause).
Container picked it up via `restart` (setting defaults to `""` when the var is absent).

**3.3 ✅** `apps/penpot/views.py: manage()` — passes `"penpot_mcp_url": settings.PENPOT_MCP_URL`
(dropped `mcp_version`).

**3.4 ✅** `templates/penpot/manage.html` — "Connect Claude Code" block rewritten:
- Removed `npx -y @penpot/mcp@…`, `claude mcp add … localhost:4401`, and the
  `localhost:4400/manifest.json` plugin-load step.
- New flow: add both servers to `.mcp.json` → open the design file → Plugins → Connect to
  MCP server → keep both connected.
- `.mcp.json` example's `penpot` URL is now `{{ penpot_mcp_url }}`.
- When `PENPOT_MCP_URL` is blank: shows "ask an administrator to expose it" instead of a
  broken command.
- Verified: `manage(24)` → 200, shows the admin message, no `localhost:4401` / `npx` in output.

**3.5 ✅** `.claude/skills/planwise-penpot-ui/SKILL.md` Preflight rewritten: Penpot MCP is a
shared host server (`http://<penpot-host>:9001/mcp/stream`), `.mcp.json` copied from the
Design tab, setup is **Plugins → Connect to MCP server** (no manifest URL, no `npx`).
`references/*` were already clean. `DEPLOY.md` updated: `@penpot/mcp` line → `.mcp.json` +
`PENPOT_MCP_URL`; and the `.env`-change note strengthened to `up -d --force-recreate web
worker` with the reason (the Phase 1 root cause). Grep confirms no `npx`/`localhost:44xx`
refs remain outside REMEDIATION.md / development_plan.md.

**When Phase 2 lands:** set `PENPOT_MCP_URL=http://172.16.16.214:4401/mcp` (or the proxied
`https://…/penpot-mcp/mcp`) in `.env` and `docker compose -f docker-compose.prod.yml up -d web`.

---

## Phase 4 — Penpot provisioning robustness  ✅

All in `apps/penpot/`. Tests: `python manage.py test apps.penpot` → 13/13; adjacent suites
(`apps.projects apps.mcp apps.devlog apps.sprints apps.tickets`) → 53/53.

**4.1 ✅** Self-healing `provision()` (`services.py`): new `_ensure_bot()` (register, or
recover via stored password, or — if the deterministic address is stuck — register at a
`penpot+<key>-<rand>@` address); new `_reset_remote_fields()` clears every remote id +
`bot_password` + session cache + invite rows. `provision()` now probes an existing bot with
`client_for()` + `get_profile()`; on any `PenpotError` it resets and rebuilds instead of
failing forever.

**4.2 ✅** `create_project` / `create_file` now also raise on a 200-with-no-id (→ `failed`).
`create_access_token` failure is recorded on `workspace.phase` (was stdout-only). Comment
added marking steps 2–3 best-effort, 4–5 load-bearing.

**4.3 ✅** `enqueue_for_project` re-queues `status in ("failed", "disabled")`.

**4.4 ✅** `services.rebuild()` + `penpot:rebuild` view + **Rebuild workspace** button
(new "Troubleshooting" card in `manage.html`) + `penpot_provision --project X --repair`.

**4.5 ✅** `views.manage()` renders `files` from stored fields — no Penpot call on GET.
`services._refresh_ready()` (worker, every drain pass, ≥10 min since `synced_at`) keeps the
stored file list current; the explicit "Refresh" button still forces it.

**4.6 ✅** `apps/penpot/signals.py` — `post_save` on `projects.Project` queues a workspace on
`created`, wired in `PenpotConfig.ready()`. Inline block removed from
`apps/projects/views.py`. Verified: `Project.objects.create(...)` → workspace auto-queued.

**4.7 ✅ (no code change)** Probed the live Penpot 2.16: `create-team-invitations` returns
`{"total": N, "invitations": ["<jwt>", …]}` — a list of token strings, exactly what
`_send_invites` + `PenpotInvite.accept_url` already assume. `delete-team-invitation` works
for cleanup.

**4.8 ✅** Removed the `<iframe>` share preview + its toggle from `manage.html` and the dead
handler from `static/js/penpot.js` (was blocked by mixed-content + `X-Frame-Options:
SAMEORIGIN` — permanently blank). "Open ↗" link kept.

**4.9 ✅** `apps/penpot/tests.py` rewritten: `FakePenpotClient` gained `get_profile`,
`get_teams`, `logout`, `delete_*` and failure flags. New tests —
`test_signal_enqueues_on_project_create`, `test_enqueue_requeues_failed_and_disabled`,
`test_reprovision_when_stored_bot_is_gone`, `test_rebuild_takes_a_fresh_email_when_address_is_stuck`,
`test_rebuild_clears_remote_fields_first`, `test_manage_makes_no_penpot_call`,
`test_rebuild_view`.

---

## Phase 5 — Planwise MCP ↔ Penpot bridge  ✅

**5.1 ✅** `apps/mcp/tools.py`: read-only `get_design_workspace` tool (no new token scope) →
`{design_workspace: {status, phase, ready, file_id, file_name, penpot_project_id,
penpot_project_name, team_name, workspace_url, viewer_url, public_share_url, provisioned_at,
synced_at, last_error}}` or `{design_workspace: null}`. Formatter `fmt.design_workspace()`
in `apps/mcp/format.py`.

**5.2 ✅** `apps/mcp/resources.py`: `planwise://project/design` resource (listed + readable),
delegates to the tool.

**5.3 ✅** `apps/mcp/protocol.py` `initialize` instructions: "For UI work, call
get_design_workspace for this project's Penpot file id and URL, then use the separate
`penpot` MCP server to build the design."

**5.4 ✅** `.claude/skills/planwise-penpot-ui/references/planwise-tools.md`: documented the
tool and added `planwise://project/design` to the resource list.

**Verified:** live `tools/call get_design_workspace` for DDMS returns the file id +
`workspace_url` (pointing at the correct local Penpot); resource lists + reads; tool in
`tools/list`. `apps/mcp/tests.py` — `DesignWorkspaceTests` (none / this-project-only) +
`get_design_workspace` added to the tool-list assertion. 24 mcp tests, 49 with penpot+devlog.

---

## Phase 6 — Deployment / repo hygiene  ✅ (6.4 needs a human)

**6.1 ✅** Base `docker-compose.yml` `db` → `pgvector/pgvector:pg16` (was `postgres:16-alpine`,
which fails the `rag` vector-extension migration). README rewritten: dual dev/prod run
commands + a pointer to `development_plan.md` / `REMEDIATION.md`; Containers table adds
`proxy` and corrects `denoise`/`asr` (remote Qwen ASR, local `pyannote.audio` 4.x
diarization); "Two models" and "Real weights vs fallback" tables corrected (pyannote, not
NeMo/parakeet); Layout section lists all 12 apps + `services/proxy`.

**6.2 ✅** `entrypoint.sh`: no more boot-time `makemigrations`. Now: if migrations are
unapplied it runs `makemigrations --check --dry-run` and **exits 1 with a clear message** if
a model change shipped without its migration, then `migrate`. Verified: with everything
applied it skips straight to `migrate` (no-op).

**6.3 ✅** `git init` + `.gitignore` (excludes `.env*`, `/data/`, `/media/`, `secrets/`,
`*.zip`, `.claude/settings.local.json`, caches) + `.gitattributes` (LF, binary certs/docs) +
initial commit `1a9136b` (360 files, branch `master`). No secrets/data/snapshots staged.
Retire `apps.zip` / `pms-deploy-*.zip` — git is the history now.

**6.4 ⬜ (human action)** `HF_TOKEN` is already wired (`.env` line 75, `docker-compose*.yml`,
and documented in both env files). Needs: create an HF read token, accept the model
conditions on **both** `pyannote/speaker-diarization-3.1` and `pyannote/segmentation-3.0`
for that account, put it in `.env`, `up -d asr`, confirm `/health` diarization leaves
`mock`. Rotate afterward (an earlier token was pasted in plaintext — dev_plan §13 item 6).

**6.5 ✅** `smoke_test.py` gained a `[logging]` check that asserts `django.request` routes
500s to a stream handler (guards a silent-500 regression). Also fixed a pre-existing
`smoke_test.py` breakage: the prod `.env` restricts `ALLOWED_HOSTS`, which the Django test
client's `testserver` host tripped — the script now widens it in-process. `smoke_test.py` →
**ALL CHECKS PASSED**; `manage.py test apps` → 68/68.

---

## Suggested order

**1** → **3.1–3.4** → **2** → **2.4 / 3.5** (verify end-to-end from the laptop) →
**4.1–4.5** → **6.1–6.3** → **4.6–4.9, 5** → **6.4**.
