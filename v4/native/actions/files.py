"""Filesystem actions — pure stdlib pathlib/shutil."""

from __future__ import annotations

import base64
import fnmatch
import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from typing import Any


def _norm(path: str) -> str:
    p = (path or "").strip()
    if not p:
        raise ValueError("path is required")
    # Accept forward slashes (WRE convention) and normalize to OS-native.
    return p.replace("/", os.sep)


def _file_proof(path: str, content_bytes: bytes | None = None) -> dict[str, Any]:
    stat = os.stat(path)
    return {
        "path": path.replace("\\", "/"),
        "byteLength": stat.st_size,
        "sha256": hashlib.sha256(content_bytes if content_bytes is not None
                                else open(path, "rb").read()).hexdigest(),
        "lastWriteUtc": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
    }


def write_text(payload: dict[str, Any]) -> dict[str, Any]:
    path = _norm(payload.get("path", ""))
    text = payload.get("text", "")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    data = text.encode("utf-8")
    tmp = path + ".wre-tmp"
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
    raw = open(path, "rb").read()
    if isinstance(max_bytes, int) and max_bytes > 0 and len(raw) > max_bytes:
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
    path = _norm(payload.get("path", ""))
    os.makedirs(path, exist_ok=True)
    return {
        "data": {"path": path.replace("\\", "/"), "exists": True},
        "stdout_text": "",
        "evidence_extra": ["file.mkdir"],
    }


def delete_tree(payload: dict[str, Any]) -> dict[str, Any]:
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
    path = _norm(payload.get("path", ""))
    b64 = payload.get("base64")
    if not b64:
        raise ValueError("base64 is required")
    data = base64.b64decode(b64)
    if len(data) > 4 * 1024 * 1024:
        raise ValueError("putBinary payload exceeds 4 MB; use SFTP via controller instead")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".wre-tmp"
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
    if not os.path.isdir(root):
        raise FileNotFoundError(f"directory not found: {root}")
    entries: list[dict[str, Any]] = []
    truncated = False
    if recursive:
        for dirpath, dirnames, filenames in os.walk(root):
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
                 "count": len(entries), "truncated": truncated},
        "stdout_text": "\n".join(e["path"] for e in entries),
        "evidence_extra": ["file.list"],
    }


def search(payload: dict[str, Any]) -> dict[str, Any]:
    root = _norm(payload.get("root", ""))
    name_glob = payload.get("nameGlob")
    content_regex = payload.get("contentRegex")
    if not name_glob and not content_regex:
        raise ValueError("nameGlob and/or contentRegex is required")
    max_matches = int(payload.get("maxMatches") or 100)
    max_file_bytes = int(payload.get("maxFileBytes") or 1024 * 1024)
    if not os.path.isdir(root):
        raise FileNotFoundError(f"directory not found: {root}")
    rx = re.compile(content_regex) if content_regex else None
    matches: list[dict[str, Any]] = []
    truncated = False
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in sorted(filenames):
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
                 "count": len(matches), "truncated": truncated},
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