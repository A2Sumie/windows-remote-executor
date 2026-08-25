"""Read and rewrite Windows OpenSSH `sshd_config` without shell tools.

Layout on a default OpenSSH-on-Windows install:
  config:       C:/ProgramData/ssh/sshd_config
  admin keys:   C:/ProgramData/ssh/administrators_authorized_keys
  user keys:    <user>/.ssh/authorized_keys
Service name: sshd  (managed by service.py)

Pure stdlib text editing — no schtasks, no sc, no PowerShell. The active
listener enumeration uses iphlpapi GetExtendedTcpTable via stdlib ctypes
(Windows-only, executed lazily; off-Windows it reports "unknown").

v5 fixes (2026-08-18 v4 audit, finding A3):
- `_active_listen_addresses` is a REAL socket table read, not the v4
  getaddrinfo/gethostbyname_ex pseudo-enumeration.
- Default sshd config (no ListenAddress line = wildcard listener) now judges
  UNSAFE instead of the v4 false-negative "safe".
- Dead code removed: _SAFE_PRIVATE_PREFIXES, _loopback_or_private.
- Trailing-newline preservation in _rewrite_listen_block fixed (v4 had the
  condition inverted).
"""

from __future__ import annotations

import os
import shutil
import socket
import struct
from datetime import datetime, timezone
from typing import Any

_CONFIG_PATH = r"C:/ProgramData/ssh/sshd_config"
_WILDCARD_ADDRS = ("0.0.0.0", "::", "::0")
_LINK_LOCAL_PREFIXES = ("169.254.", "fe80:")


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
        # None means "enumeration unavailable (unknown)" — surfaced as [] with
        # an explicit flag so JSON consumers never mistake it for "no listeners".
        "activeListenAddresses": active if active is not None else [],
        "activeListenersKnown": active is not None,
        "exposureSafe": policy_ok,
        "reason": reason,
        "noDisable": no_disable,
    }
    if not policy_ok and not no_disable:
        _append_log(log_path, f"UNSAFE exposure detected: {reason}. noDisable=False — would disable sshd.")
        # We do not actually disable sshd here to avoid a remote-disconnect race;
        # host.repair handles service restart. Guard is read-only by default.
    return diagnostics


def _active_listen_addresses(port: int = 22) -> list[str] | None:
    """Real enumeration of local IPv4 TCP listeners on `port` via iphlpapi
    GetExtendedTcpTable (stdlib ctypes, no new dependency).

    Returns a sorted list of local addresses, or None when the enumeration is
    unavailable (non-Windows host, or iphlpapi failure) — callers must treat
    None as "unknown", never as "safe".
    """
    if os.name != "nt":
        return None
    try:
        return _active_listen_addresses_win32(port)
    except Exception:  # noqa: BLE001
        return None


def _active_listen_addresses_win32(port: int) -> list[str]:
    import ctypes
    from ctypes import wintypes

    AF_INET = 2
    MIB_TCP_STATE_LISTEN = 2
    TCP_TABLE_OWNER_PID_LISTENER = 5
    ERROR_INSUFFICIENT_BUFFER = 122

    class MIB_TCPROW_OWNER_PID(ctypes.Structure):
        _fields_ = [
            ("dwState", wintypes.DWORD),
            ("dwLocalAddr", wintypes.DWORD),
            ("dwLocalPort", wintypes.DWORD),
            ("dwRemoteAddr", wintypes.DWORD),
            ("dwRemotePort", wintypes.DWORD),
            ("dwOwningPid", wintypes.DWORD),
        ]

    iphlpapi = ctypes.windll.iphlpapi  # type: ignore[attr-defined]
    size = wintypes.DWORD(0)
    rc = iphlpapi.GetExtendedTcpTable(
        None, ctypes.byref(size), False, AF_INET, TCP_TABLE_OWNER_PID_LISTENER, 0
    )
    if rc != ERROR_INSUFFICIENT_BUFFER or size.value == 0:
        raise OSError(f"GetExtendedTcpTable sizing call failed, rc={rc}")
    buf = (ctypes.c_byte * size.value)()
    rc = iphlpapi.GetExtendedTcpTable(
        buf, ctypes.byref(size), False, AF_INET, TCP_TABLE_OWNER_PID_LISTENER, 0
    )
    if rc != 0:
        raise OSError(f"GetExtendedTcpTable failed, rc={rc}")

    num_rows = wintypes.DWORD.from_buffer(buf, 0).value
    row_size = ctypes.sizeof(MIB_TCPROW_OWNER_PID)
    addrs: set[str] = set()
    for i in range(num_rows):
        row = MIB_TCPROW_OWNER_PID.from_buffer(buf, 4 + i * row_size)
        if row.dwState != MIB_TCP_STATE_LISTEN:
            continue
        # dwLocalPort holds the 16-bit port in network byte order in its low
        # half-word; dwLocalAddr is the IPv4 address in network byte order.
        local_port = socket.ntohs(row.dwLocalPort & 0xFFFF)
        if local_port != port:
            continue
        addrs.add(socket.inet_ntoa(struct.pack("=I", row.dwLocalAddr)))
    return sorted(addrs)


def _policy_check(
    expected: str | None,
    configured: list[str],
    active: list[str] | None,
) -> tuple[bool, str]:
    # Wildcard / link-local exposure is unsafe wherever it appears.
    for a in configured + (active or []):
        if a in _WILDCARD_ADDRS:
            return False, f"wildcard listener present: {a}"
        if any(a.lower().startswith(p) for p in _LINK_LOCAL_PREFIXES):
            return False, f"link-local listener present: {a}"
    # No ListenAddress line means sshd's default: wildcard bind on all
    # interfaces. v4 treated that as safe when the pseudo-enumeration returned
    # anything; that was a false negative. It is UNSAFE now.
    if not configured:
        return False, "no ListenAddress configured; sshd defaults to wildcard"
    if expected and not any(a == expected for a in configured):
        return False, f"configured listen {configured} does not include expected {expected}"
    if active is None:
        return True, ("active listener enumeration unknown (non-Windows host or "
                      "iphlpapi failure); verdict based on configured ListenAddress only")
    return True, ""


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
            "# BEGIN WRE-MANAGED: sshd listen address (v5)",
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
    # Preserve the original trailing-newline convention (v4 had this inverted).
    trailing = "\n" if text.endswith(("\n", "\r")) else ""
    return "\n".join(out) + trailing


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
