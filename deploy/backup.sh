#!/usr/bin/env bash
# Dump the database to ./data/backups/. Model weights and uploads are already plain
# files under ./data/models and ./media, so a tar of the install directory is a full backup.
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE="docker compose -f docker-compose.prod.yml"
STAMP=$(date +%Y%m%d-%H%M%S)
OUT="data/backups/pms-${STAMP}.sql"

mkdir -p data/backups
set -a; . ./.env; set +a

$COMPOSE exec -T db pg_dump -U "${POSTGRES_USER:-pms}" -d "${POSTGRES_DB:-pms}" > "$OUT"
gzip "$OUT"
echo "wrote ${OUT}.gz  ($(du -h "${OUT}.gz" | cut -f1))"
