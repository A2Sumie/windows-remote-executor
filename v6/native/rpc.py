#!/usr/bin/env python3
"""WRE v6 native rpc-stdio entrypoint.

Runs on the Windows host. Invoked as:
    python.exe -I -X utf8 C:/WRE/wre/rpc.py rpc-stdio
(a sidecar deploy uses its own tree, e.g. C:/WRE/wre6/rpc.py; the legacy
fleet tree is C:/CodexRemote/wre/rpc.py)

Protocol: stdin = UTF-8 JSON request lines, stdout = UTF-8 JSON response
lines. v6 reads lines until EOF ("loop mode") so one SSH connection amortizes
many calls; the v5 one-shot behaviour is the degenerate case (write one line,
close stdin, process exits after answering). See V6.md.

v6 changes vs v5 (design: .agent/reports/2026-08-18-wre-v6-design.md):
  - PROTOCOL_VERSION = 6, BUILD = "v6".
  - Loop mode + startup prewarm (pywin32/comtypes import) — COM cost is paid
    once per connection, not once per call.
  - New action families: process.* (run/start/wait/status/kill, job model),
    wsl.* (run/list/wslpath/status), host.info, system.help, host.tasks.clean.
  - Self-description: host.capabilities carries per-action JSON Schemas;
    unknown actions return a didYouMean suggestion (edit distance).
  - Response slimming: `data` is the single source of truth; `stdoutText`
    is opt-in via payload {"includeStdoutText": true}.
  - Error-as-data: errorClass is one of protocol/auth/unsupported/request/
    not-found/remote-exception; `message` is one human line; tracebacks move
    to opt-in payload {"includeTraceback": true}.
  - timeoutMs (ms, replaces timeoutSeconds) — enforced server-side by
    process.run; recorded for every action. The controller still adds a
    +30s transport margin.
  - Audit log (best-effort JSONL) at WRE_ROOT/logs/rpc-audit.log; job
    lifecycle events included. WRE_ROOT/TASK_PREFIX live in version.py.
"""

from __future__ import annotations

import sys
import os
import json
import time
import traceback
import uuid

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS_DIR)

from version import PROTOCOL_VERSION, BUILD, WRE_ROOT, TASK_PREFIX  # noqa: E402
from auditlog import audit_request, now_iso  # noqa: E402
from win32.access_policy import enforce_policy_for_action  # noqa: E402
from schemas import PAYLOAD_SCHEMAS, HELP  # noqa: E402

from typing import Any, Callable  # noqa: E402

ACTIONS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}

# putBinary allows 32 MB of raw bytes -> ~45 MB of base64; 56 MB leaves ample
# headroom while still bounding memory per request line.
MAX_REQUEST_LINE_BYTES = 56 * 1024 * 1024

ERROR_CLASSES = ("protocol", "auth", "unsupported", "request", "not-found", "remote-exception")


def _register(name: str) -> Callable:
    def deco(fn: Callable[[dict[str, Any]], dict[str, Any]]) -> Callable[[dict[str, Any]], dict[str, Any]]:
        ACTIONS[name] = fn
        return fn
    return deco


def _collect_registrations() -> None:
    from actions import host as host_actions
    from actions import files as file_actions
    from actions import process as process_actions
    from actions import wsl as wsl_actions
    for name, fn in (*host_actions.REGISTRATIONS, *file_actions.REGISTRATIONS,
                     *process_actions.REGISTRATIONS, *wsl_actions.REGISTRATIONS):
        ACTIONS[name] = fn


_collect_registrations()


def _prewarm() -> None:
    """Loop-mode startup: pay the pywin32/comtypes import cost once so the
    first real call does not eat it. Best-effort; never fatal."""
    if os.name != "nt":
        return
    try:
        import pythoncom  # type: ignore  # noqa: F401
        import win32com.client  # type: ignore  # noqa: F401
    except Exception:  # noqa: BLE001
        pass


# ---------- responses ----------

def _build_response(
    request_id: str,
    ok: bool,
    *,
    data: dict[str, Any] | None = None,
    error_class: str = "",
    message: str = "",
    stdout_text: str = "",
    stderr_text: str = "",
    started_at: str | None = None,
    ended_at: str | None = None,
    evidence: list[str] | None = None,
    include_stdout: bool = False,
    traceback_text: str = "",
    did_you_mean: str = "",
) -> dict[str, Any]:
    resp: dict[str, Any] = {
        "id": request_id,
        "ok": ok,
        "protocolVersion": PROTOCOL_VERSION,
        "errorClass": error_class,
        "message": message,
        "startedAt": started_at or now_iso(),
        "endedAt": ended_at or now_iso(),
        "data": data or {},
        "evidence": evidence or [],
    }
    # Response slimming: stdoutText/stderrText travel only when the caller
    # opted in via payload.includeStdoutText. `data` is the truth.
    if include_stdout:
        resp["stdoutText"] = stdout_text
        resp["stderrText"] = stderr_text
        resp["stdoutEncoding"] = "utf-8"
        resp["stderrEncoding"] = "utf-8"
    if traceback_text:
        resp["traceback"] = traceback_text
    if did_you_mean:
        resp["didYouMean"] = did_you_mean
    return resp


