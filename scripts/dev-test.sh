#!/bin/sh
# Run the backend tests (or a manage.py command) without a local Python setup.
#
# Uses the `store-api:dev` image (built by the store-infra dev stack:
#   docker compose -f compose.yaml -f compose.dev.yaml build api)
# plus a throwaway Postgres container, so nothing touches the dev database.
#
#   scripts/dev-test.sh test [pytest args]     e.g. test store_backend/catalog -q
#   scripts/dev-test.sh manage <command...>    e.g. manage export_openapi
#   scripts/dev-test.sh down                   stop the throwaway Postgres
#
# Notes:
# - Test dependencies are installed into the container on every run (about 10 s).
# - `manage` runs as root so it can install the allauth headless-spec extra; a file it
#   creates (such as a new migration) is root-owned: delete and recreate it, or
#   `sudo chown`, before editing.
# - Needs `uv` on the host (mounted into the container).
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="${API_IMAGE:-store-api:dev}"
PG_NAME=store-test-pg
PG_PORT="${TEST_PG_PORT:-55432}"
UV="$(command -v uv)"

ensure_pg() {
  if ! docker ps --format '{{.Names}}' | grep -qx "$PG_NAME"; then
    docker run -d --rm --name "$PG_NAME" -e POSTGRES_PASSWORD=test -e POSTGRES_DB=test \
      -p "127.0.0.1:$PG_PORT:5432" postgres:17 >/dev/null
    until docker exec "$PG_NAME" pg_isready -U postgres -d test >/dev/null 2>&1; do sleep 1; done
  fi
}

run() {
  script="$1"
  shift
  docker run --rm --network host --entrypoint "" -u root \
    -v "$ROOT":/src -v "$UV":/usr/local/bin/uv:ro -w /src \
    -e DATABASE_URL="postgres://postgres:test@127.0.0.1:$PG_PORT/test" \
    -e REDIS_URL=redis://127.0.0.1:6379/0 \
    -e DJANGO_SETTINGS_MODULE=config.settings.test -e PYTHONDONTWRITEBYTECODE=1 \
    "$IMAGE" sh -c "$script" sh "$@"
}

cmd="${1:-}"
[ $# -gt 0 ] && shift
case "$cmd" in
  test)
    ensure_pg
    run 'uv pip install -q --python /app/.venv/bin/python pytest==9.2.0 pytest-django==4.14.0 factory-boy==3.3.3 >/dev/null 2>&1
         exec /app/.venv/bin/python -m pytest -p no:cacheprovider --create-db "$@"' "$@"
    ;;
  manage)
    ensure_pg
    run 'uv pip install -q --python /app/.venv/bin/python "django-allauth[headless-spec]" >/dev/null 2>&1
         exec /app/.venv/bin/python manage.py "$@"' "$@"
    ;;
  down)
    docker stop "$PG_NAME" >/dev/null 2>&1 && echo stopped || echo "not running"
    ;;
  *)
    sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
