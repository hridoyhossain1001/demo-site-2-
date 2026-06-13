#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PROJECT_DIR="${PROJECT_DIR:-/var/www/buykori-adsync}"
ENV_FILE="${ENV_FILE:-$PROJECT_DIR/.env}"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_DIR/.backups/postgresql}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
HOST="$(hostname -s)"
PREFIX="buykori-${HOST}-${TIMESTAMP}"
TMP_DUMP="$BACKUP_DIR/.${PREFIX}.dump.partial"
FINAL_DUMP="$BACKUP_DIR/${PREFIX}.dump"
MANIFEST="$BACKUP_DIR/${PREFIX}.manifest"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

cleanup() {
  rm -f "$TMP_DUMP"
}
trap cleanup EXIT

if [[ ! -r "$ENV_FILE" ]]; then
  echo "Cannot read environment file: $ENV_FILE" >&2
  exit 1
fi

DATABASE_URL="$(
  PROJECT_DIR="$PROJECT_DIR" ENV_FILE="$ENV_FILE" "$PROJECT_DIR/venv/bin/python" - <<'PY'
import os
from pathlib import Path
from dotenv import dotenv_values

value = dotenv_values(Path(os.environ["ENV_FILE"])).get("DATABASE_URL")
if not value:
    raise SystemExit("DATABASE_URL is missing")
print(value.replace("postgresql+asyncpg://", "postgresql://", 1))
PY
)"

echo "Creating PostgreSQL backup: $FINAL_DUMP"
pg_dump \
  --dbname="$DATABASE_URL" \
  --format=custom \
  --compress=9 \
  --no-owner \
  --no-acl \
  --file="$TMP_DUMP"

pg_restore --list "$TMP_DUMP" >/dev/null
mv "$TMP_DUMP" "$FINAL_DUMP"
chmod 600 "$FINAL_DUMP"

{
  echo "created_utc=$TIMESTAMP"
  echo "host=$HOST"
  echo "archive=$(basename "$FINAL_DUMP")"
  echo "archive_bytes=$(stat -c %s "$FINAL_DUMP")"
  echo "sha256=$(sha256sum "$FINAL_DUMP" | awk '{print $1}')"
  echo "pg_dump_version=$(pg_dump --version)"
} >"$MANIFEST"
chmod 600 "$MANIFEST"

find "$BACKUP_DIR" -type f \
  \( -name 'buykori-*.dump' -o -name 'buykori-*.manifest' \) \
  -mtime "+$BACKUP_RETENTION_DAYS" -delete

if [[ -n "${BACKUP_UPLOAD_COMMAND:-}" ]]; then
  BACKUP_FILE="$FINAL_DUMP" BACKUP_MANIFEST="$MANIFEST" bash -c "$BACKUP_UPLOAD_COMMAND"
fi

echo "Backup verified successfully."
cat "$MANIFEST"