def _err_response(request_id: str, error_class: str, message: str, *,
                  started_at: str | None = None, ended_at: str | None = None,
                  evidence: list[str] | None = None, traceback_text: str = "",
                  did_you_mean: str = "") -> dict[str, Any]:
    return _build_response(
        request_id, False,
        error_class=error_class,
        message=message.splitlines()[0] if message else message,
        stderr_text=message,
        started_at=started_at,
        ended_at=ended_at,
        evidence=evidence or [error_class],
        traceback_text=traceback_text,
        did_you_mean=did_you_mean,
    )


def _did_you_mean(action: str) -> str:
    import difflib
    matches = difflib.get_close_matches(action, ACTIONS.keys(), n=1, cutoff=0.55)
    return matches[0] if matches else ""


# ---------- built-in actions ----------

@_register("host.capabilities")
def _host_capabilities(_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "stdout_text": "",
        "data": {
            "protocol": "wre-rpc-stdio",
            "version": PROTOCOL_VERSION,
            "build": BUILD,
            "wreRoot": WRE_ROOT,
            "taskPrefix": TASK_PREFIX,
            "actions": sorted(ACTIONS.keys()),
            "schemas": {name: PAYLOAD_SCHEMAS.get(name, {}) for name in sorted(ACTIONS.keys())},
        },
        "evidence": ["host.capabilities"],
    }


@_register("system.help")
def _system_help(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "").strip()
    if action:
        entry = HELP.get(action)
        if entry is None:
            raise ValueError(f"no help for unknown action {action!r}"
                             + (f"; did you mean {_did_you_mean(action)}?" if _did_you_mean(action) else ""))
        return {"data": {"action": action, **entry,
                         "schema": PAYLOAD_SCHEMAS.get(action, {})},
                "stdout_text": "", "evidence": ["system.help"]}
    return {"data": {"actions": {name: {**HELP.get(name, {}),
                                        "schema": PAYLOAD_SCHEMAS.get(name, {})}
                                 for name in sorted(ACTIONS.keys())}},
            "stdout_text": "", "evidence": ["system.help"]}


# ---------- dispatch ----------

def _dispatch_inner(request: dict[str, Any]) -> dict[str, Any]:
    request_id = str(request.get("id") or f"rpc-{uuid.uuid4().hex}")
    action = str(request.get("action") or "").strip()
    payload = request.get("payload") or {}
    access_token = request.get("accessToken")
    started = now_iso()
    include_stdout = bool(isinstance(payload, dict) and payload.get("includeStdoutText"))
    include_tb = bool(isinstance(payload, dict) and payload.get("includeTraceback"))

    def err(error_class: str, message: str, **kw: Any) -> dict[str, Any]:
        return _err_response(request_id, error_class, message,
                             started_at=started, ended_at=now_iso(), **kw)

    if not action:
        return err("protocol", "missing action", evidence=["protocol"])

    handler = ACTIONS.get(action)
    if handler is None:
        suggestion = _did_you_mean(action)
        msg = f"unsupported action: {action}"
        if suggestion:
            msg += f"; did you mean {suggestion}?"
        return err("unsupported", msg, evidence=[action, "unsupported"],
                   did_you_mean=suggestion)

    try:
        enforce_policy_for_action(action, access_token)
    except PermissionError as exc:
        return err("auth", str(exc), evidence=[action, "auth"])

    if not isinstance(payload, dict):
        return err("request", "payload must be a JSON object", evidence=[action, "request"])

    # timeoutMs (ms) is the v6 name; timeoutSeconds is still accepted and
    # converted for back-compat. Native enforces it inside process.run;
    # otherwise it is recorded only (controller adds a +30s transport margin).
    try:
        result = handler(payload)
    except FileNotFoundError as exc:
        return err("not-found", str(exc), evidence=[action, "not-found"])
    except PermissionError as exc:
        return err("auth", str(exc), evidence=[action, "auth"])
    except ValueError as exc:
        return err("request", str(exc), evidence=[action, "request"])
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc(limit=8) if include_tb else ""
        return err("remote-exception", f"{type(exc).__name__}: {exc}",
                   evidence=[action, "remote-exception"], traceback_text=tb)

    return _build_response(
        request_id,
        True,
        data=result.get("data", {}),
        stdout_text=result.get("stdout_text", ""),
        stderr_text=result.get("stderr_text", ""),
        started_at=started,
        ended_at=now_iso(),
        evidence=[action] + result.get("evidence_extra", []),
        include_stdout=include_stdout,
    )


