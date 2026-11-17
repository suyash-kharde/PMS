#!/usr/bin/env bash
# Restore a dump produced by backup.sh.
#
#   ./deploy/restore.sh data/backups/pms-20260820-101500.sql.gz
#
# DESTRUCTIVE: drops and recreates the database. Everything currently in it is lost.
set -euo pipefail

cd "$(dirname "$0")/.."
DUMP="${1:?usage: restore.sh <dump.sql|dump.sql.gz>}"
[ -f "$DUMP" ] || { echo "no such file: $DUMP" >&2; exit 1; }

COMPOSE="docker compose -f docker-compose.prod.yml"
set -a; . ./.env; set +a
DB="${POSTGRES_DB:-pms}"; USER="${POSTGRES_USER:-pms}"

echo "This DROPS the '${DB}' database and replaces it with ${DUMP}."
read -r -p "Type the database name to confirm: " reply
[ "$reply" = "$DB" ] || { echo "aborted"; exit 1; }

# the app holds connections open; stop everything but the database first
$COMPOSE stop web worker

$COMPOSE exec -T db psql -U "$USER" -d postgres -c "DROP DATABASE IF EXISTS \"$DB\";"
$COMPOSE exec -T db psql -U "$USER" -d postgres -c "CREATE DATABASE \"$DB\" OWNER \"$USER\";"

if [ "${DUMP##*.}" = "gz" ]; then
  gunzip -c "$DUMP" | $COMPOSE exec -T db psql -U "$USER" -d "$DB"
else
  $COMPOSE exec -T db psql -U "$USER" -d "$DB" < "$DUMP"
fi

$COMPOSE start web worker
echo "restored from $DUMP"
