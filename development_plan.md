# Planwise — Development Plan and Build Log

A record of what has been built, why it was built that way, what is verified, and what is
still open. This file is the project's memory: the repository is not under version
control, so there is no commit history to read.

**Stack:** Django 5.1 · PostgreSQL 16 · nginx (TLS) · vanilla JS + CSS (no frontend
framework) · Docker Compose. Remote LLM and ASR endpoints, one local model (diarization).

---

## 1. Goals, in the order they were asked for

| # | Requirement | Status |
|---|---|---|
| 1 | Project plan management system, Docker Compose, Django + Postgres, source and cache on volumes | done |
| 2 | Django + JS + CSS only, responsive on any screen | done |
| 3 | Multi user type, "most dynamic fashion possible", with hierarchy | done |
| 4 | Timeline: upload text and audio; client-side and delivery-side people; role, responsibility, remarks per person | done |
| 5 | One markdown BRD per project, rebuilt on every timeline input, version controlled | done |
| 6 | OpenAI-compatible services for noise reduction and for ASR + diarization, hosted in the compose stack | done (denoise on fallback engine) |
| 7 | Name speakers after processing; speaker names appear in the BRD and map to users | done |
| 8 | Customer entity; dashboard shows customers and their projects | done |
| 9 | Web push notifications, HTTPS, working with audio upload and background processing | done (delivery needs a browser opt-in) |
| 10 | Speaker picker from the People list; edit transcript; Sync-to-BRD button | done |
| 11 | ASR from a remote multilingual endpoint; diarization local | done |
| 12 | A page to configure AI/ML endpoints, with certificate upload | done |
| 13 | Diarization via `pyannote/speaker-diarization-3.1`, ASR and other models removed from local | code done, **blocked on an HF gate** |
| 14 | Listen to a short clip per speaker while choosing the name | done |

---

## 2. Runtime shape

```
browser ──https:8443──▶ nginx (proxy) ──▶ web (Django) ──▶ db (Postgres 16)
        ──http:8000────────────────────▶               ──▶ denoise :8100
                                        worker         ──▶ asr     :8200 ──▶ remote Qwen ASR
                                                       ──▶ remote Qwen LLM
```

| Service | Role |
|---|---|
| `web` | Django dev server. Source bind-mounted, so host edits are live. |
| `worker` | `manage.py process_queue --loop`; drains queued timeline entries. |
| `db` | Postgres 16. Application tables **and** the Django cache table `pms_cache`. |
| `proxy` | nginx. Self-signed cert generated on first boot; TLS on `:8443`, redirect on `:8080`. |
| `denoise` | FastAPI, OpenAI-compatible speech enhancement. |
| `asr` | FastAPI. Local diarization plus fan-out to the remote ASR endpoint. |

Volumes: `pgdata`, `staticfiles`, `hfcache` (model weights), `certs` (TLS), `vapid` (push keys).

### Why the cache is in Postgres

The brief asked for "postgres cache as well". Implemented as
`django.core.cache.backends.db.DatabaseCache` in table `pms_cache`, created by the
entrypoint, with sessions on `cached_db`. An earlier attempt to give Postgres a second
volume for `stats_temp_directory` failed — PG16 removed that parameter — so the cache
lives in the same persistent volume as the data.

---

## 3. Django apps

