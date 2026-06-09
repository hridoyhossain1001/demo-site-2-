"""Deploy only the compiled client portal fallback and WordPress plugin ZIP."""

from __future__ import annotations

import argparse
import os
import posixpath
import stat
from pathlib import Path

import paramiko


REMOTE_ROOT = os.environ.get("DO_REMOTE_DIR", "/var/www/buykori-adsync")
REMOTE_STATIC_DIR = posixpath.join(REMOTE_ROOT, "app/static/client-portal")
REMOTE_PLUGIN_ZIP = posixpath.join(
    REMOTE_ROOT, "wordpress-plugin/buykori-adsync.zip"
)


def ensure_remote_dir(sftp: paramiko.SFTPClient, remote_dir: str) -> None:
    current = ""
    for part in remote_dir.strip("/").split("/"):
        current += f"/{part}"
        created = False
        try:
            sftp.mkdir(current)
            created = True
        except OSError:
            pass
        if created:
            sftp.chmod(current, 0o755)


def remove_remote_path(sftp: paramiko.SFTPClient, remote_path: str) -> None:
    try:
        info = sftp.stat(remote_path)
    except FileNotFoundError:
        return

    if stat.S_ISDIR(info.st_mode):
        for entry in sftp.listdir_attr(remote_path):
            remove_remote_path(sftp, posixpath.join(remote_path, entry.filename))
        sftp.rmdir(remote_path)
    else:
        sftp.remove(remote_path)


def upload_tree(
    sftp: paramiko.SFTPClient, local_dir: Path, remote_dir: str
) -> int:
    uploaded = 0
    for local_path in sorted(local_dir.rglob("*")):
        if not local_path.is_file():
            continue
        relative_path = local_path.relative_to(local_dir).as_posix()
        remote_path = posixpath.join(remote_dir, relative_path)
        ensure_remote_dir(sftp, posixpath.dirname(remote_path))
        sftp.put(str(local_path), remote_path)
        sftp.chmod(remote_path, 0o644)
        uploaded += 1
    return uploaded


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    workspace = Path(__file__).resolve().parents[2]
    local_static_dir = workspace / "app/static/client-portal"
    local_plugin_zip = workspace / "wordpress-plugin/buykori-adsync.zip"

    if not (local_static_dir / "index.html").is_file():
        raise FileNotFoundError("Compiled client portal index.html is missing.")
    if not local_plugin_zip.is_file():
        raise FileNotFoundError("WordPress plugin ZIP is missing.")

    static_files = [path for path in local_static_dir.rglob("*") if path.is_file()]
    if args.dry_run:
        print(f"replace: {REMOTE_STATIC_DIR} ({len(static_files)} files)")
        print(f"upload: {REMOTE_PLUGIN_ZIP} ({local_plugin_zip.stat().st_size} bytes)")
        return 0

    host = os.environ.get("DO_SSH_HOST")
    username = os.environ.get("DO_SSH_USER")
    password = os.environ.get("DO_SSH_PASSWORD")
    if not host or not username:
        print("Set DO_SSH_HOST and DO_SSH_USER before running a live deploy.")
        return 2
    if username == "root" and os.environ.get("ALLOW_ROOT_DEPLOY", "").lower() not in ("true", "1", "yes"):
        print("Refusing root deploy by default. Use a limited deploy user or set ALLOW_ROOT_DEPLOY=true for a controlled migration window.")
        return 2
    if password and os.environ.get("ALLOW_SSH_PASSWORD_DEPLOY", "").lower() not in ("true", "1", "yes"):
        print("Refusing password-based SSH deploy by default. Use SSH keys or set ALLOW_SSH_PASSWORD_DEPLOY=true temporarily.")
        return 2

    ssh = paramiko.SSHClient()
    known_hosts_path = Path(os.environ.get("DO_SSH_KNOWN_HOSTS", str(Path.home() / ".ssh" / "known_hosts")))
    if not known_hosts_path.exists():
        print(f"Known-hosts file not found: {known_hosts_path}")
        return 2
    ssh.load_host_keys(str(known_hosts_path))
    ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
    ssh.connect(
        host,
        username=username,
        password=password,
        timeout=20,
        look_for_keys=True,
        allow_agent=True,
    )
    sftp = ssh.open_sftp()
    try:
        remove_remote_path(sftp, REMOTE_STATIC_DIR)
        ensure_remote_dir(sftp, REMOTE_STATIC_DIR)
        uploaded = upload_tree(sftp, local_static_dir, REMOTE_STATIC_DIR)
        ensure_remote_dir(sftp, posixpath.dirname(REMOTE_PLUGIN_ZIP))
        sftp.put(str(local_plugin_zip), REMOTE_PLUGIN_ZIP)
        sftp.chmod(REMOTE_PLUGIN_ZIP, 0o644)
    finally:
        sftp.close()
        ssh.close()

    print(f"uploaded: {uploaded} compiled portal files")
    print(f"uploaded: {REMOTE_PLUGIN_ZIP}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
