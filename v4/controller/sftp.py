"""WRE v4 SFTP file transfer.

Uses the OpenSSH SFTP client bundled with macOS via `sftp -b -` (batch mode
driven by stdin) — no paramiko dependency. Large file transfers happen here,
not inside the rpc-stdio channel.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path

from .targets import Target

_DRIVE_ABS = re.compile(r"^[A-Za-z]:/")


def _remote_norm(path: str) -> str:
    """Windows OpenSSH sftp resolves paths without a leading slash against the
    SSH user's home; drive-absolute paths must be sent as /C:/... form."""
    path = path.replace("\\", "/")
    if _DRIVE_ABS.match(path):
        return "/" + path
    return path


def put(target: Target, local_path: Path, remote_path: str) -> None:
    """Upload a single file."""
    remote_path = _remote_norm(remote_path)
    if not Path(local_path).is_file():
        raise FileNotFoundError(local_path)
    cmds = [
        f"put {shlex.quote(str(local_path))} {shlex.quote(remote_path)}",
        "bye",
    ]
    _run_sftp(target, cmds)


def put_dir(target: Target, local_dir: Path, remote_dir: str) -> None:
    remote_dir = _remote_norm(remote_dir)
    cmds = [
        f"-mkdir {shlex.quote(remote_dir)}",
        f"put -r {shlex.quote(str(local_dir))} {shlex.quote(remote_dir)}",
        "bye",
    ]
    _run_sftp(target, cmds)


def get(target: Target, remote_path: str, local_path: Path) -> None:
    remote_path = _remote_norm(remote_path)
    cmds = [f"get {shlex.quote(remote_path)} {shlex.quote(str(local_path))}", "bye"]
    _run_sftp(target, cmds)


def _run_sftp(target: Target, batch_commands: list[str]) -> None:
    batch = "\n".join(batch_commands) + "\n"
    args = [
        "sftp",
        "-P", str(target.port),
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-b", "-",
    ]
    if target.key:
        args += ["-i", target.key]
    args += [target.ssh_destination]
    completed = subprocess.run(args, input=batch, text=True, encoding="utf-8",
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"sftp failed: {completed.stderr.strip() or completed.stdout.strip()}")