| App | Contents |
|---|---|
| `accounts` | `User`, `Role`, `Capability`, `RoleCapability`, `UserCapability`, `Team` + the permission engine |
| `projects` | `Customer`, `Project`, `ProjectMembership`, `Task`, `Milestone`, `Workflow`, `WorkflowState`, `Label`, `Comment`, `Activity` + queryset scoping |
| `intake` | `TimelineEntry`, `MediaAsset`, `Speaker`, `TranscriptSegment`, `BRD`, `BRDVersion` + the audio and BRD services |
| `notify` | `PushSubscription`, `Notification`, VAPID handling, service worker |
| `aimodels` | `ModelProvider` — every AI endpoint, key and certificate, editable in the UI |
| `core` | Dashboard, search, range-capable media serving, context processor |
| `tickets` | `Ticket`, `TicketComment`, `TicketAttachment`, `TicketEvent` — bug / change-request triage |
| `sprints` | `Sprint`, `Requirement`, `AcceptanceCriterion`, `FieldSpec`, `UnitTestCase`, risks, dependencies, `ExternalService` |
| `rag` | `Chunk` (pgvector), `ChatSession`, the Ask AI agent + the `rag_reader` SQL sandbox |
| `mcp` | `ProjectMCPToken` — one Claude Code / MCP link per project (see §14) |
| `devlog` | `DevUpdate` — Claude Code development-progress log + milestone rollup (see §15) |

---

## 4. Permissions: dynamic user types

No role is hard-coded in a Python conditional. Four data-driven layers:

1. **Capability** — an atomic permission such as `brd.generate`. New ones can be added at
   runtime from *Administration → Capabilities*; 25 are seeded.
2. **Role** — a user type. Roles form a **tree**; a child inherits every ancestor grant and
   the closest role wins on conflict. Seeded: Guest → Member → Team Lead → Project Manager
   → Director → Administrator.
3. **Scope** — every grant carries `own` / `team` / `all`, so one capability means
   different reach per role.
4. **Per-user override** — grant or `deny` for one person, on top of their role.

```python
has_cap(user, "brd.generate", project)   # bool, scope-aware
require(user, "timeline.add", project)   # raises PermissionDenied
```

Templates use `{% load perms %}` then `{% if user|can:"brd.edit" %}`. Querysets are scoped
in one place, `apps/projects/selectors.py` (`visible_projects`, `visible_tasks`,
`visible_users`, `visible_customers`), so a view cannot accidentally leak rows. Resolved
capabilities are cached in Postgres for 120 s and invalidated on write.

**Four hierarchies:** the role tree, the team tree (`Team.parent`), the reporting line
(`User.manager`), and the work breakdown (programme → project → task → subtask). All three
org trees are drawn at `/org-chart/`.

---

## 5. The intake pipeline

```
upload ─▶ denoise ─▶ diarize (who spoke when) ─▶ slice per turn
       ─▶ ASR per slice (remote, multilingual) ─▶ segments + speakers
       ─▶ name speakers ─▶ BRD rewritten as a new version
```

Queued entries are picked up by the `worker` container within seconds; "Add and process
now" runs it inline.

### Why diarize first, then transcribe per turn

The remote Qwen ASR endpoint returns **text only** — it rejects `verbose_json`
(*"Currently do not support verbose_json for Qwen/Qwen3-ASR-0.6B"*) and has a short
context. There are therefore no word timestamps to align speakers against. Diarizing
first, cutting the audio on speaker turns, and transcribing each turn separately gives
per-speaker text with real timings and keeps every request inside the model's window.
Turns are capped at `MAX_CHUNK_SECONDS` (25 s) and run 4-way parallel over one keep-alive
connection: a 16.8-minute recording became 41 turns transcribed in **12 seconds**.

Turns the model returns nothing for are dropped, so silence never becomes invented text;
the entry log says so explicitly.

### Speakers

Diarizer labels (`SPEAKER_00` …) stay anonymous until a human names them. The picker is
built from the project's People list and shows position and side
(`Anita Kulkarni — Plant Operations Manager · Client side`); picking someone fills the side
and relabels the transcript live. A voice counts as named once it has either a typed name
or a mapped member.

**Listening while naming:** each speaker row has two buttons — a **12 s taster** (three
4-second excerpts from the middle of their best turns) and a **longer excerpt** (their
three longest turns). Each transcript line has its own ▶. Clips are cut server-side by
`/entries/<id>/clip/?ranges=a-b,c-d` using Python's `wave` module (the denoised rendition
is already 16 kHz mono wav, so no ffmpeg is needed), capped at 300 s. Playback uses Web
Audio rather than an `<audio>` element, for exact start/stop.

