# Deploying Planwise

Everything that must survive a rebuild is a **bind mount under the install directory**, so
the whole deployment is one folder you can tar, rsync or snapshot.

| Host path | Container | Holds |
|---|---|---|
| `./` | `/app` | source code — edit on the host, no rebuild needed |
| `./data/postgres` | `/var/lib/postgresql/data` | the database (Postgres 16 + pgvector) |
| `./data/models` | `/models` | model weights (HF cache, shared by `asr` + `denoise`) |
| `./media` | `/app/media` | uploads, transcoded audio, provider TLS certs |
| `./data/staticfiles` | `/app/staticfiles` | collected static assets |
| `./data/certs` | `/etc/nginx/certs` | the proxy's TLS key pair |
| `./data/secrets` | `/app/secrets` | VAPID web-push key pair |

Nothing lives in a named Docker volume any more, so `docker compose down -v` cannot
destroy your data and moving to another host is a file copy.

`web` and `worker` run under **uvicorn's gunicorn worker class**, not plain WSGI — the
Ask AI chat page holds a WebSocket connection open, and a sync worker cannot serve that
at all. `db` is `pgvector/pgvector:pg16` (stock Postgres 16 plus the `vector` extension,
used by Ask AI's document search) — same major version and data format as plain
`postgres:16`, just with one extra extension available.

---

## Requirements

- Docker Engine 24+ with the Compose plugin (`docker compose version`)
- ~8 GB disk: ~2.5 GB images, ~2 GB diarizer weights, the rest for uploads
- Ports 80 and 443 free (change `HTTP_PORT` / `HTTPS_PORT` in `.env` if not)
- Optional: an NVIDIA GPU + Container Toolkit for faster diarization
- If your LLM/ASR/embedding/reranker endpoints live behind hostnames with no public DNS
  record (an internal-only name, resolved only via a static `/etc/hosts` entry on hosts
  that already talk to it), you'll need `docker-compose.hosts-override.yml` — see its own
  header comment. `deploy.sh` picks it up automatically if the file exists.

## Install

```bash
unzip pms-deploy-<date>.zip -d /opt/pms
cd /opt/pms
cp .env.example .env
nano .env          # fill in every <...> placeholder
./deploy/deploy.sh
```

`deploy.sh` refuses to start while any `<...>` placeholder is left in `.env`, creates the
bind-mount directories, builds, and waits for migrations.

### GPU vs CPU

`deploy.sh` decides automatically. It tries to start a throwaway `--gpus all` container;
if that works it layers `docker-compose.gpu.yml` (GPU for the diarizer in `asr` and the
hush weights in `denoise`), and if it fails — no GPU, or a broken/absent NVIDIA Container
Toolkit — it stays CPU-only instead of failing the deploy. Override with `GPU=` in `.env`
or the environment:

| `GPU` | Behaviour |
|---|---|
| `auto` (default) | GPU when a `--gpus all` container starts, else CPU |
| `1` | force the GPU overlay — deploy fails if the NVIDIA runtime is broken |
| `0` | never use the GPU overlay |

Either way both audio services also choose CUDA vs CPU at runtime (`DIAR_DEVICE`,
`DENOISE_DEVICE` = `auto|cuda|cpu`) and fall back to CPU on any CUDA error, so a
half-working driver degrades rather than crashing. `/health` on each service reports the
device actually in use.

A manual `docker compose` invocation does **not** run this probe — add
`-f docker-compose.gpu.yml` yourself only on a host you know has a working GPU runtime.

First boot takes a while: with `INSTALL_HUSH=1` / a CUDA torch index the `denoise` image
pulls ~1 GB of wheels, and the diarizer downloads its weights into `./data/models` on the
first transcription. Both are cached — later rebuilds reuse them.

## What `.env` must contain

At minimum, and none of these have safe defaults:

- `SECRET_KEY` — `python -c "import secrets;print(secrets.token_urlsafe(64))"`
- `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` — your real hostname, never `*`
- `POSTGRES_PASSWORD`, `DJANGO_SUPERUSER_PASSWORD`
- `CERT_HOST` — the hostname the self-signed certificate is issued for. `CERT_SAN`
  (optional, comma-separated) adds extra names/IPs to it; `localhost` + `127.0.0.1`
  are always included. The proxy regenerates the self-signed cert automatically once
  it expires or stops covering `CERT_HOST`; an operator-installed CA cert in
  `./data/certs` is detected and left untouched.
- `LLM_BASE_URL` / `LLM_API_KEY`, `ASR_BASE_URL` / `ASR_API_KEY` — your inference endpoints
- `HF_TOKEN` — a Hugging Face read token, **and** you must accept the conditions on both
  `pyannote/speaker-diarization-3.1` and `pyannote/segmentation-3.0`, or diarization falls
  back to mock speaker turns
- `RAG_READER_PASSWORD` — `python -c "import secrets;print(secrets.token_urlsafe(24))"`.
  Required even if you don't plan to use Ask AI yet: the migration that sets up its
  read-only database role reads this at migrate time and **hard-fails without it**.

`EMBEDDING_BASE_URL` / `RERANKER_BASE_URL` (and their `_API_KEY`, `_MODEL`) can be left
blank in `.env` — configure those two providers on the **AI models** page after first
boot instead, same as LLM/ASR can be. A database entry always wins over the env fallback
when both exist.

`PENPOT_*` are optional and off by default (`PENPOT_ENABLED=0`). Set `PENPOT_ENABLED=1`
and `PENPOT_BASE_URL` to give every project its own isolated Penpot design workspace —
see *Penpot design workspaces* below.

`DEBUG=0` is set in the template. Leave it that way.

## Verify

All six containers should be `Up`, with `db`, `denoise` and `asr` reporting `(healthy)`:

```bash
docker compose -f docker-compose.prod.yml ps
```

The app has no unauthenticated health route, so check that it answers and redirects an
anonymous request to the login page (`302`):

```bash
curl -skI https://<your-host>/ | head -1
```

The two audio services do expose one:

```bash
docker compose -f docker-compose.prod.yml exec denoise python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8100/health').read().decode())"
docker compose -f docker-compose.prod.yml exec asr     python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8200/health').read().decode())"
```

Signed in as the superuser, the **AI models** page (`/ai-models/`) tests each configured
endpoint from inside the network, which is the quickest way to confirm the LLM, ASR,
embedding and reranker credentials are all right.

The denoise health line reports which engine is live. `"engine":"fallback"` with
`"load_error":"No module named 'torch'"` is expected while `INSTALL_HUSH=0` — the service
runs its numpy spectral gate instead of the hush weights. Set `INSTALL_HUSH=1` and rebuild
to change that.

**Ask AI**, specifically: open any project's Ask AI tab and confirm the status dot in the
toolbar reaches "Connected" — that's the WebSocket handshake succeeding end to end through
nginx. If it sits on "Reconnecting…", check that nginx actually received the `Upgrade`
header (the shipped `nginx.conf` already sets it; only relevant if you've customized the
proxy config) and that `web`'s container command really is the `uvicorn_worker` one shown
in `docker-compose.prod.yml`, not a plain `gunicorn ... wsgi:application` left over from
an older copy of this file.

## Day-to-day

```bash
# logs
docker compose -f docker-compose.prod.yml logs -f web worker

# restart after editing code (code is bind-mounted, so no rebuild)
docker compose -f docker-compose.prod.yml restart web worker

# rebuild after changing requirements.txt or a Dockerfile
docker compose -f docker-compose.prod.yml up -d --build

# database-only dump -> ./data/backups/pms-<timestamp>.sql.gz
./deploy/backup.sh

# database-only restore (destructive, asks for confirmation)
./deploy/restore.sh data/backups/pms-20260820-101500.sql.gz

# everything -> ./data/backups/pms-full-<timestamp>.zip
# code + .env + certs + secrets + staticfiles + media + a fresh database dump
./deploy/full-backup.sh
```

`full-backup.sh` is the one to reach for before a risky change, or on a schedule (cron it).
It deliberately leaves `./data/postgres`'s raw files out of the zip — see the note in that
script — and dumps the database the same safe way `backup.sh` does instead.

> **The zip it produces contains `.env` in plain text** — real passwords and API keys.
> Store it somewhere access-controlled: encrypted at rest, restricted to people who should
> already have production access. Never a public bucket, never email, never a shared drive
> without access control.

## Disaster recovery

Restoring onto the **same** running stack (you broke something, want last night back):

```bash
./deploy/restore.sh data/backups/pms-20260820-101500.sql.gz
```

Restoring onto a **fresh host** from a `full-backup.sh` zip:

```bash
unzip pms-full-<date>.zip -d /opt
cd /opt/pms
./deploy/deploy.sh                      # builds and starts everything except the data
docker compose -f docker-compose.prod.yml stop web worker
./deploy/restore.sh data/backups/pms-<date>.sql.gz
docker compose -f docker-compose.prod.yml start web worker
```

Then, **only on a fresh Postgres instance that never ran this app's migrations before**:
`pg_dump` (what both backup scripts use) captures tables, schemas and views, but **not**
cluster-level roles — the `rag_reader` role Ask AI's SQL tool connects as is a role, not a
table, so it does not travel in the dump. The restored `django_migrations` table will
already list that migration as applied, so Django won't recreate it on its own. Force it:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py migrate rag 0002
docker compose -f docker-compose.prod.yml exec web python manage.py migrate rag
```

That rolls the `rag` schema/role migration back (which drops the role, harmlessly, if it's
not there) and reapplies it, recreating the schema, views and role fresh using whatever
`RAG_READER_PASSWORD` is in the new host's `.env`. Skip this if you restored onto the
*same* Postgres container the dump came from — the role was never dropped, only the
database was.

## Moving to another host

```bash
docker compose -f docker-compose.prod.yml down
./deploy/full-backup.sh                    # or: tar czf pms-full.tar.gz --exclude data/postgres .
# ... copy the zip, extract on the new host ...
./deploy/deploy.sh
# then the disaster-recovery restore steps above
```

Because Postgres stores its cluster in `./data/postgres`, copying that directory directly
only works onto the **same Postgres major version** (16) — and only when Postgres was
stopped first, or the copy risks being torn mid-write. Across major versions, or from a
live system, use `full-backup.sh` / `restore.sh` instead, which go through `pg_dump`
either way.

## Claude Code / MCP access

Each project can hand out one MCP link that connects Claude Code to that project's Ask AI /
RAG, tickets and sprint data — scoped to the one project and nothing else.

- **Generate a link.** In the app: open a project → **Claude Code** tab (needs the
  `mcp.manage` capability — lead and above by default) → *Create a link*, tick write scopes
  if wanted, copy the URL shown once. Or from the shell:
  ```bash
  docker compose -f docker-compose.prod.yml exec web \
    python manage.py mcp_token create --project PLAT --user aisha --base-url https://your-host
  ```
- **Add it to Claude Code** (on the developer's machine):
  ```bash
  claude mcp add --transport http planwise-plat https://your-host/mcp/<token>/
  ```
  The log-safe alternative keeps the secret out of URLs: point it at `https://your-host/mcp/`
  and pass `--header "Authorization: Bearer <token>"`.
- **Security.** Tokens are stored hashed, are revocable from the same tab (or
  `python manage.py mcp_token revoke <id>`), carry the permissions of the user who created
  them, and are read-only for tickets/sprints unless a write scope was ticked. The endpoint
  is rate-limited (120 req/min per token). The token appears in nginx access logs when it is
  in the URL path — prefer the `Authorization` header form if that matters, HTTPS only.
- **Dev Log.** As Claude Code works it can push progress updates (`push_dev_update`) — no
  extra scope needed. They show on the project's **Dev Log** tab (a milestone rollup + the
  update feed, visible to anyone with `devlog.view`) and in the Activity feed.
