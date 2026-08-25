"""Filesystem actions — pure stdlib pathlib/shutil.

v6 additions (on top of the v5 notes below):
- file.readText gains `offset` / `tail` (BYTE semantics — the log-watching
  pair); `base64` and `proof` are opt-in (includeBase64 / includeProof) so a
  routine read no longer ships the payload twice. With offset/tail the proof
  defaults off (hashing a 500 MB log to prove a 4 KB tail is a bad trade).
- file.putBinary raises the cap 4 MB -> 32 MB and supports chunked upload
  ({chunkIndex, totalChunks}): chunks append to `<path>.wre-part` and the
  last chunk atomically renames to the target. Larger still goes via SFTP.
- _norm preserves `//wsl.localhost/<distro>/...` UNC paths verbatim; failures
  on that prefix get a "VM not running / distro exists?" hint instead of a
  bare OSError. writeText/putBinary escalate further (BUG-A): atomic
  tmp+replace -> direct write -> wsl.exe stdin transport (base64 -d > file +
  sha256sum verification), because the wsl.localhost 9P redirector is
  session-dependent and flaky while wsl.exe's RPC path is stable.
- Protected set = hardcoded C:/WRE/wre (the default v6 tree) + hardcoded
  C:/CodexRemote/wre (LEGACY: the live v4/v5 fleet tree, protected forever —
  kept after the 2026-08-19 rebrand on purpose) + the running rpc.py's own
  tree (dynamic — a sidecar wre6 protects itself) + C:/ProgramData/ssh +
  C:/Windows + WRE_ROOT/jobs/*.json metadata.

v5 notes (kept):
- Protected-prefix guardrail on write/delete-class actions. NOTE: this is a
  fat-finger guardrail, NOT a security boundary — any token holder can set
  `allowProtected: true`, and the documented trust model is "token == SYSTEM".
- read_text stats the file BEFORE reading so maxBytes takes effect without
  loading an oversized file into memory.
- write tmp names carry a uuid4 suffix (no same-path concurrent collision).
- _file_proof reuses already-in-memory bytes instead of re-reading the file;
  callers without bytes get a streamed (chunked) hash instead of a full read.
"""

from __future__ import annotations

import base64
import fnmatch
import hashlib
import json
import os
import re
import shutil
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_THIS_DIR))
from version import WRE_ROOT, WRE_TREE, JOBS_DIR  # noqa: E402

_WSL_UNC_PREFIX = "//wsl.localhost/"

# Guardrail prefixes (see module docstring for the threat-model honesty note).
_PROTECTED_PREFIXES = (
    "C:/WRE/wre",          # hardcoded: the default v6 tree, even from a sidecar
    "C:/CodexRemote/wre",  # hardcoded LEGACY: the retired v4/v5 fleet tree —
                           # cold standby since the 2026-08-25 task migration
                           # (zero enabled tasks reference it; kept for
                           # emergency rollback only)
    WRE_TREE,              # dynamic: the running rpc.py's own tree (wre/wre6/...)
    "C:/ProgramData/ssh",
    "C:/Windows",
)


def _norm(path: str) -> str:
    p = (path or "").strip()
    if not p:
        raise ValueError("path is required")
    # //wsl.localhost/<distro>/... (and any other UNC) passes through verbatim:
    # Windows APIs accept forward-slash UNC as-is, and normalizing would risk
    # collapsing the leading double slash.
    if p.startswith("//"):
        return p
    # Accept forward slashes (WRE convention) and normalize to OS-native.
    return p.replace("/", os.sep)


def _wsl_hint(path: str, exc: OSError) -> ValueError:
    return ValueError(
        f"cannot access {path!r}: {exc}. If this is a //wsl.localhost/... path, "
        "the WSL VM may be stopped (run wsl.status to warm it, or wsl.run first) "
        "or the distro name may not exist (check wsl.list)"
    )


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
    """Reject write/delete-class operations under _PROTECTED_PREFIXES (or on
    WRE_ROOT/jobs/*.json job metadata) unless the payload explicitly carries
    `allowProtected: true`."""
    canon = _canon_for_guard(path)
    jobs_canon = _canon_for_guard(JOBS_DIR)
    if (canon.startswith(jobs_canon + "/") and canon.endswith(".json")
            and payload.get("allowProtected") is not True):
        raise ValueError(
            f"path {path!r} is job metadata under {JOBS_DIR}; pass "
            "allowProtected=true to override (job-table integrity guard)"
        )
    for prefix in _PROTECTED_PREFIXES:
        p = _canon_for_guard(prefix)
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


