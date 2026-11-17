<<<<<<< HEAD
# Planwise — Project Plan Management System

Django 5 + PostgreSQL 16 (+ pgvector), no JS framework. Vanilla JS + CSS only.
Project planning, a media timeline (text/audio), a self-maintaining, version-controlled
Business Requirement Document written by an LLM, an "Ask AI" RAG chat, an AI sprint planner,
tickets, a per-project Penpot design workspace, and an MCP server for Claude Code.

> **`development_plan.md`** is the living design/build log (accurate, dated). **`REMEDIATION.md`**
> tracks the in-flight fix plan. This README is the quick tour and may lag behind them.

## Run

Local development:

```bash
docker compose up -d --build          # http://localhost:8000
```

Server deployment (gunicorn + uvicorn worker, TLS proxy, bind-mounted `./data`):

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

App: http://localhost:8000 · Admin: http://localhost:8000/django-admin/
Model services (dev compose only — prod does not publish them): http://localhost:8100/health
(denoise) · http://localhost:8200/health (ASR)

### Logins

| user | role | sees |
|---|---|---|
| `admin` / `admin123` | Administrator (superuser) | everything |
| `dana` / `demo1234` | Director | all projects, manages teams and users |
| `marco` / `demo1234` | Project Manager | creates projects, all projects, regenerates BRDs |
| `aisha` / `demo1234` | Team Lead | own team's projects, timeline, BRD generate/edit |
| `lena` / `demo1234` | Member | own tasks, can add timeline entries |
| `guest1` / `demo1234` | Guest | read-only, own records |
| `priya`, `victor`, `mei` / `demo1234` | Guest | client-side participants on the demo projects |

## Containers