### Transcript editing

`/entries/<id>/transcript/` — correct text, reassign a line to another speaker, adjust
timings, drop lines, append a missing line. Saving renumbers, rebuilds the plain
transcript and marks the entry for re-sync.

---

## 6. The BRD

One markdown document per project, rebuilt — never patched — on every input. Each
`BRDVersion` is append-only and stores the full markdown, a change summary, a unified diff
against its predecessor, the source timeline entry, the author, the model name and token
usage. `/projects/<id>/brd/history/` renders a coloured diff between any two versions; any
version can be viewed (`?v=3`) or downloaded as `.md`. Hand edits create a version too.

The prompt carries the project brief, the participant briefs (role, responsibility,
remarks, side) and the new input with speakers resolved to real names, and demands a fixed
section order with stable `FR-xxx` / `NFR-xxx` identifiers.

### Context budgeting

`max_tokens` was a fixed 8000 against a 16000-token window, and Devanagari tokenizes far
denser than Latin, so once the document grew the request failed:

```
maximum context length is 16000 tokens ... you requested 8000 output tokens and your
prompt contains at least 8001 input tokens
```

Now each endpoint carries `context_tokens`; the reply budget is clamped to what is left
after the prompt; and when the prompt still will not fit, long transcripts are **condensed
into requirement bullets first** (chunked on line boundaries, speaker names kept) rather
than truncated, which would silently drop requirements. Token estimation counts non-Latin
characters about three times heavier than Latin. Measured on the real recording: estimate
3478 against an actual 3332 prompt tokens, 6567 total inside the 16k window.

---

## 7. AI/ML model configuration

`/ai-models/` (capability `model.manage`) holds every endpoint: kind, base URL, model name,
API key, timeout, max tokens, context tokens, temperature, ASR language, extra JSON body
fields, active and default flags.

* **Resolution order** — the active default row for that kind, else the environment
  variables the stack shipped with. Changes take effect on the next call, no redeploy.
* **Certificates** — upload a CA bundle (`ezua-domain-ca-cert.crt`) per endpoint; it
  becomes the trust store for that endpoint only. Client cert and key are supported for
  mTLS. PEM is parsed as a real X.509 certificate on upload.
* **Keys are write-only** — stored server-side, rendered only masked, and a blank field on
  edit keeps the stored value.
* **Test buttons** — connection test against `/v1/models`, plus a real completion
  round-trip for LLM rows. Results are recorded on the row.
* For ASR the chosen endpoint, key, model and CA path are pushed to the `asr` container per
  request, which is why that container needs no configuration of its own.

Configured endpoints:

| Job | Model | Where |
|---|---|---|
| LLM (writes the BRD) | `RedHatAI/Qwen3.6-35B-A3B-NVFP4` | `https://multimodel-qwen36.otw-aiapp.orbitindia.in/v1` |
| ASR (speech to text) | `Qwen/Qwen3-ASR-0.6B` | `https://multimodel-asr.otw-aiapp.orbitindia.in/v1` |
| Diarization | `pyannote/speaker-diarization-3.1` | local, in the `asr` container |
| Denoise | `weya-ai/hush` | local, in the `denoise` container (fallback engine active) |

Both remote endpoints sit behind a private CA and were unreachable until their certificate
was uploaded; the older direct-IP endpoints are now down.

---

## 8. Notifications

The VAPID key pair is generated on first boot into the shared `vapid` volume, so `web` and
`worker` sign with the same key and existing subscriptions survive restarts. The service
worker is served from the site root with `Service-Worker-Allowed: /`. Every push is also
stored as an in-app `Notification`, so nothing is lost when permission is denied; the
topbar bell polls every 30 s. Dead endpoints (404/410) are pruned automatically.

Events: recording processed, speakers needed, processing failed, BRD updated. Notification
of a new BRD version is centralised in `regenerate()`, so the worker, the Sync button and
the Regenerate button all announce it.

