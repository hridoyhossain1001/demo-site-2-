"""Fail-fast local checks for the stabilized multi-surface release."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops.staging_limited_deploy import read_manifest


REQUIRED_MANIFEST_PATHS = {
    "app/routers/client_auth.py",
    "app/routers/events.py",
    "app/services/client_secrets.py",
    "app/services/fraud_service.py",
    "app/services/usage_service.py",
    "app/static/client-portal/index.html",
    "app/static/client-portal/assets/index-DokrZTa0.js",
    "migrations/versions/c2d3e4f5a6b7_add_capi_signing_secret.py",
    "wordpress-plugin/buykori-adsync.zip",
}

REQUIRED_ENV_DEFAULTS = {
    "ALLOW_CAPI_API_KEY_SIGNING_FALLBACK=false",
    "ALLOW_GLOBAL_COURIER_WEBHOOK_SECRET_FALLBACK=false",
    "ALLOW_GLOBAL_STEADFAST_WEBHOOK_TOKEN_FALLBACK=false",
    "FRAUD_AUTO_HOLD_THRESHOLD=90",
}


def readiness_failures() -> list[str]:
    failures: list[str] = []

    manifest = set(read_manifest())
    missing_manifest = sorted(REQUIRED_MANIFEST_PATHS - manifest)
    if missing_manifest:
        failures.append(f"staging manifest missing: {missing_manifest}")

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    heads = ScriptDirectory.from_config(config).get_heads()
    if heads != ["c2d3e4f5a6b7"]:
        failures.append(f"expected Alembic head c2d3e4f5a6b7, found: {heads}")

    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    setup = (ROOT / "deploy" / "setup.sh").read_text(encoding="utf-8")
    for setting in sorted(REQUIRED_ENV_DEFAULTS):
        if setting not in env_example:
            failures.append(f".env.example missing: {setting}")
        if setting not in setup:
            failures.append(f"deploy/setup.sh missing: {setting}")

    portal_wrapper = ROOT / "client-portal" / "src" / "lib" / "installApiFetch.ts"
    portal_main = (ROOT / "client-portal" / "src" / "main.tsx").read_text(encoding="utf-8")
    if not portal_wrapper.is_file() or "installApiFetch()" not in portal_main:
        failures.append("client portal CSRF/credentials fetch wrapper is not installed")

    plugin_zip = ROOT / "wordpress-plugin" / "buykori-adsync.zip"
    try:
        with zipfile.ZipFile(plugin_zip) as archive:
            plugin_main = archive.read("buykori-adsync/buykori-adsync.php")
            plugin_settings = archive.read("buykori-adsync/includes/admin-settings.php")
        if b"capi_signing_secret" not in plugin_main or b"capi_signing_secret" not in plugin_settings:
            failures.append("plugin ZIP does not contain CAPI signing-secret support")
    except (FileNotFoundError, KeyError, zipfile.BadZipFile) as exc:
        failures.append(f"plugin ZIP is missing or invalid: {exc}")

    return failures


def main() -> int:
    failures = readiness_failures()
    if failures:
        print("Release readiness failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("Release readiness passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