- **After deploying new MCP code**, restart `web` (gunicorn does not autoreload):
  `docker compose -f docker-compose.prod.yml restart web`.

## Penpot design workspaces

With `PENPOT_ENABLED=1` (and `PENPOT_BASE_URL` pointing at a self-hosted Penpot),
**creating a project provisions a dedicated Penpot workspace** for it: its own bot
account, its own team, one design project and a starter file. Isolation is by
account — the bot belongs to exactly one team, so its credentials and access token
never reach another project's designs.

- **Penpot instance requirements.** Registration must be allowed, and
  `PENPOT_FLAGS` should include `disable-email-verification` so bot accounts
  activate without an email round-trip. Working SMTP (`enable-smtp`) is only needed
  if you want team-invite emails delivered — the **Design** tab also shows a direct
  accept link for every invite regardless.
- **How it runs.** Project creation writes a queued `PenpotWorkspace` row; the
  background **worker** (`pms_worker`) provisions it within a few seconds. Backfill
  existing projects or force one:
  ```bash
  docker compose -f docker-compose.prod.yml exec web \
    python manage.py penpot_provision --backfill
  docker compose -f docker-compose.prod.yml exec web \
    python manage.py penpot_provision --project PLAT
  ```
- **In the app.** Open a project → **Design** tab (needs `penpot.manage` — lead and
  above). From there: open the file in Penpot, mint a **public no-login view link**
  for stakeholders, invite project members onto the Penpot team, and copy the
  `.mcp.json` (both the `planwise` and `penpot` MCP servers) so Claude Code can
  build the UI.