---

## 9. Security decisions

* Uploaded recordings are **login-gated**. `/media/` is served by a Django view, and nginx
  proxies `/media/` to Django rather than serving it from disk, so the check applies on
  both ports.
* That view implements HTTP **Range**, so a long recording can be seeked; the stock
  `django.views.static.serve` ignores Range and returned the whole 32 MB file.
* API keys and the HF token are never rendered back into a page.
* Disabling TLS verification on an HTTPS endpoint is allowed only behind an explicit
  "I accept an unverified TLS connection" checkbox.
* A malformed uploaded certificate is caught at upload and, if one slips through, cannot
  500 a page — the probe catches `ssl.SSLError`, which is not an `httpx` error.
* `reset_data` requires `--yes`; `--users` and `--everything` widen the blast radius.

---

## 10. Testing

Three suites, all runnable inside the web container. They build their own fixtures and tear
them down, so they neither depend on nor damage real data.

```bash
docker compose exec web python smoke_test.py                # planning, permissions, AI config
docker compose exec web python smoke_intake.py              # intake pipeline, push, BRD, clips
docker compose exec web python smoke_audio_multilingual.py  # real audio, no mocks allowed
```

* `smoke_test.py` — every page as six user types, JSON endpoints, permission boundaries, a
  scope-resolution table, customer CRUD, people management, AI endpoint CRUD, key masking,
  certificate handling.
* `smoke_intake.py` — synthesises a two-voice recording, runs denoise → ASR → diarization,
  names speakers, maps one to a real user, versions the BRD, exercises push plumbing,
  transcript editing, Sync, clip cutting and the auth gates.
* `smoke_audio_multilingual.py` — real-audio run that **fails if any mock engine was used**,
  and reports script (Devanagari / Latin / mixed), Marathi-versus-Hindi markers and how much
  of the recording is code-mixed.

---

## 11. Bugs found and fixed