| service | what it is |
|---|---|
| `web` | Django. Dev: `runserver` with autoreload. Prod: `gunicorn` + `uvicorn_worker.UvicornWorker` (ASGI — the Ask AI chat holds a WebSocket open). Source bind-mounted `./:/app`. |
| `worker` | `manage.py process_queue --loop` — drains queued timeline entries (denoise → diarize → transcribe → BRD), sprint-plan runs, and Penpot workspace provisioning. |
| `db` | `pgvector/pgvector:pg16` — Postgres 16 + the `vector` extension (Ask AI's RAG index). Holds app tables **and** the Django cache table `pms_cache`. |
| `proxy` | nginx TLS terminator; self-signed cert on first boot; proxies `/media/` to Django so the login check applies on both ports. |
| `denoise` | OpenAI-compatible speech enhancement — `weya-ai/hush` (runs a numpy spectral-gate fallback unless `INSTALL_HUSH=1`). |
| `asr` | OpenAI-compatible ASR + speaker diarization. ASR is a **remote** endpoint (`Qwen/Qwen3-ASR-0.6B` by default, `ASR_BASE_URL`); diarization runs locally with `pyannote.audio` 4.x (`pyannote/speaker-diarization-3.1`, needs `HF_TOKEN` + the model gate accepted, else falls back to mock turns). |

Volumes (dev): `pgdata` (database **and** the Django cache table `pms_cache`), `staticfiles`,
`hfcache` (downloaded model weights), `certs`, `vapid`. Prod bind-mounts these under `./data`.
Cache backend is `django.core.cache.backends.db.DatabaseCache` in Postgres; sessions use
`cached_db`. `CONN_MAX_AGE=0` — the ASGI server is multi-threaded and persistent connections
accumulate.

## Timeline → BRD

1. **Add to the timeline** (`/projects/<id>/timeline/`) — type a note, drop a text file
   (`.txt .md .csv .json .srt .vtt`) or an audio/video file
   (`.wav .mp3 .m4a .ogg .opus .flac .webm .mp4`). Queue it, or process it immediately.
2. **Audio is cleaned then transcribed** — the file goes to the denoise service, the
   enhanced 16 kHz wav goes to the ASR service, and the reply is stored as
   `TranscriptSegment` rows with anonymous diarizer labels (`SPEAKER_00` …).
3. **Name the speakers** — the entry parks in `awaiting_speakers`. On the entry page each
   voice gets a free-text name and/or a mapping to a project participant; the transcript
   relabels live as you type.
4. **The BRD is rewritten** — once every speaker is named, the LLM receives the current
   BRD, the participant briefs, and the new input with speakers resolved to real names.
   It returns the complete document, stored as a **new immutable version**.

Requirements are attributed in the document itself, e.g.
`**NFR-001:** … 99.9% uptime … (raised by Priya Nair, Client — Product Owner)`.

### Version control

`BRDVersion` rows are append-only: number, full markdown, change summary, unified diff
against the previous version, source timeline entry, author, model name and token usage.
`/projects/<id>/brd/history/` lists every revision and renders a coloured diff between any
two. Any version can be viewed (`?v=3`) or downloaded as `.md`. Hand edits create a new
version too — nothing is ever overwritten.

## Participants

Each project carries people from **both sides** (`ProjectMembership.side` = `client` /
`internal`), each with position, role description, responsibility, remarks and a primary
contact flag — managed at `/projects/<id>/participants/`. That block is fed to the BRD
writer verbatim, and named speakers are cross-referenced against it.

## Models and endpoints

The BRD writer talks to an OpenAI-compatible chat endpoint (configured in `.env`):

```
LLM_BASE_URL=http://172.16.15.111:7879/v1
LLM_MODEL=RedHatAI/Qwen3.6-35B-A3B-NVFP4
LLM_API_KEY=ollama
```

Both audio services are OpenAI-compatible:

```bash
curl -X POST http://localhost:8100/v1/audio/enhancements -F file=@noisy.wav -o clean.wav
curl -X POST http://localhost:8200/v1/audio/transcriptions \
     -F file=@clean.wav -F response_format=verbose_json -F diarize=true -F language=de
```

`/v1/audio/transcriptions` returns the OpenAI verbose shape plus a `speaker` on every
segment and a `speakers` roster. `GET /v1/models` and `GET /health` exist on both.

### Two models, two jobs

| job | model | where it runs |
|---|---|---|
| speech to text | `Qwen/Qwen3-ASR-0.6B` (default) | remote OpenAI-compatible endpoint (`ASR_BASE_URL`) |
| speaker diarization | `pyannote/speaker-diarization-3.1` (`pyannote.audio` 4.x) | locally, inside the `asr` container |

The Qwen endpoint returns **text only** — it rejects `verbose_json`, so there are no word
timestamps to align speakers against, and its context is short. The service therefore
works the other way round:

```
diarize → slice the audio on speaker turns → transcribe each slice → reassemble
```

Each turn becomes one transcript segment with real start/end times and a speaker label,
every request stays inside the model's context (`MAX_CHUNK_SECONDS`, default 25s), and
language is auto-detected unless you set `ASR_LANGUAGE` (or pass `language=` per request).
Turns the model returns nothing for are dropped, so silence never becomes invented text.

### Real weights vs fallback engines

Each service reports which engine it used (`x-engine` header, `asr_engine` /
`diarization_engine` fields, and the *Model services* page):

| job | real engine | fallback |
|---|---|---|
| denoise | `hush` — DeepFilterLib + weya-ai/hush checkpoint | `fallback` — spectral gating in numpy |
| ASR | `qwen-remote` — the remote endpoint (**default, live**) | `mock` — deterministic placeholder text |
| diarization | `pyannote` — `pyannote.audio` 4.x, `pyannote/speaker-diarization-3.1` | `mock` — even turns across the recording |

ASR needs no local weights. Diarization does: set `HF_TOKEN` in `.env` and accept the model
conditions on **both** `pyannote/speaker-diarization-3.1` and `pyannote/segmentation-3.0`
for that account, then `docker compose ... up -d asr`. Weights land in `hfcache` /
`./data/models`. Without a valid token the service falls back to mock (even 25s) turns —
`/health` shows `"engine": "mock+remote"`.

For a GPU host, uncomment the `deploy.resources` block on the `asr` service.

## User types and hierarchy

Nothing about roles is hard-coded in Python conditionals. Four data-driven layers:

1. **Capability** (`accounts.Capability`) — an atomic permission such as `brd.generate`.
   New ones can be added at runtime from *Administration → Capabilities*.
2. **Role** (`accounts.Role`) — a user type. Roles form a **tree**; a child inherits every
   ancestor grant, closest role wins on conflict.
3. **Scope** — each grant carries `own` / `team` / `all`.
4. **Per-user override** (`accounts.UserCapability`) — grant or `deny` for one person.

```python
has_cap(user, "brd.generate", project)   # bool, scope-aware
require(user, "timeline.add", project)   # raises PermissionDenied
```

Templates: `{% load perms %}` then `{% if user|can:"brd.edit" %}`.
Querysets are scoped once in [selectors.py](apps/projects/selectors.py).

Four hierarchies: role tree, team tree (`Team.parent`), reporting line (`User.manager`),
and work breakdown (programme → project → task → subtask). `/org-chart/` draws all three
org trees.

## Verify

```bash
docker compose exec web python smoke_test.py
```
Renders every planning page as six user types, exercises the JSON endpoints, asserts the
permission boundaries and prints the resolved scope per role.

```bash
docker compose exec web python smoke_intake.py
```
Synthesises a two-voice recording, runs denoise → ASR → diarization, names the speakers,
maps one to a real user, and versions the BRD (falls back to a stub version if the LLM
endpoint is unreachable). Both suites currently report `ALL CHECKS PASSED`.

Worker path:
```bash
docker compose logs -f worker
```

## Layout

```
config/                 settings, urls, wsgi, asgi (+ channels routing)
apps/accounts/          User, Role, Capability, Team + the permission engine
apps/projects/          Customer, Workflow, Project, Task, Milestone, ProjectMembership,
                        ProjectDocument + queryset selectors
apps/intake/            TimelineEntry, MediaAsset, Speaker, TranscriptSegment, BRD, BRDVersion
  services/llm.py       OpenAI-compatible chat client
  services/audio.py     denoise + ASR clients
  services/brd.py       prompt construction, response parsing, versioning
  services/pipeline.py  entry processing orchestration
apps/sprints/           Sprint, Requirement, AcceptanceCriterion, FieldSpec, UnitTestCase,
                        risks, dependencies, ExternalService + the AI SprintPlanRun pipeline
apps/tickets/           Ticket, TicketComment/Attachment/Event — bug / change-request triage
apps/rag/               Chunk (pgvector), ChatSession — "Ask AI" agent + the rag_reader SQL sandbox
apps/mcp/               ProjectMCPToken — the per-project MCP server for Claude Code
apps/devlog/            DevUpdate — Claude Code progress log + milestone rollup
apps/penpot/            PenpotWorkspace — per-project isolated Penpot design workspace
apps/aimodels/          ModelProvider — every AI endpoint / key / certificate, editable in the UI
apps/notify/            PushSubscription, Notification — Web Push (VAPID) + in-app inbox
apps/bulk/              ImportBatch — spreadsheet bulk import (customers / projects / people)
apps/core/              dashboard, search, range-capable media serving, context processor
services/proxy/         nginx TLS terminator
services/denoise/       FastAPI wrapper around weya-ai/hush
services/asr/           FastAPI: local pyannote diarization + fan-out to the remote ASR endpoint
templates/, static/     server-rendered pages, one stylesheet, vanilla JS modules
```
=======
# AT-PlanWise
>>>>>>> 90da21cdbeedaeebbfa996a1b61f3a7485de1337
