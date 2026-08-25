#!/usr/bin/env python3
"""WRE v4 native rpc-stdio entrypoint.

Runs on the Windows host. Invoked as:
    pythonw.exe -I -X utf8 C:/CodexRemote/wre/rpc.py rpc-stdio

Reads one UTF-8 JSON request line on stdin, writes one UTF-8 JSON
response line on stdout. See V4.md for the protocol contract.

v4-hardening backports (2026-08-25, branch v4-hardening; protocolVersion
stays 4, action list and wire format unchanged):
- Fail-closed auth in win32/access_policy.py (from v5; audit finding A1).
- Best-effort per-request audit log at C:/CodexRemote/logs/rpc-audit.log
  (from v5; audit finding A6), WITH 20 MB size rotation keeping one `.1`
  generation (from v6/native/auditlog.py H12) — the log is rotated from the
  day it is introduced, and the same rotation now covers wre-apply.log and
  the sshd guard/repair logs, which previously grew without bound.
- Bounded request line (16 MB) instead of an unbounded readline() (from v5).
"""

from __future__ import annotations

import sys
import os
import json
import time
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

PROTOCOL_VERSION = 4
ACTIONS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}

# putBinary allows 4 MB of raw bytes -> ~5.6 MB of base64; 16 MB leaves ample
# headroom while still bounding memory per one-shot rpc process.
MAX_REQUEST_LINE_BYTES = 16 * 1024 * 1024

AUDIT_LOG_PATH = "C:/CodexRemote/logs/rpc-audit.log"
# Rotation (from v6/native/auditlog.py): size-based, one previous generation
# kept as `<log>.1`, gzip-free for tail-ability.
LOG_ROTATE_BYTES = 20 * 1024 * 1024

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS_DIR)

from actions import host as host_actions  # noqa: E402
from actions import files as file_actions  # noqa: E402
from win32.access_policy import enforce_policy_for_action  # noqa: E402


def _register(name: str) -> Callable:
    def deco(fn: Callable[[dict[str, Any]], dict[str, Any]]) -> Callable[[dict[str, Any]], dict[str, Any]]:
        ACTIONS[name] = fn
        return fn
    return deco


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _collect_registrations() -> None:
    from actions import host as host_actions
    from actions import files as file_actions
    for name, fn in (*host_actions.REGISTRATIONS, *file_actions.REGISTRATIONS):
        ACTIONS[name] = fn


_collect_registrations()


def _build_response(
    request_id: str,
    ok: bool,
    *,
    data: dict[str, Any] | None = None,
    error_class: str = "",
    stdout_text: str = "",
    stderr_text: str = "",
    stdout_encoding: str = "utf-8",
    stderr_encoding: str = "utf-8",
    started_at: str | None = None,
    ended_at: str | None = None,
    evidence: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": request_id,
        "ok": ok,
        "protocolVersion": PROTOCOL_VERSION,
        "errorClass": error_class,
        "stdoutText": stdout_text,
        "stderrText": stderr_text,
        "stdoutEncoding": stdout_encoding,
        "stderrEncoding": stderr_encoding,
        "startedAt": started_at or _now_iso(),
        "endedAt": ended_at or _now_iso(),
        "data": data or {},
        "evidence": evidence or [],
    }


def _err_response(request_id: str, error_class: str, message: str) -> dict[str, Any]:
    return _build_response(
        request_id,
        False,
        error_class=error_class,
        stderr_text=message,
        evidence=[error_class],
    )


@_register("host.capabilities")
def _host_capabilities(_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "stdout_text": "",
        "data": {
            "protocol": "wre-rpc-stdio",
            "version": PROTOCOL_VERSION,
            "actions": sorted(ACTIONS.keys()),
        },
        "evidence": ["host.capabilities"],
    }


def dispatch(request: dict[str, Any]) -> dict[str, Any]:
    start_perf = time.perf_counter()
    response = _dispatch_inner(request)
    duration_ms = int((time.perf_counter() - start_perf) * 1000)
    response["durationMs"] = duration_ms
    _audit(request, response, duration_ms)
    return response


def _rotate_if_large(path: str, limit: int = LOG_ROTATE_BYTES) -> None:
    """Size-based rotation: path -> path.1 (previous .1 is dropped)."""
    try:
        if os.path.getsize(path) > limit:
            prev = path + ".1"
            if os.path.exists(prev):
                os.remove(prev)
            os.replace(path, prev)
    except OSError:
        pass


