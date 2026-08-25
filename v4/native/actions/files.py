"""Filesystem actions — pure stdlib pathlib/shutil.

v4-hardening backports (2026-08-25):
From v5:
- Protected-prefix guardrail on write/delete-class actions (see
  `_check_protected_path`). NOTE: this is a fat-finger guardrail, NOT a
  security boundary — any token holder can set `allowProtected: true`, and the
  documented trust model is "token == SYSTEM".
- read_text stats the file BEFORE reading so maxBytes takes effect without
  loading an oversized file into memory.
- write tmp names carry a uuid4 suffix (no same-path concurrent collision).
- _file_proof reuses already-in-memory bytes instead of re-reading the file;
  callers without bytes get a streamed (chunked) hash instead of a full read.
- file.search accepts `path` as an alias for `root` (root wins if both given).
From v6 (H5/H6):
- file.search caps FILES EXAMINED via `maxFiles` (default 2000) — a no-match
  regex used to read every file under the root (unbounded runtime). Response
  gains `filesExamined`; `truncated` also fires on the scan cap.
- file.list recursive caps DIRECTORIES VISITED via `maxScan` (default 20000);
  response gains `dirsScanned` (recursive only) and `truncated` is precise:
  walking exactly to the cap with nothing left is NOT truncated.
"""

from __future__ import annotations

import base64
import fnmatch
import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from typing import Any

# Directories whose contents must not be written/deleted via file.* actions
# without an explicit `allowProtected: true` in the payload:
#   - the WRE tree itself (rpc.py / access-policy.json / apply-tasks.json)
#   - the OpenSSH configuration (sshd_config, host keys)
#   - the OS directory
# Guardrail only: NOT a security boundary (see module docstring).
_PROTECTED_PREFIXES = (
    "C:/CodexRemote/wre",
    "C:/ProgramData/ssh",
    "C:/Windows",
)


def _norm(path: str) -> str:
    p = (path or "").strip()
    if not p:
        raise ValueError("path is required")
    # Accept forward slashes (WRE convention) and normalize to OS-native.
    return p.replace("/", os.sep)


def _canon_for_guard(path: str) -> str:
    """Canonical form for prefix matching: forward slashes, no trailing slash,
    case-folded (Windows paths are case-insensitive). Runs on the RAW payload
    path so it works identically on Windows and on a non-Windows loopback."""
    p = (path or "").strip().replace("\\", "/")
    if p.startswith("//?/"):
        p = p[4:]
    # collapse duplicate slashes and any "." / ".." segments
    parts: list[str] = []
    for seg in p.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
            continue
        parts.append(seg)
    return "/".join(parts).lower()


def _check_protected_path(path: str, payload: dict[str, Any]) -> None:
    """Reject write/delete-class operations under _PROTECTED_PREFIXES unless
    the payload explicitly carries `allowProtected: true`."""
    canon = _canon_for_guard(path)
    for prefix in _PROTECTED_PREFIXES:
        p = prefix.lower()
        if canon == p or canon.startswith(p + "/"):
            if payload.get("allowProtected") is True:
                return
            raise ValueError(
                f"path {path!r} is under protected prefix {prefix}; "
                "pass allowProtected=true to override (guardrail against "
                "operator mistakes, not a security boundary)"
            )


def _file_proof(path: str, content_bytes: bytes | None = None) -> dict[str, Any]:
    stat = os.stat(path)
    if content_bytes is not None:
        digest = hashlib.sha256(content_bytes).hexdigest()
    else:
        # Stream the hash instead of a second full read into memory.
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        digest = h.hexdigest()
    return {
        "path": path.replace("\\", "/"),
        "byteLength": stat.st_size,
        "sha256": digest,
        "lastWriteUtc": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
    }


def _tmp_path(path: str) -> str:
    return f"{path}.wre-tmp-{uuid.uuid4().hex}"


def write_text(payload: dict[str, Any]) -> dict[str, Any]:
    _check_protected_path(str(payload.get("path", "")), payload)
    path = _norm(payload.get("path", ""))
    text = payload.get("text", "")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    data = text.encode("utf-8")
    tmp = _tmp_path(path)
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
    return {
        "data": {"proof": _file_proof(path, data)},
        "stdout_text": "",
        "evidence_extra": ["file.writeText"],
    }


