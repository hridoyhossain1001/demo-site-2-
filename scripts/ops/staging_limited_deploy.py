"""Run the stabilized limited deployment scope against a confirmed staging host."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.changed_deploy import should_skip


DEFAULT_MANIFEST = ROOT / "deploy" / "staging_limited_manifest.txt"


def read_manifest(path: Path = DEFAULT_MANIFEST) -> list[str]:
    paths = [
        line.strip().replace("\\", "/")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not paths:
        raise ValueError("Staging deployment manifest is empty.")
    if len(paths) != len(set(paths)):
        raise ValueError("Staging deployment manifest contains duplicate paths.")
    invalid = [path for path in paths if should_skip(path)]
    if invalid:
        raise ValueError(f"Manifest contains non-deployable paths: {invalid}")
    missing = [path for path in paths if not (ROOT / path).is_file()]
    if missing:
        raise ValueError(f"Manifest paths are missing locally: {missing}")
    return paths


def staging_environment(env: dict[str, str], *, apply: bool) -> dict[str, str]:
    if not apply:
        return dict(env)

    staging_host = env.get("STAGING_SSH_HOST", "").strip()
    staging_user = env.get("STAGING_SSH_USER", "").strip()
    active_host = env.get("DO_SSH_HOST", "").strip()
    if not staging_host or not staging_user:
        raise ValueError("Set STAGING_SSH_HOST and STAGING_SSH_USER before an applied staging deploy.")
    if active_host and staging_host.casefold() == active_host.casefold():
        raise ValueError("Refusing staging deploy because STAGING_SSH_HOST matches the active DO_SSH_HOST.")
    if env.get("STAGING_DEPLOY_CONFIRM", "").strip().casefold() != "staging":
        raise ValueError("Set STAGING_DEPLOY_CONFIRM=staging before an applied staging deploy.")
    if env.get("PORTAL_RELEASE_CONFIRM", "").strip().casefold() != "csrf-wrapper":
        raise ValueError(
            "Set PORTAL_RELEASE_CONFIRM=csrf-wrapper after the updated client portal is ready."
        )
    if env.get("PLUGIN_RELEASE_CONFIRM", "").strip().casefold() != "capi-signing-secret":
        raise ValueError(
            "Set PLUGIN_RELEASE_CONFIRM=capi-signing-secret after the updated plugin package is ready."
        )

    prepared = dict(env)
    prepared["DO_SSH_HOST"] = staging_host
    prepared["DO_SSH_USER"] = staging_user
    if env.get("STAGING_REMOTE_DIR"):
        prepared["DO_REMOTE_DIR"] = env["STAGING_REMOTE_DIR"]
    if env.get("STAGING_SSH_KNOWN_HOSTS"):
        prepared["DO_SSH_KNOWN_HOSTS"] = env["STAGING_SSH_KNOWN_HOSTS"]
    return prepared


def build_command(paths: list[str], *, apply: bool, base: str) -> list[str]:
    command = [
        sys.executable,
        str(ROOT / "deploy" / "changed_deploy.py"),
        "--base",
        base,
        "--working-tree",
        "--include-untracked",
    ]
    for path in paths:
        command.extend(["--only", path])
    if not apply:
        command.append("--dry-run")
    return command


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy the stabilized limited scope to staging.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--apply", action="store_true", help="Apply to a separately configured staging SSH host.")
    args = parser.parse_args()

    try:
        paths = read_manifest(args.manifest)
        env = staging_environment(dict(os.environ), apply=args.apply)
    except ValueError as exc:
        parser.error(str(exc))

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"Staging limited deploy: {mode}; {len(paths)} manifest paths.")
    return subprocess.run(
        build_command(paths, apply=args.apply, base=args.base),
        cwd=ROOT,
        env=env,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