def _write_audit(entry: dict[str, Any]) -> None:
    """Best-effort JSONL audit of every dispatched request.

    Never blocks the RPC path: any failure is swallowed. Windows-only — local
    macOS/Linux selftests and loopback runs skip the log entirely (the audit
    path is a host filesystem location). Rotated at 20 MB (see
    _rotate_if_large).
    """
    if os.name != "nt":
        return
    try:
        os.makedirs(os.path.dirname(AUDIT_LOG_PATH), exist_ok=True)
        _rotate_if_large(AUDIT_LOG_PATH)
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        with open(AUDIT_LOG_PATH, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _audit(request: dict[str, Any], response: dict[str, Any], duration_ms: int) -> None:
    _write_audit({
        "ts": _now_iso(),
        "id": response.get("id") or str(request.get("id") or ""),
        "action": str(request.get("action") or ""),
        "ok": bool(response.get("ok")),
        "errorClass": str(response.get("errorClass") or ""),
        "durationMs": duration_ms,
    })


def _dispatch_inner(request: dict[str, Any]) -> dict[str, Any]:
    request_id = str(request.get("id") or f"rpc-{uuid.uuid4().hex}")
    action = str(request.get("action") or "").strip()
    payload = request.get("payload") or {}
    access_token = request.get("accessToken")
    timeout_seconds = request.get("timeoutSeconds")
    started = _now_iso()

    if not action:
        return _err_response(request_id, "request", "missing action")

    handler = ACTIONS.get(action)
    if handler is None:
        return _err_response(request_id, "unsupported", f"unsupported action: {action}")

    try:
        enforce_policy_for_action(action, access_token)
    except PermissionError as exc:
        return _err_response(request_id, "auth", str(exc))

    if not isinstance(payload, dict):
        return _err_response(request_id, "request", "payload must be a JSON object")

    if timeout_seconds is not None:
        # v4 native does not enforce timeout on its own thread; we record it.
        pass

    try:
        result = handler(payload)
    except FileNotFoundError as exc:
        return _build_response(
            request_id, False, error_class="not-found",
            stderr_text=str(exc), started_at=started, ended_at=_now_iso(),
            evidence=[action, "not-found"],
        )
    except ValueError as exc:
        return _build_response(
            request_id, False, error_class="request",
            stderr_text=str(exc), started_at=started, ended_at=_now_iso(),
            evidence=[action, "request"],
        )
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc(limit=8)
        return _build_response(
            request_id, False, error_class="remote-exception",
            stderr_text=f"{exc}\n{tb}", started_at=started, ended_at=_now_iso(),
            evidence=[action, "remote-exception"],
        )

    ended = _now_iso()
    response = _build_response(
        request_id,
        True,
        data=result.get("data", {}),
        stdout_text=result.get("stdout_text", ""),
        stderr_text=result.get("stderr_text", ""),
        started_at=started,
        ended_at=ended,
        evidence=[action] + result.get("evidence_extra", []),
    )
    return response


def _parse_kv_flags(argv: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok.startswith("--"):
            key = tok[2:]
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                out[key] = argv[i + 1]
                i += 2
            else:
                out[key] = "1"
                i += 1
        else:
            i += 1
    return out


def _run_repair_sshd(argv: list[str]) -> int:
    """Self-heal entry invoked by the SYSTEM scheduled tasks. Runs the same
    logic as the host.repair action but writes a human log line to stderr."""
    flags = _parse_kv_flags(argv)
    from actions import host as host_actions
    payload = {
        "expectedListenAddress": flags.get("expected-listen-address")
        or flags.get("expected-listen"),
        "forceRewrite": flags.get("force-rewrite", "") == "1",
        "logPath": flags.get("log-path") or "C:/CodexRemote/logs/sshd-repair.log",
    }
    try:
        result = host_actions._repair(payload)
        sys.stderr.write(json.dumps(result.get("data", {}), ensure_ascii=False) + "\n")
        return 0
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"repair-sshd error: {exc}\n")
        return 1


def _run_apply_tasks(argv: list[str]) -> int:
    """SYSTEM-privileged task (re)registration agent.

    Invoked by the `CodexRemote WRE Apply` scheduled task, which runs as SYSTEM.
    This lets the controller re-register every managed task WITHOUT the operator
    re-running an elevated command each time v4 changes. Reads a JSON spec file
    (default C:/CodexRemote/wre/apply-tasks.json) describing which tasks to
    ensure. Absent file => ensure sshd repair tasks + StreamServ backend task
    with defaults.
    """
    flags = _parse_kv_flags(argv)
    spec_path = flags.get("spec") or "C:/CodexRemote/wre/apply-tasks.json"
    expected_listen = flags.get("expected-listen") or ""
    from win32 import scheduled_tasks as tasks_mod

    spec: dict[str, Any] = {}
    try:
        if os.path.isfile(spec_path):
            with open(spec_path, "r", encoding="utf-8-sig") as fh:
                spec = json.load(fh)
    except (OSError, ValueError) as exc:  # ValueError covers JSONDecodeError
        sys.stderr.write(f"apply-tasks: cannot read spec {spec_path}: {exc}\n")

    expected_listen = spec.get("expectedListenAddress") or expected_listen
    results: dict[str, Any] = {"repairTasks": None, "streamserv": None, "custom": []}

    exit_code = 0
    if spec.get("ensureRepairTasks", True):
        try:
            results["repairTasks"] = tasks_mod.ensure_repair_tasks(expected_listen=expected_listen)
        except Exception as exc:  # noqa: BLE001
            results["repairTasks"] = {"error": str(exc)}
            exit_code = 1

    ss = spec.get("streamserv")
    try:
        if ss is None:
            # default: register the headless backend task as SYSTEM
            results["streamserv"] = tasks_mod.ensure_streamserv_task()
        elif ss:  # a dict with overrides
            run_as = ss.get("runAsUser", "SYSTEM")
            # SYSTEM apply-agent cannot reliably create/update a non-SYSTEM user
            # task without credentials. Keep existing user task; elevated
            # deploy-wre creates it. This avoids deleting a known-good task.
            if str(run_as).upper() != "SYSTEM":
                results["streamserv"] = {"skipped": "non-SYSTEM user task kept", "runAsUser": run_as}
            else:
                results["streamserv"] = tasks_mod.ensure_streamserv_task(
                    root=ss.get("root", "D:/StreamServ"),
                    run_as_user=run_as,
                )
    except Exception as exc:  # noqa: BLE001
        results["streamserv"] = {"error": str(exc), "spec": ss}
        exit_code = 1

    for custom in spec.get("tasks", []):
        try:
            results["custom"].append(tasks_mod.create_task(custom))
        except Exception as exc:  # noqa: BLE001
            results["custom"].append({"name": custom.get("name"), "error": str(exc)})
            exit_code = 1

    line = json.dumps(results, ensure_ascii=False)
    try:
        os.makedirs("C:/CodexRemote/logs", exist_ok=True)
        _rotate_if_large("C:/CodexRemote/logs/wre-apply.log")
        with open("C:/CodexRemote/logs/wre-apply.log", "a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")
    except OSError as exc:
        sys.stderr.write(f"apply-tasks: cannot write log: {exc}\n")
    sys.stderr.write(line + "\n")
    return exit_code


def _read_request_line() -> dict[str, Any]:
    raw = sys.stdin.readline(MAX_REQUEST_LINE_BYTES + 1)
    if not raw:
        return {}
    if len(raw) > MAX_REQUEST_LINE_BYTES:
        raise ValueError(
            f"request line exceeds {MAX_REQUEST_LINE_BYTES} bytes; "
            "split the payload (SFTP for large files)"
        )
    text = raw.lstrip("\ufeff").strip()
    if not text:
        return {}
    return json.loads(text)


def _write_response_line(response: dict[str, Any]) -> None:
    encoded = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write(encoded + "\n")
    sys.stdout.flush()


def _ensure_stdio() -> None:
    """pythonw.exe scheduled-task runs can have sys.stdout/stderr as None.
    RPC mode needs real pipes, but maintenance subcommands can write to NUL.
    """
    if sys.stdin is None:
        sys.stdin = open(os.devnull, "r", encoding="utf-8")  # type: ignore[assignment]
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # type: ignore[assignment]
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # type: ignore[assignment]
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", newline="\n")
            except Exception:
                pass


def main() -> int:
    _ensure_stdio()

    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        sys.stderr.write(__doc__ or "")
        return 0

    if args[0] in ("selftest", "rpc-selftest"):
        response = _build_response(
            "selftest", True,
            data={"protocol": "wre-rpc-stdio", "version": PROTOCOL_VERSION,
                  "actions": sorted(ACTIONS.keys())},
            evidence=["selftest"],
        )
        _write_response_line(response)
        return 0

    if args[0] == "repair-sshd":
        return _run_repair_sshd(args[1:])

    if args[0] == "apply-tasks":
        return _run_apply_tasks(args[1:])

    if args[0] != "rpc-stdio":
        sys.stderr.write(f"unknown subcommand: {args[0]}\n")
        return 2

    try:
        request = _read_request_line()
    except json.JSONDecodeError as exc:
        _write_response_line(_err_response("malformed", "protocol", f"bad json: {exc}"))
        return 0
    except ValueError as exc:
        _write_response_line(_err_response("malformed", "protocol", str(exc)))
        return 0
    if not request:
        _write_response_line(_err_response("empty", "protocol", "empty stdin"))
        return 0

    response = dispatch(request)
    _write_response_line(response)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        sys.stderr.write("rpc-stdio: broken pipe\n")
        raise SystemExit(0)