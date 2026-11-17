#!/usr/bin/env bash
# Full disaster-recovery backup: application code, every bind-mounted volume except the
# live database files, and a fresh database dump -- all in one zip under ./data/backups/.
#
#   ./deploy/full-backup.sh
#
# ./data/postgres is deliberately excluded: copying a running Postgres's data directory
# file-by-file can produce a torn, unstartable copy, since files are being written to
# mid-copy. A pg_dump (this script calls backup.sh for that) is the safe, portable
# equivalent -- restorable with restore.sh, including onto a different Postgres minor
# version. To restore, see the "Disaster recovery" section of DEPLOY.md.
set -euo pipefail

cd "$(dirname "$0")/.."
STAMP=$(date +%Y%m%d-%H%M%S)
OUT="data/backups/pms-full-${STAMP}.zip"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

echo "==> dumping the database"
./deploy/backup.sh
DUMP=$(ls -t data/backups/pms-*.sql.gz | head -1)

echo "==> staging files"
mkdir -p "$WORK/pms"
rsync -a \
  --exclude data/postgres \
  --exclude data/backups \
  --exclude '__pycache__' --exclude '*.pyc' \
  ./ "$WORK/pms/"
mkdir -p "$WORK/pms/data/backups"
cp "$DUMP" "$WORK/pms/data/backups/"

echo "==> zipping"
mkdir -p data/backups
( cd "$WORK" && zip -rq - pms ) > "$OUT"

echo
echo "wrote $OUT ($(du -h "$OUT" | cut -f1))"
echo "contains: application code, .env, data/certs, data/models, data/secrets,"
echo "data/staticfiles, media/, and a database dump at data/backups/pms-*.sql.gz"
echo
echo "!! This zip includes ./.env in plain text -- real passwords and API keys."
echo "!! Store it somewhere access-controlled. Never a public bucket, never email."
