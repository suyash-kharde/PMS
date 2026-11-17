#!/bin/sh
set -e

echo "[entrypoint] waiting for postgres at ${POSTGRES_HOST}:${POSTGRES_PORT} ..."
until pg_isready -h "${POSTGRES_HOST}" -p "${POSTGRES_PORT}" -U "${POSTGRES_USER}" >/dev/null 2>&1; do
  sleep 1
done
echo "[entrypoint] postgres ready"

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  # Migrations are committed with the code — generate them in development
  # (`manage.py makemigrations`) and review them, never at container boot. If a
  # model change ships without its migration, fail loudly here rather than
  # silently writing one onto the running source tree.
  if ! python manage.py migrate --check >/dev/null 2>&1; then
    python manage.py makemigrations --check --dry-run || {
      echo "[entrypoint] ERROR: model changes without a migration — run 'manage.py makemigrations' and commit it" >&2
      exit 1
    }
  fi
  python manage.py migrate --noinput

  # postgres-backed Django cache table
  python manage.py createcachetable

  python manage.py collectstatic --noinput --clear >/dev/null 2>&1 || true

  python manage.py seed_providers

  if [ "${SEED_DEMO}" = "1" ]; then
    python manage.py seed_demo
  elif [ "${SEED_CONFIG:-1}" = "1" ]; then
    # keep roles, capabilities, workflows and the superuser in sync without demo content
    python manage.py seed_demo --config-only
  fi
else
  # secondary containers (worker) wait for the web container to finish migrating
  echo "[entrypoint] waiting for migrations ..."
  until python manage.py migrate --check >/dev/null 2>&1; do sleep 2; done
fi

exec "$@"