def _write_bytes(path: str, data: bytes) -> None:
    """Atomic tmp+replace write. UNC targets fall back to a direct write when
    the atomic dance fails: the //wsl.localhost/<distro>/... 9P share rejects
    MoveFileEx-style replace (observed on X570 as WinError 67 while reads and
    wsl.run worked — BUG-A). Atomicity is best-effort on UNC; local-path
    failures keep raising (atomicity matters there)."""
    tmp = _tmp_path(path)
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
        return
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        if not path.startswith("//"):
            raise
    with open(path, "wb") as fh:  # UNC fallback: direct, non-atomic
        fh.write(data)


def _replace_or_copy(src: str, dst: str) -> None:
    """os.replace with the same UNC fallback as _write_bytes (used by the
    chunked putBinary final rename, where the staging .wre-part file already
    holds the full payload)."""
    try:
        os.replace(src, dst)
    except OSError:
        if not dst.startswith("//"):
            raise
        shutil.copyfile(src, dst)
        os.remove(src)


def _write_or_hint(raw_path: str, path: str, data: bytes) -> dict[str, Any]:
    """makedirs + _write_bytes, with two escalating fallbacks for
    //wsl.localhost/... targets (BUG-A) and the readText-style hint on
    terminal failure. Returns the file proof.

    The wsl.localhost 9P UNC redirector is session-dependent and flaky on
    Windows (observed on X570: read/write both ways at 12:40, write-only
    WinError 67 at 12:48, full provider loss at 14:45 — same ssh route, VM
    running throughout). wsl.exe itself talks to LxssManager over RPC and is
    stable from the ssh session, so it is the fallback transport."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        _write_bytes(path, data)
        return _file_proof(path, data)
    except OSError as exc:
        wsl_ref = _parse_wsl_unc(raw_path)
        if wsl_ref is None:
            raise
        try:
            return _wsl_mediated_write(wsl_ref[0], wsl_ref[1], data)
        except Exception as med_exc:  # noqa: BLE001
            raise ValueError(
                f"cannot access {raw_path!r}: UNC failed ({exc}) and the "
                f"WSL-mediated write failed too ({med_exc}). Check wsl.list for "
                "the distro name and wsl.status for VM state"
            ) from exc


_WSL_UNC_HOSTS = ("wsl.localhost", "wsl$")


def _is_wsl_unc(raw_path: str) -> bool:
    p = (raw_path or "").strip().replace("\\", "/").lower()
    return any(p.startswith(f"//{host}/") for host in _WSL_UNC_HOSTS)


def _parse_wsl_unc(raw_path: str) -> tuple[str, str] | None:
    """'//wsl.localhost/<distro>/<posix path>' (either slash style) ->
    (distro, posix_path). None when the path is not a WSL UNC."""
    p = (raw_path or "").strip().replace("\\", "/")
    for host in _WSL_UNC_HOSTS:
        prefix = f"//{host}/"
        if p.lower().startswith(prefix):
            rest = p[len(prefix):]
            distro, sep, posix = rest.partition("/")
            if distro and sep and posix:
                return distro, "/" + posix
            return None
    return None


def _wsl_run_capture(argv: list[str], stdin_text: str | None) -> dict[str, Any]:
    """Lazy bridge into the wsl/process layer (import cycle guard + the
    WRE_WSL_EXE mock hook comes along for free)."""
    from actions import wsl as _wsl  # noqa: PLC0415
    from actions import process as _proc  # noqa: PLC0415
    exe = _wsl._ensure_wsl()
    env = dict(os.environ)
    env["WSL_UTF8"] = "1"
    return _proc.run_capture(exe, argv, cwd=None, stdin_text=stdin_text,
                             env=env, timeout_ms=60_000, capture_kb=64)


def _wsl_mediated_write(distro: str, posix_path: str, data: bytes) -> dict[str, Any]:
    """Write `data` to <distro>:posix_path through wsl.exe stdin
    (base64 -d > file), then verify with sha256sum on the Linux side.
    Raises RuntimeError with the wsl.exe stderr snippet on any failure."""
    b64 = base64.b64encode(data).decode("ascii")
    parent = posix_path.rsplit("/", 1)[0] or "/"
    r = _wsl_run_capture(["-d", distro, "--exec", "/bin/mkdir", "-p", parent], None)
    if r["exitCode"] != 0:
        raise RuntimeError(f"mkdir -p failed (exit {r['exitCode']}): "
                           f"{(r['stdout'] + r['stderr']).strip()[:200]}")
    # "$1" positional quoting: the path travels as its own argv element, so
    # spaces/quotes/non-ASCII cannot inject into the shell snippet.
    r = _wsl_run_capture(
        ["-d", distro, "--exec", "/bin/bash", "-c",
         "base64 -d > \"$1\"", "_", posix_path],
        b64)
    if r["exitCode"] != 0:
        raise RuntimeError(f"stdin write failed (exit {r['exitCode']}): "
                           f"{(r['stdout'] + r['stderr']).strip()[:200]}")
    r = _wsl_run_capture(["-d", distro, "--exec", "/usr/bin/sha256sum", posix_path], None)
    local_digest = hashlib.sha256(data).hexdigest()
    remote_digest = (r["stdout"].split() or [""])[0] if r["exitCode"] == 0 else ""
    proof: dict[str, Any] = {
        "path": f"//wsl.localhost/{distro}{posix_path}",
        "byteLength": len(data),
        "sha256": remote_digest or local_digest,
        "lastWriteUtc": datetime.now(timezone.utc).isoformat(),
        "transport": "wsl-stdin",
    }
    if remote_digest:
        proof["verifiedAgainst"] = "sha256sum on target"
        if remote_digest != local_digest:
            raise RuntimeError(
                f"post-write verification failed: target sha256 {remote_digest} "
                f"!= payload sha256 {local_digest}")
    return proof


def write_text(payload: dict[str, Any]) -> dict[str, Any]:
    _check_protected_path(str(payload.get("path", "")), payload)
    raw_path = str(payload.get("path", ""))
    path = _norm(raw_path)
    text = payload.get("text", "")
    data = text.encode("utf-8")
    proof = _write_or_hint(raw_path, path, data)
    return {
        "data": {"proof": proof},
        "stdout_text": "",
        "evidence_extra": ["file.writeText"],
    }


def read_text(payload: dict[str, Any]) -> dict[str, Any]:
    raw_path = str(payload.get("path", ""))
    path = _norm(raw_path)
    max_bytes = payload.get("maxBytes")
    offset = payload.get("offset")
    tail = payload.get("tail")
    include_b64 = bool(payload.get("includeBase64"))
    include_proof = bool(payload.get("includeProof"))
    try:
        if not os.path.isfile(path):
            raise FileNotFoundError(f"file not found: {raw_path}")
        size = os.stat(path).st_size
        if offset is not None or tail is not None:
            # Partial read: offset = start byte, tail = last N bytes (they
            # compose: offset applies first, then tail within the remainder
            # would be odd — declare them mutually exclusive instead).
            if offset is not None and tail is not None:
                raise ValueError("offset and tail are mutually exclusive")
            with open(path, "rb") as fh:
                if offset is not None:
                    off = max(0, int(offset))
                    fh.seek(min(off, size))
                    raw = fh.read(max_bytes if isinstance(max_bytes, int) and max_bytes > 0 else -1)
                else:
                    n = max(0, int(tail))
                    fh.seek(max(0, size - n))
                    raw = fh.read()
        else:
            # Enforce maxBytes from stat BEFORE reading the file into memory.
            if isinstance(max_bytes, int) and max_bytes > 0 and size > max_bytes:
                raise ValueError(f"file size {size} exceeds maxBytes {max_bytes}")
            raw = open(path, "rb").read()
            if isinstance(max_bytes, int) and max_bytes > 0 and len(raw) > max_bytes:
                # File grew between stat and read; still refuse.
                raise ValueError(f"file size {len(raw)} exceeds maxBytes {max_bytes}")
    except OSError as exc:
        if _is_wsl_unc(raw_path):
            raise _wsl_hint(raw_path, exc) from exc
        raise
    text = raw.decode("utf-8", errors="replace")
    data: dict[str, Any] = {
        "text": text,
        "bytes": len(raw),
        "fileBytes": size,
    }
    if offset is not None:
        data["offset"] = max(0, int(offset))
        data["nextOffset"] = min(size, max(0, int(offset)) + len(raw))
    if tail is not None:
        data["tailBytes"] = len(raw)
    # proof: full-file hash. On by default for whole-file reads, off for
    # partial reads (hashing a huge log for a 4 KB tail is a bad trade);
    # includeProof forces it either way.
    if include_proof or (offset is None and tail is None):
        data["proof"] = _file_proof(path, raw if len(raw) == size else None)
    if include_b64:
        data["base64"] = base64.b64encode(raw).decode("ascii")
    return {
        "data": data,
        "stdout_text": text,
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


_PUTBINARY_MAX_BYTES = 32 * 1024 * 1024


def put_binary(payload: dict[str, Any]) -> dict[str, Any]:
    """Write base64-encoded bytes. <=32 MB decoded per file; chunked upload via
    {chunkIndex, totalChunks} (chunks append to `<path>.wre-part`, the last
    chunk atomically renames to the target). Larger files go via SFTP."""
    _check_protected_path(str(payload.get("path", "")), payload)
    raw_path = str(payload.get("path", ""))
    path = _norm(raw_path)
    b64 = payload.get("base64")
    if not b64:
        raise ValueError("base64 is required")
    data = base64.b64decode(b64)

    chunk_index = payload.get("chunkIndex")
    total_chunks = payload.get("totalChunks")
    if chunk_index is None and total_chunks is None:
        if len(data) > _PUTBINARY_MAX_BYTES:
            raise ValueError(
                f"putBinary payload exceeds {_PUTBINARY_MAX_BYTES // (1024 * 1024)} MB; "
                "use chunkIndex/totalChunks or SFTP via controller instead"
            )
        proof = _write_or_hint(raw_path, path, data)
        return {
            "data": {"proof": proof},
            "stdout_text": "",
            "evidence_extra": ["file.putBinary"],
        }

    # Chunked path.
    if not isinstance(chunk_index, int) or not isinstance(total_chunks, int):
        raise ValueError("chunkIndex and totalChunks must both be integers")
    if total_chunks < 1 or not (0 <= chunk_index < total_chunks):
        raise ValueError(f"invalid chunkIndex {chunk_index} / totalChunks {total_chunks}")
    part = f"{path}.wre-part"
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        mode = "wb" if chunk_index == 0 else "ab"
        if chunk_index > 0 and not os.path.isfile(part):
            raise ValueError(
                f"chunk {chunk_index} arrived but {part} is missing; upload chunks in order starting at 0"
            )
        with open(part, mode) as fh:
            fh.write(data)
        part_size = os.path.getsize(part)
        if part_size > _PUTBINARY_MAX_BYTES:
            os.remove(part)
            raise ValueError("chunked upload exceeded the 32 MB cap; staging file removed")
        done = chunk_index == total_chunks - 1
        if done:
            _replace_or_copy(part, path)
    except OSError as exc:
        if _is_wsl_unc(raw_path):
            raise _wsl_hint(raw_path, exc) from exc
        raise
    return {
        "data": {
            "receivedChunks": chunk_index + 1,
            "totalChunks": total_chunks,
            "receivedBytes": part_size,
            "complete": done,
            **({"proof": _file_proof(path)} if done else {}),
        },
        "stdout_text": "",
        "evidence_extra": ["file.putBinary:chunk" if not done else "file.putBinary"],
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
    # H6: a recursive walk with a pattern that matches nothing used to scan
    # the ENTIRE tree (C:/ -> minutes). Cap directories visited; `truncated`
    # tells the caller to narrow the root.
    max_scan = int(payload.get("maxScan") or 20_000)
    entries: list[dict[str, Any]] = []
    truncated = False
    scanned = 0
    if recursive:
        walk = os.walk(root)
        for dirpath, dirnames, filenames in walk:
            scanned += 1
            # H6: peek — if this is the LAST directory and we have not hit
            # any cap, the walk is complete and NOT truncated. A cap fires
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
    # Accept `path` as an alias for `root` (2026-08-25 review: every other
    # file.* action incl. file.list takes `path`; root-only here was an
    # inconsistency that made habitual calls fail with "path is required").
    root = _norm(payload.get("root") or payload.get("path", ""))
    name_glob = payload.get("nameGlob")
    content_regex = payload.get("contentRegex")
    if not name_glob and not content_regex:
        raise ValueError("nameGlob and/or contentRegex is required")
    max_matches = int(payload.get("maxMatches") or 100)
    max_file_bytes = int(payload.get("maxFileBytes") or 1024 * 1024)
    # H5: cap FILES EXAMINED too — a no-match regex used to read every file
    # under the root (unbounded runtime). `truncated` now also fires on the
    # scan cap; narrow the root or raise maxFiles.
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
