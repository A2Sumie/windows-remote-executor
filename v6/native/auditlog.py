"""Best-effort JSONL audit log shared by rpc.py and the process/job actions.

Never blocks the RPC path: any failure is swallowed. Windows-only — local
macOS/Linux selftests and loopback runs skip the log entirely (the audit
path is a host filesystem location).

H12 rotation (2026-08-25): rpc-audit.log rotates at 20 MB to rpc-audit.log.1
(one previous generation kept, gzip-free for tail-ability). A host running
since the v4 era accumulates unbounded audit lines otherwise.
H14 job history (2026-08-25): job exits append a compact summary line to
WRE_ROOT/logs/jobs-history.log — jobId, exe, args hash, exitCode, duration,
kill/timeout flags and a 2 KB log tail. Job logs themselves expire with the
24 h retention; this history file is the durable record ("keep logs on the
installed machine for future improvement").
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from version import AUDIT_LOG_PATH, LOGS_DIR

AUDIT_ROTATE_BYTES = 20 * 1024 * 1024
JOBS_HISTORY_PATH = f"{LOGS_DIR}/jobs-history.log"
JOBS_HISTORY_TAIL_BYTES = 2048


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rotate_if_large(path: str, limit: int) -> None:
    """Size-based rotation: path -> path.1 (previous .1 is dropped)."""
    try:
        if os.path.getsize(path) > limit:
            prev = path + ".1"
            if os.path.exists(prev):
                os.remove(prev)
            os.replace(path, prev)
    except OSError:
        pass


def _append_jsonl(path: str, entry: dict[str, Any]) -> None:
    line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(line + "\n")


def write_audit(entry: dict[str, Any]) -> None:
    if os.name != "nt":
        return
    try:
        os.makedirs(os.path.dirname(AUDIT_LOG_PATH), exist_ok=True)
        _rotate_if_large(AUDIT_LOG_PATH, AUDIT_ROTATE_BYTES)
        _append_jsonl(AUDIT_LOG_PATH, entry)
    except OSError:
        pass


def audit_request(request: dict[str, Any], response: dict[str, Any], duration_ms: int) -> None:
    write_audit({
        "ts": now_iso(),
        "id": response.get("id") or str(request.get("id") or ""),
        "action": str(request.get("action") or ""),
        "ok": bool(response.get("ok")),
        "errorClass": str(response.get("errorClass") or ""),
        "durationMs": duration_ms,
    })


def audit_job(event: str, **fields: Any) -> None:
    """Job/process lifecycle event (start/exit/kill/reap/cleanup)."""
    write_audit({"ts": now_iso(), "kind": "job", "event": event, **fields})


def audit_job_history(meta: dict[str, Any], log_tail: str) -> None:
    """H14: durable per-job exit record. The ring-buffered job log dies with
    the 24 h retention sweep; this one-line summary survives for post-mortem
    ('what long jobs ran here and how did they end')."""
    if os.name != "nt":
        return
    entry = {
        "ts": now_iso(),
        "jobId": meta.get("jobId"),
        "exe": meta.get("exe"),
        "argsSha256": meta.get("argsSha256"),
        "state": meta.get("state"),
        "exitCode": meta.get("exitCode"),
        "durationMs": meta.get("durationMs"),
        "timedOut": bool(meta.get("timedOut")),
        "killed": bool(meta.get("killed")),
        "startedAt": meta.get("startedAt"),
        "endedAt": meta.get("endedAt"),
        "logTail": log_tail[-JOBS_HISTORY_TAIL_BYTES:],
    }
    try:
        os.makedirs(os.path.dirname(JOBS_HISTORY_PATH), exist_ok=True)
        _rotate_if_large(JOBS_HISTORY_PATH, AUDIT_ROTATE_BYTES)
        _append_jsonl(JOBS_HISTORY_PATH, entry)
    except OSError:
        pass
