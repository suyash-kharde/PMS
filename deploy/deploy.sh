#!/usr/bin/env bash
# First-run bootstrap on the server. Safe to re-run: it never overwrites .env or data.
#
#   ./deploy/deploy.sh            auto-detect a usable NVIDIA GPU, else run CPU-only
#   GPU=1 ./deploy/deploy.sh      force the GPU overlay (fail loudly if it can't start)
#   GPU=0 ./deploy/deploy.sh      never use the GPU overlay
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE="docker compose -f docker-compose.prod.yml"

if ! docker compose version >/dev/null 2>&1; then
  echo "error: 'docker compose' not available. Install Docker Engine + the compose plugin." >&2
  exit 1
fi

# GPU overlay selection with graceful fallback. The probe runs the exact container-init
# path the asr/denoise services use, so if it passes the overlay will start, and if the
# host has no GPU / a broken NVIDIA Container Toolkit we quietly stay on CPU instead of
# failing `up`. Both audio services also pick CPU vs CUDA at runtime on their own.
gpu_probe() {
  docker run --rm --gpus all "${GPU_PROBE_IMAGE:-alpine:latest}" true >/dev/null 2>&1
}
# an explicit GPU= in the environment wins; otherwise take one from .env if set
if [ -z "${GPU:-}" ] && [ -f .env ]; then
  GPU="$(grep -E '^GPU=' .env 2>/dev/null | tail -1 | cut -d= -f2 | tr -d '[:space:]' || true)"
fi
case "${GPU:-auto}" in
  1|true|yes)
    COMPOSE="$COMPOSE -f docker-compose.gpu.yml"
    echo "==> GPU overlay: forced on (GPU=1)"
    ;;
  0|false|no)
    echo "==> GPU overlay: disabled (GPU=0) — audio services run on CPU"
    ;;
  *)
    if gpu_probe; then
      COMPOSE="$COMPOSE -f docker-compose.gpu.yml"
      echo "==> GPU overlay: enabled — a usable NVIDIA runtime was detected"
    else
      echo "==> GPU overlay: skipped — no usable NVIDIA runtime; audio services run on CPU"
    fi
    ;;
esac
# static hostname mapping for a private inference host with no public DNS record --
# only applies if this bundle shipped with one (see that file's own header)
[ -f docker-compose.hosts-override.yml ] && COMPOSE="$COMPOSE -f docker-compose.hosts-override.yml"

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from the template."
  echo "Fill in every <...> placeholder, then run this script again:"
  echo "    nano .env"
  exit 1
fi

if grep -q '<[a-z-]*>' .env; then
  echo "error: .env still has unfilled <...> placeholders:" >&2
  grep -n '<[a-z-]*>' .env >&2
  exit 1
fi

# bind-mount targets. Postgres needs its data dir to exist and be empty on first boot.
mkdir -p data/postgres data/models data/staticfiles data/certs data/secrets media
chmod 700 data/postgres

echo "==> building images (first run pulls ~2 GB for the diarizer)"
$COMPOSE build

echo "==> starting"
$COMPOSE up -d

echo "==> waiting for the database"
until $COMPOSE exec -T db pg_isready -U "${POSTGRES_USER:-pms}" >/dev/null 2>&1; do sleep 2; done

# migrations, cache table, static files, providers and the superuser all run from
# entrypoint.sh inside the web container; give it a moment then report.
echo "==> waiting for migrations to settle"
for _ in $(seq 1 60); do
  if $COMPOSE exec -T web python manage.py migrate --check >/dev/null 2>&1; then break; fi
  sleep 3
done

echo
$COMPOSE ps
echo
echo "Done. Open  https://$(grep -E '^CERT_HOST=' .env | cut -d= -f2)/"
echo "The proxy serves a self-signed certificate on first boot — replace ./data/certs"
echo "with a real key pair, or put this behind your own TLS terminator."