| Area | Problem | Fix |
|---|---|---|
| Postgres | PG16 removed `stats_temp_directory`; container unhealthy | dropped the parameter and the second volume |
| Templates | `/service-status/` 500 — chained `default:` with a variable argument raises `VariableDoesNotExist` | `{% firstof %}` |
| Navigation | Two different tab bars; "People" missing on BRD; "Timeline" appearing twice | one shared partial, Gantt renamed |
| Forms | Chrome credential autofill wrote `admin` into the new-person username | `autocomplete="off"` / `new-password` |
| Forms | A failed submit discarded everything typed | invalid posts re-render with values and inline errors |
| Speakers | `unnamed_speakers` ignored the user mapping, so mapped speakers blocked Sync | count only rows with neither name nor member |
| Media | Reprocessing created a duplicate `MediaAsset`; the UI showed stale engine labels | replace in place, newest-first ordering |
| ASR | One dead endpoint pinned the backend to mock forever | 60 s retry cooldown |
| Push | `pywebpush` was handed PEM text when it needs a file path — delivery could never work | write `vapid.pem`, pass the path |
| UI | Bell badge showed "0" because a class-level `display` beat the `hidden` attribute | `.bell-badge[hidden]{display:none}` |
| UI | Transcript relabel injected the whole option label instead of the person's name | use `data-name` |
| Selectors | A user with no `customer.view` grant still saw customers | return `none()` when the capability is absent |
| Notifications | Only some BRD paths notified; the Regenerate button was silent | centralised in `regenerate()` |
| Media | No Range support and no login check on uploads | range-capable, login-gated view |
| LLM | Fixed `max_tokens` blew the context window on long Devanagari transcripts | context budgeting plus condensing |
| Tests | The people test leaked a real user account per run (18 accumulated) | teardown deletes the account |
| Penpot | Every Design tab operation (open file, sync, share, invite) failed with auth errors; workspaces were `ready` but pointed at bots/teams/files that didn't exist. `pms_web` used `PENPOT_BASE_URL=http://172.16.16.214:9001` (from `.env`) while `pms_worker` still carried `http://172.16.15.76:9001` from before an `.env` change — `restart` doesn't re-read env. Worker provisioned every workspace on the 172.16.15.76 Penpot; web talked to the local 172.16.16.214 one. | `docker compose -f docker-compose.prod.yml up -d --force-recreate worker`; reset + re-queue all 4 `PenpotWorkspace` rows (clear remote ids + `bot_password`); worker re-provisioned all 4 on the correct Penpot. Verified: 4 bots/files/teams/tokens on `penpot-penpot-postgres-1`, `services.sync()` works. Follow-up in REMEDIATION.md Phase 1.7 (guard against web/worker env drift). |
| Penpot | A `PenpotWorkspace` whose remote bot/objects were deleted (Penpot reset) was permanently wedged: `provision()` skipped account creation because `profile_id` was set, then failed on every login; `enqueue_for_project` only re-queued `failed` (not `disabled`); the only escape was deleting the row in admin. The Design tab also did a blocking Penpot call on every GET, and the share-preview `<iframe>` was permanently blank (mixed-content + `X-Frame-Options: SAMEORIGIN`). | REMEDIATION.md Phase 4: `provision()` self-heals (`_ensure_bot` + `_reset_remote_fields`, probes the bot with `get_profile()` and rebuilds on failure, falling back to a fresh `penpot+<key>-<rand>@` address); `services.rebuild()` + a "Rebuild workspace" button + `penpot_provision --repair`; workspace queued via a `post_save` signal on `Project` (covers admin/bulk/CLI); `manage()` renders from stored state, worker refreshes `ready` workspaces every ≥10 min; iframe removed. 13 penpot tests, 53 adjacent — all pass. |
| Postgres | Site-wide intermittent 500s ("Server Error (500)" on any page, cleared by hard refresh, returned). `web` under gunicorn+UvicornWorker had accumulated 100 idle DB connections (`CONN_MAX_AGE=60` × ASGI thread pool × 3 workers) and pinned all of `max_connections=100`; any request needing a fresh connection got `OperationalError: too many clients already`. No traceback in logs because `DEBUG=0` with no `ADMINS`/`LOGGING`. | `CONN_MAX_AGE` → 0 (env-overridable) for the ASGI deploy; Postgres `max_connections=200` + `superuser_reserved_connections=5` in `docker-compose.prod.yml`; added a `LOGGING` config so request exceptions reach stdout. Verified: 0 × 500 and DB backends steady at ~3 under a 50-request burst (was 100). |

---

## 12. Environment notes

* **Django's autoreloader does not fire on Windows bind mounts** — inotify events are not
  delivered. After editing Python, run `docker compose restart web worker`.
* JS and CSS changes need `docker compose exec web python manage.py collectstatic
  --noinput` plus a cache-busting reload; WhiteNoise serves the collected copy.
* `services/asr` and `services/denoise` have **no bind mount** — edits there need
  `docker compose build asr` / `denoise`.
* `.env` changes need `--force-recreate` on the affected containers.

---

## 13. Current state

Running: `pms_web`, `pms_worker`, `pms_db`, `pms_proxy`, `pms_asr`, `pms_denoise`.

Data: customer **CNH**, project **HU · handling UNIT**, one real audio timeline entry, a
versioned BRD. Users: `admin`, `anitakulkarni` (plus any added since).

Engines in use: **LLM remote — live**, **ASR remote — live**, **diarization — pyannote
installed but gated**, **denoise — numpy fallback**.

### Open items

1. **pyannote gate (blocking real diarization).** `pyannote/speaker-diarization-3.1`
   downloads fine, but its dependency `pyannote/segmentation-3.0` returns **403** for
   account `justnavneet`. Accept the conditions at
   <https://huggingface.co/pyannote/segmentation-3.0>, then reprocess. Until then the
   diarizer falls back to even 25-second windows, so speaker turns — and the per-speaker
   clips — are arbitrary slices rather than true single-speaker audio.
