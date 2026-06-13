#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-/var/www/buykori-adsync}"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_DIR/.backups/postgresql}"
MAX_BACKUP_AGE_HOURS="${MAX_BACKUP_AGE_HOURS:-36}"
MIN_FREE_GB="${MIN_FREE_GB:-10}"
LOCK_FILE="${MIGRATION_LOCK_FILE:-$PROJECT_DIR/.migration-lock}"
failures=0

ok() { echo "OK: $*"; }
fail() {
  echo "FAIL: $*" >&2
  failures=$((failures + 1))
}

if [[ -e "$LOCK_FILE" ]]; then
  fail "Migration lock is active during preparation mode: $LOCK_FILE"
else
  ok "Migration lock is off"
fi

if curl --fail --silent --show-error http://127.0.0.1:8000/status >/dev/null; then
  ok "Application health endpoint is responding"
else
  fail "Application health endpoint failed"
fi

latest="$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'buykori-*.dump' -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -1 | cut -d' ' -f2-)"
if [[ -z "$latest" ]]; then
  fail "No PostgreSQL backup found in $BACKUP_DIR"
else
  age_seconds=$(( $(date +%s) - $(stat -c %Y "$latest") ))
  max_age_seconds=$(( MAX_BACKUP_AGE_HOURS * 3600 ))
  if (( age_seconds > max_age_seconds )); then
    fail "Latest backup is older than ${MAX_BACKUP_AGE_HOURS}h: $latest"
  else
    ok "Latest backup is fresh: $latest"
  fi

  manifest="${latest%.dump}.manifest"
  if [[ ! -r "$manifest" ]]; then
    fail "Manifest missing: $manifest"
  else
    expected="$(sed -n 's/^sha256=//p' "$manifest")"
    actual="$(sha256sum "$latest" | awk '{print $1}')"
    if [[ -n "$expected" && "$expected" == "$actual" ]]; then
      ok "Backup SHA-256 matches manifest"
    else
      fail "Backup SHA-256 does not match manifest"
    fi
  fi

  if pg_restore --list "$latest" >/dev/null; then
    ok "PostgreSQL archive structure is valid"
  else
    fail "PostgreSQL archive structure is invalid"
  fi
fi

free_kb="$(df -Pk "$PROJECT_DIR" | awk 'NR==2 {print $4}')"
min_free_kb=$(( MIN_FREE_GB * 1024 * 1024 ))
if (( free_kb >= min_free_kb )); then
  ok "At least ${MIN_FREE_GB}GB disk space is free"
else
  fail "Less than ${MIN_FREE_GB}GB disk space is free"
fi

if (( failures > 0 )); then
  echo "Migration preparation check failed with $failures issue(s)." >&2
  exit 1
fi

echo "Migration preparation check passed."
