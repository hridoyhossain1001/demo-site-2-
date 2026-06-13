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
TEST_URL="${URLS[1]}"

cleanup() {
  dropdb --if-exists --force --maintenance-db="$SOURCE_URL" "$TEST_DB" >/dev/null 2>&1 || true
}
trap cleanup EXIT

pg_restore --list "$ARCHIVE" >/dev/null
createdb --maintenance-db="$SOURCE_URL" "$TEST_DB"
pg_restore --dbname="$TEST_URL" --no-owner --no-acl "$ARCHIVE"

TABLE_COUNT="$(psql "$TEST_URL" -Atqc "select count(*) from information_schema.tables where table_schema = 'public';")"
ALEMBIC_VERSION="$(psql "$TEST_URL" -Atqc "select version_num from alembic_version limit 1;" 2>/dev/null || true)"

echo "Restore test successful."
echo "archive=$ARCHIVE"
echo "temporary_database=$TEST_DB"
echo "public_tables=$TABLE_COUNT"
echo "alembic_version=${ALEMBIC_VERSION:-unknown}"