2. ~~**Pin pyannote 4.0.7 in the image.**~~ **Done 2026-09-04.** `services/asr/Dockerfile`
   now installs `pyannote.audio==4.0.7` (3.3.2 imported `torchaudio.AudioMetaData`, removed
   in torchaudio ≥2.9, so it failed to import at all). `load_diarizer` in `app.py` switched
   to `token=` (4.x removed `use_auth_token=`). Verified in the rebuilt image: `import
   pyannote.audio` → 4.0.7 OK; the diarizer load now reaches the model download and fails
   cleanly on the HF gate (item 1) instead of an ImportError.
3. **Denoise runs the numpy spectral gate.** `weya-ai/hush` ships no Python module — only
   `model_best.ckpt` and a DeepFilterNet-style ONNX triplet (`enc` / `erb_dec` / `df_dec`).
   Real inference needs an onnxruntime path plus the libdf ERB/DF feature pipeline.
4. **Push delivery is unproven end to end.** Everything up to the browser prompt is
   verified; granting permission is native UI that automation cannot click.
5. **Mobile layout is unverified.** The breakpoints (1100 / 860 / 520 px) are written but
   the automated browser refused to resize.
6. **Rotate the HF token.** It was pasted into chat and written to `.env` in plain text.
7. **No version control.** `git init` would make this log redundant and make rollbacks
   possible.

### Suggested next steps

* Clear the segmentation-3.0 gate, reprocess entry 17, compare real speaker turns against
  the 41 mock windows, then re-sync the BRD.
* Bake pyannote 4.0.7 into the image and re-run `smoke_audio_multilingual.py`, which fails
  on any mock engine.
* Decide whether the hush ONNX path is worth wiring, or keep the fallback denoiser.
* Consider a real queue (Celery or similar) if intake volume grows beyond one polling worker.

---

## 14. Claude Code / MCP access (app `mcp`)

**Why.** Developers using Claude Code to build or fix a project's code had none of the
project's context in their terminal — the BRD, the coding-standard docs, the open bugs,
the requirements and their field-level specs. This gives each project one MCP link that
Claude Code connects to (`claude mcp add --transport http …`), exposing that project's
Ask AI / RAG, tickets and sprint module as MCP tools, hard-scoped to the one project.

**Shape.** A hand-rolled MCP Streamable-HTTP endpoint (JSON-RPC 2.0), no new dependency,
served by the existing `web` container. One endpoint: `POST /mcp/<token>/` (or `POST /mcp/`
with `Authorization: Bearer <token>` — the log-safe form). `GET` → 405; notifications → 202;
stateless (no session id).

**Auth (`apps/mcp/auth.py`, `models.ProjectMCPToken`).** The token is a 32-byte urlsafe
secret, shown once at creation, stored only as SHA-256 + a display prefix. Each token binds
to one project **and** the user who created it; every request re-checks that the user is
active and can still see the project. Per-token fixed-window rate limit (120/min) and
`last_used_at`. Revocable; optional `expires_at`.

**Scope enforcement (`apps/mcp/tools.py`).** Every tool filters through the normal
`visible_*(user)` selectors **and** `.filter(project=token.project)` — a token cannot reach
another project even if its user could. Reads are always allowed; writes need **both** the
token's opt-in scope flag (`allow_ticket_write` / `allow_sprint_write`) **and** the bound
user's capability on that project (`ticket.triage`, `sprint.manage`, …).

**Tools.** Context/RAG: `project_overview`, `search_docs`, `ask_project`, `query_data`
(reuses the `rag_reader` sandbox), `get_document` (tech_stack / dev_instructions /
build_plan / brd), `get_design_workspace` (the project's Penpot file id + URLs, for the
`penpot` MCP — read-only, added in REMEDIATION.md Phase 5). Tickets: `list_tickets`, `get_ticket`, + writes `comment_ticket`,
`set_ticket_status`, `create_ticket`. Sprints: `list_sprints`, `get_sprint`,
`list_requirements`, `get_requirement` (acceptance criteria + field specs),
`list_test_cases`, `get_test_case`, `list_external_services`, + writes
`set_requirement_status`, `record_test_result`, `add_acceptance_criterion`. Plus MCP
resources (`planwise://project/{overview,brd,design,doc/*}`) and prompts (`fix_bug`,
`implement_requirement`).

