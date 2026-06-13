#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PROJECT_DIR="${PROJECT_DIR:-/var/www/buykori-adsync}"
ENV_FILE="${ENV_FILE:-$PROJECT_DIR/.env}"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_DIR/.backups/postgresql}"
ARCHIVE="${1:-$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'buykori-*.dump' -printf '%T@ %p\n' | sort -nr | head -1 | cut -d' ' -f2-)}"
TEST_DB="buykori_restore_test_$(date -u +%Y%m%d%H%M%S)"

if [[ -z "$ARCHIVE" || ! -r "$ARCHIVE" ]]; then
  echo "No readable backup archive found." >&2
  exit 1
fi

mapfile -t URLS < <(
  ENV_FILE="$ENV_FILE" TEST_DB="$TEST_DB" "$PROJECT_DIR/venv/bin/python" - <<'PY'
import os
from pathlib import Path
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

source = dotenv_values(Path(os.environ["ENV_FILE"])).get("DATABASE_URL")
if not source:
    raise SystemExit("DATABASE_URL is missing")
url = make_url(source.replace("postgresql+asyncpg://", "postgresql://", 1))
print(url.render_as_string(hide_password=False))
print(url.set(database=os.environ["TEST_DB"]).render_as_string(hide_password=False))
PY
)

SOURCE_URL="${URLS[0]}"
TEST_URL="${RESTORE_TEST_DATABASE_URL:-${URLS[1]}}"

if [[ -n "${RESTORE_TEST_DATABASE_URL:-}" ]]; then
  TEST_DB="$(
    RESTORE_TEST_DATABASE_URL="$RESTORE_TEST_DATABASE_URL" "$PROJECT_DIR/venv/bin/python" - <<'PY'
import os
from sqlalchemy.engine import make_url

print(make_url(os.environ["RESTORE_TEST_DATABASE_URL"]).database or "")
PY
  )"
  if [[ -z "$TEST_DB" ]]; then
    echo "RESTORE_TEST_DATABASE_URL must include a database name." >&2
    exit 1
  fi
  if [[ "$TEST_DB" != *restore* && "$TEST_DB" != *test* && "${ALLOW_NON_TEST_RESTORE_DB:-}" != "true" ]]; then
    echo "Refusing to restore into '$TEST_DB'. Use a disposable database containing 'restore' or 'test' in its name." >&2
    exit 1
  fi
  if [[ "${RESTORE_TEST_CONFIRM:-}" != "$TEST_DB" ]]; then
    echo "Set RESTORE_TEST_CONFIRM=$TEST_DB to acknowledge the disposable restore target." >&2
    exit 1
  fi
fi

cleanup() {
  if [[ -z "${RESTORE_TEST_DATABASE_URL:-}" ]]; then
    dropdb --if-exists --force --maintenance-db="$SOURCE_URL" "$TEST_DB" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

pg_restore --list "$ARCHIVE" >/dev/null
if [[ -z "${RESTORE_TEST_DATABASE_URL:-}" ]]; then
  createdb --maintenance-db="$SOURCE_URL" "$TEST_DB"
else
  psql "$TEST_URL" -v ON_ERROR_STOP=1 -Atqc "drop schema if exists public cascade; create schema public;"
fi
pg_restore --dbname="$TEST_URL" --no-owner --no-acl "$ARCHIVE"

TABLE_COUNT="$(psql "$TEST_URL" -Atqc "select count(*) from information_schema.tables where table_schema = 'public';")"
ALEMBIC_VERSION="$(psql "$TEST_URL" -Atqc "select version_num from alembic_version limit 1;" 2>/dev/null || true)"

echo "Restore test successful."
echo "archive=$ARCHIVE"
echo "temporary_database=$TEST_DB"
echo "public_tables=$TABLE_COUNT"
echo "alembic_version=${ALEMBIC_VERSION:-unknown}"
