"""Read and rewrite Windows OpenSSH `sshd_config` without shell tools.

Layout on a default OpenSSH-on-Windows install:
  config:       C:/ProgramData/ssh/sshd_config
  admin keys:   C:/ProgramData/ssh/administrators_authorized_keys
  user keys:    <user>/.ssh/authorized_keys
Service name: sshd  (managed by service.py)

Pure stdlib text editing — no schtasks, no sc, no PowerShell.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
from datetime import datetime, timezone
from typing import Any

_CONFIG_PATH = r"C:/ProgramData/ssh/sshd_config"
_SAFE_PRIVATE_PREFIXES = ("10.", "100.64.", "100.65.", "100.66.", "100.67.",
                          "100.68.", "100.69.", "100.70.", "100.71.", "100.72.",
                          "100.73.", "100.74.", "100.75.", "100.76.", "100.77.",
                          "100.78.", "100.79.", "100.80.", "100.81.", "100.82.",
                          "100.83.", "100.84.", "100.85.", "100.86.", "100.87.",
                          "100.88.", "100.89.", "100.90.", "100.91.", "100.92.",
                          "100.93.", "100.94.", "100.95.", "100.96.", "100.97.",
                          "100.98.", "100.99.", "100.10", "100.11", "100.12",
                          "192.168.")
_LOOPBACK = ("127.", "::1")
_LINK_LOCAL = ("169.254.", "fe80:")


def read_listen_addresses() -> list[str]:
    """Return the explicit `ListenAddress` lines in `sshd_config` (IPv4 only)."""
    if not os.path.isfile(_CONFIG_PATH):
        return []
    addrs: list[str] = []
    seen: set[str] = set()
    with open(_CONFIG_PATH, "r", encoding="utf-8-sig", errors="replace") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 2 and parts[0].lower() in ("listenaddress", "listen"):
                addr = parts[1].split(":")[0]
                if addr and addr not in seen:
                    seen.add(addr)
                    addrs.append(addr)
    return addrs


def read_full_config() -> str:
    if not os.path.isfile(_CONFIG_PATH):
        return ""
    with open(_CONFIG_PATH, "r", encoding="utf-8-sig", errors="replace") as fh:
        return fh.read()


def rewrite_config(*, expected_listen: str | None, force: bool, log_path: str) -> dict[str, Any]:
    """Rewrite sshd_config's ListenAddress block to `expected_listen`.

    If `expected_listen` is empty/None, this is read-only and only returns diagnostics.
    Returns a dict with `before`, `after`, `rewritten`, `backupPath`.
    """
    before = read_listen_addresses()
    backup_path: str | None = None
    rewritten = False

    if not expected_listen:
        return {"before": before, "after": before, "rewritten": False, "backupPath": None}

    if not os.path.isfile(_CONFIG_PATH):
        raise FileNotFoundError(f"sshd_config not found at {_CONFIG_PATH}")

    original = read_full_config()
    fixed = _rewrite_listen_block(original, expected_listen)
    if fixed != original or force:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_dir = os.path.join(os.path.dirname(_CONFIG_PATH), "wre-backups")
        os.makedirs(backup_dir, exist_ok=True)
        backup_path = os.path.join(backup_dir, f"sshd_config.{ts}.bak")
        shutil.copy2(_CONFIG_PATH, backup_path)
        _atomic_write(_CONFIG_PATH, fixed)
        _append_log(log_path, f"sshd_config rewritten (ListenAddress={expected_listen}); backup={backup_path}")
        rewritten = True
    after = read_listen_addresses()
    return {"before": before, "after": after, "rewritten": rewritten, "backupPath": backup_path}


def evaluate_exposure(*, expected_listen: str | None, no_disable: bool, log_path: str) -> dict[str, Any]:
    configured = read_listen_addresses()
    active = _active_listen_addresses()
    policy_ok, reason = _policy_check(expected_listen, configured, active)
    diagnostics: dict[str, Any] = {
        "expectedListenAddress": expected_listen or "",
        "configuredListenAddresses": configured,
        "activeListenAddresses": active,
        "exposureSafe": policy_ok,
        "reason": reason,
        "noDisable": no_disable,
    }
    if not policy_ok and not no_disable:
        _append_log(log_path, f"UNSAFE exposure detected: {reason}. noDisable=False — would disable sshd.")
        # We do not actually disable sshd here to avoid a remote-disconnect race;
        # host.repair handles service restart. Guard is read-only by default.
    return diagnostics


def _active_listen_addresses() -> list[str]:
    seen: set[str] = set()
    try:
        for info in socket.getaddrinfo(None, 22, socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP):
            seen.add(info[4][0])
    except OSError:
        pass
    addrs = sorted(seen)
    return [_normalize_listen(a) for a in addrs]


def _normalize_listen(addr: str) -> str:
    return addr


def _policy_check(expected: str | None, configured: list[str], active: list[str]) -> tuple[bool, str]:
    # Wildcard exposure is unsafe.
    for a in configured + active:
        if a in ("0.0.0.0", "::", "::0"):
            return False, f"wildcard listener present: {a}"
        if a.startswith("169.254.") or a.lower().startswith("fe80:"):
            return False, f"link-local listener present: {a}"
    if expected:
        def matches_expected(addr: str) -> bool:
            return addr == expected or (expected in _loopback_or_private(addr, expected))
        if configured and not any(matches_expected(a) for a in configured):
            return False, f"configured listen {configured} does not include expected {expected}"
    if not configured and not active:
        return False, "no listen addresses configured"
    return True, ""


def _loopback_or_private(addr: str, expected: str) -> bool:
    return False


def _rewrite_listen_block(text: str, expected_listen: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    found = False
    in_match = False
    for raw in lines:
        line = raw.strip()
        low = line.lower()
        if line and not line.startswith("#") and low.split()[0] == "match":
            in_match = True
        if (line and not line.startswith("#")
                and line.split()[0].lower() in ("listenaddress", "listen")
                and not in_match):
            if not found:
                out.append(f"ListenAddress {expected_listen}")
                found = True
            continue
        out.append(raw)
    if not found:
        managed = [
            "# BEGIN WRE-MANAGED: sshd listen address (v4)",
            f"ListenAddress {expected_listen}",
            "# END WRE-MANAGED",
        ]
        insert_at = None
        for idx, raw in enumerate(out):
            s = raw.strip()
            if s and not s.startswith("#") and s.lower().split()[0] == "match":
                insert_at = idx
                break
        if insert_at is None:
            if out and out[-1].strip():
                out.append("")
            out.extend(managed)
        else:
            block = list(managed)
            if insert_at > 0 and out[insert_at - 1].strip():
                block = [""] + block
            block = block + [""]
            out[insert_at:insert_at] = block
    return "\n".join(out) + ("\n" if not text.endswith("\n") and not text.endswith("\r") else "")


def _atomic_write(path: str, content: str) -> None:
    tmp = path + ".wre-tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)
    os.replace(tmp, path)


def _append_log(log_path: str, message: str) -> None:
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(f"{datetime.now(timezone.utc).isoformat()} {message}\n")
    except OSError:
        pass