def dispatch(request: dict[str, Any]) -> dict[str, Any]:
    start_perf = time.perf_counter()
    response = _dispatch_inner(request)
    duration_ms = int((time.perf_counter() - start_perf) * 1000)
    response["durationMs"] = duration_ms
    audit_request(request, response, duration_ms)
    return response


# ---------- maintenance subcommands (SYSTEM scheduled tasks) ----------

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
    from version import LOGS_DIR
    from actions import host as host_actions
    payload = {
        "expectedListenAddress": flags.get("expected-listen-address")
        or flags.get("expected-listen"),
        "forceRewrite": flags.get("force-rewrite", "") == "1",
        "logPath": flags.get("log-path") or f"{LOGS_DIR}/sshd-repair.log",
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

    Invoked by the `<TASK_PREFIX> WRE Apply` scheduled task, which runs as
    SYSTEM. Reads a JSON spec file (default <own tree>/apply-tasks.json)
    describing which tasks to ensure. Absent file => ensure sshd repair tasks
    + StreamServ backend task with defaults.
    """
    flags = _parse_kv_flags(argv)
    from version import LOGS_DIR, WRE_TREE
    spec_path = flags.get("spec") or f"{WRE_TREE}/apply-tasks.json"
    expected_listen = flags.get("expected-listen") or ""
    from win32 import scheduled_tasks as tasks_mod

    spec: dict[str, Any] = {}
    try:
        if os.path.isfile(spec_path):
            with open(spec_path, "r", encoding="utf-8-sig") as fh:
                spec = json.load(fh)
    except Exception as exc:  # noqa: BLE001
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
            results["streamserv"] = tasks_mod.ensure_streamserv_task()
        elif ss:
            run_as = ss.get("runAsUser", "SYSTEM")
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
        log_dir = LOGS_DIR.replace("/", os.sep)
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, "wre-apply.log"), "a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")
    except Exception as exc:
        sys.stderr.write(f"apply-tasks: cannot write log: {exc}\n")
    sys.stderr.write(line + "\n")
    return exit_code


# ---------- stdio plumbing ----------

def _read_request_line() -> dict[str, Any] | None:
    """Return one parsed request, {} for a blank line, or None at EOF."""
    raw = sys.stdin.readline(MAX_REQUEST_LINE_BYTES + 1)
    if not raw:
        return None
    if len(raw) > MAX_REQUEST_LINE_BYTES:
        raise ValueError(
            f"request line exceeds {MAX_REQUEST_LINE_BYTES} bytes; "
            "split the payload (chunked file.putBinary, or SFTP for large files)"
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


def _rpc_loop() -> int:
    """v6 loop mode: answer request lines until stdin hits EOF. One SSH
    connection = many calls; a v5-style one-shot is the 1-line degenerate."""
    _prewarm()
    while True:
        try:
            request = _read_request_line()
        except json.JSONDecodeError as exc:
            _write_response_line(_err_response("malformed", "protocol", f"bad json: {exc}"))
            continue
        except ValueError as exc:
            _write_response_line(_err_response("malformed", "protocol", str(exc)))
            continue
        if request is None:
            return 0  # EOF: clean shutdown
        if not request:
            continue  # blank line keepalive
        response = dispatch(request)
        _write_response_line(response)


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
                  "build": BUILD, "actions": sorted(ACTIONS.keys())},
            started_at="", ended_at="",
            evidence=["selftest"],
        )
        _write_response_line(response)
        return 0

    if args[0] == "repair-sshd":
        return _run_repair_sshd(args[1:])

    if args[0] == "apply-tasks":
        return _run_apply_tasks(args[1:])

    if args[0] == "job-run":
        # Detached per-job supervisor spawned by process.start. Not an RPC
        # mode; never invoked by the controller directly.
        flags = _parse_kv_flags(args[1:])
        spec = flags.get("spec")
        if not spec:
            sys.stderr.write("job-run requires --spec <path>\n")
            return 2
        from actions import process as process_actions
        return process_actions.job_run_main(spec)

    if args[0] != "rpc-stdio":
        sys.stderr.write(f"unknown subcommand: {args[0]}\n")
        return 2

    return _rpc_loop()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        sys.stderr.write("rpc-stdio: broken pipe\n")
        raise SystemExit(0)