- **Penpot MCP.** The Penpot 2.16 frontend already proxies its MCP container
  (`/mcp/stream` → `penpot-mcp:4401`, `/mcp/ws` → `:4402`); set
  `PENPOT_MCP_URL=http://<penpot-host>:9001/mcp/stream` in `.env` and the Design tab
  renders it into the copy-paste `.mcp.json`. Blank `PENPOT_MCP_URL` → the tab tells
  the developer to ask an admin to expose it. Nothing is installed with `npx`.
- **Auth note.** Planwise talks to Penpot with the bot's **session** (cookie), not
  the `Authorization: Token` header — a reverse proxy in front of Penpot that strips
  `Authorization` (some do) would otherwise break provisioning. The per-project
  access token is still minted and shown on the Design tab for your own tooling.
- **Teardown.** The Design tab's *danger zone* disables a workspace, optionally
  deleting the Penpot account (which cascades its team and files). Deleting a
  Planwise project leaves the Penpot side intact — disable it first if you want it
  gone.
- **After deploying new Penpot code**, restart `web` and `worker`; after changing
  `PENPOT_*` in `.env`, **recreate both together** —
  `docker compose -f docker-compose.prod.yml up -d --force-recreate web worker`.
  `restart` does *not* re-read env, and if `web` and `worker` end up with different
  `PENPOT_BASE_URL` the worker provisions design workspaces on one Penpot while the
  Design tab talks to another.

