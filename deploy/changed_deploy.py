"""Deploy only changed tracked files to the DigitalOcean app server.

Usage:
    set DO_SSH_PASSWORD=...
    python deploy/changed_deploy.py --base origin/main

Environment:
    DO_SSH_HOST       SSH host, defaults to 159.223.59.78
    DO_SSH_USER       SSH user, defaults to root
    DO_SSH_PASSWORD   Optional SSH password; omit to use local SSH key/agent auth
    DO_REMOTE_DIR     Remote project directory, defaults to /var/www/buykori-adsync
"""

from __future__ import annotations

import argparse
import os
import posixpath
import stat
import subprocess
from pathlib import Path

import paramiko


DEFAULT_REMOTE_DIR = "/var/www/buykori-adsync"
EXCLUDED_PREFIXES = (
    ".git/",
    "client-portal/",
    "admin-portal/",
    "scratch/",
)
EXCLUDED_FILES = {
    ".env",
    "buykori-adsync-updated.zip",
}


def run_git(args: list[str]) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def changed_files(base: str) -> list[tuple[str, str]]:
    output = run_git(["diff", "--name-status", f"{base}..HEAD"])
    changes: list[tuple[str, str]] = []
    if not output:
        return changes

    for line in output.splitlines():
        parts = line.split("\t")
        status_code = parts[0]
        status = status_code[0]
        path = parts[-1]
        if should_skip(path):
            continue
        changes.append((status, path))
    return changes


def should_skip(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return (
        normalized in EXCLUDED_FILES
        or normalized.endswith(".pyc")
        or any(normalized.startswith(prefix) for prefix in EXCLUDED_PREFIXES)
    )


def ensure_remote_dir(sftp: paramiko.SFTPClient, remote_dir: str) -> None:
    current = ""
    for part in remote_dir.strip("/").split("/"):
        current += f"/{part}"
        try:
            sftp.mkdir(current)
        except OSError:
            pass


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


def upload_file(sftp: paramiko.SFTPClient, local_root: Path, remote_root: str, rel_path: str) -> None:
    local_path = local_root / rel_path
    if not local_path.is_file():
        return
    remote_path = posixpath.join(remote_root, rel_path.replace("\\", "/"))
    ensure_remote_dir(sftp, posixpath.dirname(remote_path))
    sftp.put(str(local_path), remote_path)
    print(f"uploaded {rel_path}")


def run_remote(ssh: paramiko.SSHClient, command: str) -> int:
    stdin, stdout, stderr = ssh.exec_command(command)
    for line in stdout:
        print(line, end="")
    err = stderr.read().decode("utf-8", errors="replace").strip()
    if err:
        print(err)
    return stdout.channel.recv_exit_status()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="HEAD~1", help="Git ref to diff against")
    parser.add_argument("--skip-migrations", action="store_true")
    parser.add_argument("--skip-restart", action="store_true")
    args = parser.parse_args()

    local_root = Path(run_git(["rev-parse", "--show-toplevel"]))
    remote_root = os.environ.get("DO_REMOTE_DIR", DEFAULT_REMOTE_DIR)
    host = os.environ.get("DO_SSH_HOST", "159.223.59.78")
    username = os.environ.get("DO_SSH_USER", "root")
    password = os.environ.get("DO_SSH_PASSWORD")

    changes = changed_files(args.base)
    if not changes:
        print("No deployable tracked file changes found.")
        return 0

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
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
        for status_code, rel_path in changes:
            remote_path = posixpath.join(remote_root, rel_path.replace("\\", "/"))
            if status_code == "D":
                remove_remote_path(sftp, remote_path)
                print(f"deleted {rel_path}")
            else:
                upload_file(sftp, local_root, remote_root, rel_path)
    finally:
        sftp.close()

    commands = [f"cd {remote_root}"]
    if not args.skip_migrations:
        commands.append("./venv/bin/alembic upgrade head")
    if not args.skip_restart:
        commands.append("sudo supervisorctl restart buykori-web buykori-worker:*")
        commands.append("sudo supervisorctl status")

    if len(commands) > 1:
        exit_code = run_remote(ssh, " && ".join(commands))
    else:
        exit_code = 0
    ssh.close()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