**Management.** New capability `mcp.manage` (granted to lead/manager/director/admin). A
"Claude Code" tab on the project page (`/projects/<pk>/mcp/`) mints a link — full URL shown
once — lists links with last-used, and revokes. Also in Django admin and via
`manage.py mcp_token {create,list,revoke}`. All mint/revoke actions land in the project
activity log.

**Verified.** `apps/mcp/tests.py` (22 tests: auth, cross-project isolation, write gating,
doc/search) plus a live pass against `https://localhost/` — handshake, `tools/list`,
`project_overview`, `list_tickets`, `get_requirement`, `query_data`, `ask_project`
(synthesised answer over the SQL tool), and `comment_ticket` (write persisted with a
`TicketEvent`). Read-only tokens correctly refuse writes.

**Note.** `web` runs under gunicorn + UvicornWorker (no autoreload) — new code needs a
`docker restart pms_web` (or `docker kill --signal=HUP pms_web` where a `kill` is present).

---

## 15. Claude Code dev log (app `devlog`)

**Why.** The MCP link (§14) let Claude Code read a project and touch tickets/sprints, but
there was nowhere for it to report *its own* progress — what it built, how far along, which
milestone it reached. This adds a separate model and a **Dev Log** project tab.

**Model — `devlog.DevUpdate`.** One row per progress update: `headline`, `details`
(markdown), `kind` (feature/bugfix/refactor/test/docs/chore/research), `status`
(in_progress/completed/blocked/needs_review), a **free-text `milestone`** label +
`milestone_done` flag, optional links to a `Sprint` / `Requirement` / `Ticket` / `Task`,
`files_changed` (JSON list), `branch` / `commit` / `pr_url`, `created_by`, `source`
(mcp/web/admin) and `via_token` (which MCP link pushed it).

**Milestone rollup.** `devlog.selectors.milestone_progress()` derives the milestone set
from the distinct `milestone` labels in first-seen order; a milestone is done once any
update for it is flagged `milestone_done` (sticky), status = the latest update's. Drives
the "N / M complete" bar on the tab. No `projects.Milestone` coupling, no mutation of
sprint/ticket state.

**MCP tools (added to `apps/mcp/tools.py`, no token-scope change).** `push_dev_update`,
`update_dev_update`, `list_dev_updates`, `get_dev_update`, `list_dev_milestones`. `push`
resolves the sprint/requirement/ticket/task refs through the same project-scoped helpers,
writes with `source="mcp"` + `via_token`, logs an `Activity`, and notifies the project
audience (kind `devlog`). Available on any valid link — it only writes to the devlog model.

**UI / permissions.** New capabilities `devlog.view` (guest→all, mirrors `sprint.view`) and
`devlog.manage` (lead/manager). **Dev Log** tab at `/projects/<pk>/devlog/` — milestone
progress + filterable update feed; add/edit/delete for `devlog.manage`; Django admin.
Updates also surface in the existing project Activity tab.

**Verified.** `apps/devlog/tests.py` (11 tests: rollup ordering/stickiness, `visible_dev_updates`
scoping, tab render + capability gates, UI create/edit/delete, MCP push/list/update +
project-scoped link resolution) — 53/53 with `apps.mcp`/`apps.tickets`/`apps.sprints`. Live
pass against `https://localhost/`: `push_dev_update` → entry on the Dev Log tab + Activity
feed + 7 audience notifications; `update_dev_update` flipped the milestone rollup to 1/1;
web create/edit worked. Test data cleaned up.