## Notes and limits

- **The proxy issues a self-signed certificate on first boot** for `CERT_HOST` (+ `CERT_SAN`).
  Browsers will warn. It regenerates that cert when it expires or `CERT_HOST` changes; set
  `CERT_FORCE=1` to regenerate on the next boot regardless. To use a real cert, drop your
  `server.crt` / `server.key` into `./data/certs` (a non-self-signed pair is left untouched)
  and `up -d --force-recreate proxy`, or put the stack behind your own TLS terminator and
  publish only `HTTP_PORT`.
- **Postgres is not published to the host** in `docker-compose.prod.yml` — only the compose
  network reaches it. Uncomment the `ports:` block if you need external access, and bind it
  to `127.0.0.1`.
- **`web` runs gunicorn with the `uvicorn_worker` class**, not `runserver` and not plain
  WSGI. Tune `GUNICORN_WORKERS` (start at `2 × cores + 1`); its `--timeout` is set to `0`
  deliberately, since gunicorn's normal worker-silence timeout would otherwise kill a
  worker sitting on an idle-but-healthy Ask AI WebSocket between questions.
- **Speech-to-text is remote.** Only the diarizer runs locally. If `ASR_BASE_URL` is
  unreachable the pipeline fails the entry rather than substituting mock text, which is
  deliberate (`ASR_BACKEND=remote`).
- **`docker-compose.hosts-override.yml`, if present, has real IP addresses baked in for
  your specific network.** Don't copy it wholesale into an unrelated deployment — check its
  contents and adjust the addresses first.
- **The dev compose file (`docker-compose.yml`) still uses named volumes and `runserver`.**
  It is unchanged, for local work. Use `-f docker-compose.prod.yml` on servers.
