import os
from pathlib import Path


DEFAULT_MIGRATION_LOCK_FILE = "/var/www/buykori-adsync/.migration-lock"


def migration_lock_path() -> Path:
    return Path(os.getenv("MIGRATION_LOCK_FILE", DEFAULT_MIGRATION_LOCK_FILE))


def migration_locked() -> bool:
    return migration_lock_path().is_file()
