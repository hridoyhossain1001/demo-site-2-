#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PROJECT_DIR="${PROJECT_DIR:-/var/www/buykori-adsync}"
LOCK_FILE="${MIGRATION_LOCK_FILE:-$PROJECT_DIR/.migration-lock}"
ACTION="${1:-status}"

restart_services() {
  sudo supervisorctl restart buykori-web 'buykori-worker:*'
}

case "$ACTION" in
  on)
    {
      echo "locked_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
      echo "locked_by=$(id -un)@$(hostname -s)"
      echo "reason=${2:-migration}"
    } >"$LOCK_FILE"
    chmod 600 "$LOCK_FILE"
    restart_services
    echo "Migration lock enabled. Mutating HTTP requests and background workers are paused."
    ;;
  off)
    rm -f "$LOCK_FILE"
    restart_services
    echo "Migration lock disabled. Web and background workers restarted."
    ;;
  status)
    if [[ -f "$LOCK_FILE" ]]; then
      echo "LOCKED"
      cat "$LOCK_FILE"
    else
      echo "UNLOCKED"
    fi
    sudo supervisorctl status
    ;;
  *)
    echo "Usage: $0 {on [reason]|off|status}" >&2
    exit 2
    ;;
esac