def read_text(payload: dict[str, Any]) -> dict[str, Any]:
    path = _norm(payload.get("path", ""))
    max_bytes = payload.get("maxBytes")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"file not found: {path}")
    # Enforce maxBytes from stat BEFORE reading the file into memory.
    if isinstance(max_bytes, int) and max_bytes > 0:
        size = os.stat(path).st_size
        if size > max_bytes:
            raise ValueError(f"file size {size} exceeds maxBytes {max_bytes}")
    raw = open(path, "rb").read()
    if isinstance(max_bytes, int) and max_bytes > 0 and len(raw) > max_bytes:
        # File grew between stat and read; still refuse.
        raise ValueError(f"file size {len(raw)} exceeds maxBytes {max_bytes}")
    return {
        "data": {
            "proof": _file_proof(path, raw),
            "text": raw.decode("utf-8", errors="replace"),
            "base64": base64.b64encode(raw).decode("ascii"),
            "bytes": len(raw),
        },
        "stdout_text": raw.decode("utf-8", errors="replace"),
        "evidence_extra": ["file.readText"],
    }


def mkdir(payload: dict[str, Any]) -> dict[str, Any]:
    _check_protected_path(str(payload.get("path", "")), payload)
    path = _norm(payload.get("path", ""))
    os.makedirs(path, exist_ok=True)
    return {
        "data": {"path": path.replace("\\", "/"), "exists": True},
        "stdout_text": "",
        "evidence_extra": ["file.mkdir"],
    }


def delete_tree(payload: dict[str, Any]) -> dict[str, Any]:
    _check_protected_path(str(payload.get("path", "")), payload)
    path = _norm(payload.get("path", ""))
    if not os.path.exists(path):
        return {
            "data": {"path": path.replace("\\", "/"), "existed": False, "deleted": False},
            "stdout_text": "",
            "evidence_extra": ["file.deleteTree:noop"],
        }
    if os.path.isfile(path):
        os.remove(path)
    else:
        shutil.rmtree(path, ignore_errors=False)
    return {
        "data": {"path": path.replace("\\", "/"), "existed": True, "deleted": True},
        "stdout_text": "",
        "evidence_extra": ["file.deleteTree"],
    }


def copy(payload: dict[str, Any]) -> dict[str, Any]:
    _check_protected_path(str(payload.get("destination", "")), payload)
    src = _norm(payload.get("source", ""))
    dst = _norm(payload.get("destination", ""))
    if not os.path.exists(src):
        raise FileNotFoundError(f"source not found: {src}")
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    shutil.copy2(src, dst)
    return {
        "data": {"proof": _file_proof(dst)},
        "stdout_text": "",
        "evidence_extra": ["file.copy"],
    }


def put_binary(payload: dict[str, Any]) -> dict[str, Any]:
    """Write base64-encoded bytes. Use SFTP for files larger than ~1 MB."""
    _check_protected_path(str(payload.get("path", "")), payload)
    path = _norm(payload.get("path", ""))
    b64 = payload.get("base64")
    if not b64:
        raise ValueError("base64 is required")
    data = base64.b64decode(b64)
    if len(data) > 4 * 1024 * 1024:
        raise ValueError("putBinary payload exceeds 4 MB; use SFTP via controller instead")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = _tmp_path(path)
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
    return {
        "data": {"proof": _file_proof(path, data)},
        "stdout_text": "",
        "evidence_extra": ["file.putBinary"],
    }


def _entry(path: str, name: str) -> dict[str, Any]:
    try:
        st = os.stat(path)
        is_dir = os.path.isdir(path)
        return {
            "name": name,
            "path": path.replace("\\", "/"),
            "type": "dir" if is_dir else "file",
            "byteLength": 0 if is_dir else st.st_size,
            "lastWriteUtc": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
        }
    except OSError:
        return {"name": name, "path": path.replace("\\", "/"), "type": "unknown"}


