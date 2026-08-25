"""Target model for WRE v4 controller.

Loads a `.env` file describing one Windows host:
    TARGET_NAME=NANDTUS
    TARGET_HOST=100.111.107.14
    TARGET_USER=OsuLab
    TARGET_PORT=22
    TARGET_KEY=/Users/you/.ssh/id_ed25519
    TARGET_EXPECTED_LISTEN_ADDRESS=100.111.107.14
    TARGET_EXPOSURE_MODE=private-only
    TARGET_ACCESS_TOKEN=replace-with-random-token
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TARGETS_DIR = Path(__file__).resolve().parent.parent.parent / "windows-remote-executor" / "targets"


@dataclass
class Target:
    name: str
    env_file: Path
    host: str
    user: str
    port: str
    key: str | None
    access_token: str | None
    expected_listen_address: str | None
    exposure_mode: str

    @property
    def ssh_destination(self) -> str:
        return f"{self.user}@{self.host}"

    @property
    def ssh_args(self) -> list[str]:
        args = [
            "ssh",
            "-p", str(self.port),
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=15",
            "-o", "ServerAliveInterval=20",
            "-o", "ServerAliveCountMax=3",
        ]
        if self.key:
            args += ["-i", self.key]
        return args


def load_target(raw: str) -> Target:
    env_file = Path(raw)
    if not env_file.is_file():
        env_file = TARGETS_DIR / f"{raw}.env"
    if not env_file.is_file():
        raise FileNotFoundError(f"target env not found: {env_file}")
    values = parse_env_file(env_file)
    host = _require(values, "TARGET_HOST", env_file)
    user = _require(values, "TARGET_USER", env_file)
    return Target(
        name=values.get("TARGET_NAME", raw),
        env_file=env_file,
        host=host,
        user=user,
        port=values.get("TARGET_PORT", "22"),
        key=_maybe(values.get("TARGET_KEY")),
        access_token=_maybe(values.get("TARGET_ACCESS_TOKEN")),
        expected_listen_address=_maybe(values.get("TARGET_EXPECTED_LISTEN_ADDRESS")),
        exposure_mode=values.get("TARGET_EXPOSURE_MODE", "private-only"),
    )


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        try:
            parts = shlex.split(line, posix=True)
        except ValueError:
            continue
        if len(parts) != 1 or "=" not in parts[0]:
            continue
        key, value = parts[0].split("=", 1)
        values[key] = value
    return values


def _require(values: dict[str, str], key: str, path: Path) -> str:
    v = values.get(key)
    if not v:
        raise RuntimeError(f"{key} is required in {path}")
    return v


def _maybe(value: str | None) -> str | None:
    if not value:
        return None
    return value


def list_targets() -> list[str]:
    if not TARGETS_DIR.is_dir():
        return []
    return sorted(p.stem for p in TARGETS_DIR.glob("*.env") if p.stem != "example")