def list_dir(payload: dict[str, Any]) -> dict[str, Any]:
    root = _norm(payload.get("path", ""))
    pattern = payload.get("pattern") or "*"
    recursive = bool(payload.get("recursive", False))
    max_entries = int(payload.get("maxEntries") or 500)
    # A recursive walk with a pattern that matches nothing used to scan the
    # ENTIRE tree (C:/ -> minutes). Cap directories visited; `truncated`
    # tells the caller to narrow the root.
    max_scan = int(payload.get("maxScan") or 20_000)
    if not os.path.isdir(root):
        raise FileNotFoundError(f"directory not found: {root}")
    entries: list[dict[str, Any]] = []
    truncated = False
    scanned = 0
    if recursive:
        walk = os.walk(root)
        for dirpath, dirnames, filenames in walk:
            scanned += 1
            # Peek — if this is the LAST directory and we have not hit any
            # cap, the walk is complete and NOT truncated. A cap fires
            # (truncated=True) whenever more directories remained.
            if scanned > max_scan:
                truncated = True
                scanned = max_scan  # report the cap, not cap+1
                break
            if scanned == max_scan:
                # at the boundary: truncated only if another dir follows
                try:
                    next(walk)
                    truncated = True
                except StopIteration:
                    pass
            names = sorted(dirnames) + sorted(filenames)
            for name in names:
                if not fnmatch.fnmatch(name.lower(), pattern.lower()):
                    continue
                entries.append(_entry(os.path.join(dirpath, name), name))
                if len(entries) >= max_entries:
                    truncated = True
                    break
            if truncated:
                break
    else:
        for name in sorted(os.listdir(root)):
            if not fnmatch.fnmatch(name.lower(), pattern.lower()):
                continue
            entries.append(_entry(os.path.join(root, name), name))
            if len(entries) >= max_entries:
                truncated = True
                break
    return {
        "data": {"root": root.replace("\\", "/"), "entries": entries,
                 "count": len(entries), "truncated": truncated,
                 **({"dirsScanned": scanned} if recursive else {})},
        "stdout_text": "\n".join(e["path"] for e in entries),
        "evidence_extra": ["file.list"],
    }


def search(payload: dict[str, Any]) -> dict[str, Any]:
    # `path` accepted as an alias for `root` (consistency with file.list;
    # root wins when both are given).
    root = _norm(payload.get("root") or payload.get("path", ""))
    name_glob = payload.get("nameGlob")
    content_regex = payload.get("contentRegex")
    if not name_glob and not content_regex:
        raise ValueError("nameGlob and/or contentRegex is required")
    max_matches = int(payload.get("maxMatches") or 100)
    max_file_bytes = int(payload.get("maxFileBytes") or 1024 * 1024)
    # Cap FILES EXAMINED too — a no-match regex used to read every file under
    # the root (unbounded runtime). `truncated` now also fires on the scan
    # cap; narrow the root or raise maxFiles.
    max_files = int(payload.get("maxFiles") or 2000)
    if not os.path.isdir(root):
        raise FileNotFoundError(f"directory not found: {root}")
    rx = re.compile(content_regex) if content_regex else None
    matches: list[dict[str, Any]] = []
    truncated = False
    files_seen = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in sorted(filenames):
            files_seen += 1
            if files_seen > max_files:
                truncated = True
                break
            full = os.path.join(dirpath, name)
            if name_glob and fnmatch.fnmatch(name.lower(), name_glob.lower()):
                matches.append({"kind": "name", **_entry(full, name)})
            elif rx:
                try:
                    if os.path.getsize(full) > max_file_bytes:
                        continue
                    with open(full, "r", encoding="utf-8", errors="replace") as fh:
                        for lineno, line in enumerate(fh, 1):
                            if rx.search(line):
                                matches.append({
                                    "kind": "content",
                                    "path": full.replace("\\", "/"),
                                    "line": lineno,
                                    "text": line.strip()[:300],
                                })
                                break
                except OSError:
                    continue
            if len(matches) >= max_matches:
                truncated = True
                break
        if truncated:
            break
    return {
        "data": {"root": root.replace("\\", "/"), "matches": matches,
                 "count": len(matches), "truncated": truncated,
                 "filesExamined": min(files_seen, max_files)},
        "stdout_text": "\n".join(
            m["path"] if m["kind"] == "name" else f"{m['path']}:{m['line']}: {m['text']}"
            for m in matches),
        "evidence_extra": ["file.search"],
    }


REGISTRATIONS = (
    ("file.writeText", write_text),
    ("file.readText", read_text),
    ("file.mkdir", mkdir),
    ("file.deleteTree", delete_tree),
    ("file.copy", copy),
    ("file.putBinary", put_binary),
    ("file.list", list_dir),
    ("file.search", search),
)
