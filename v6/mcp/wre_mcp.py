#!/usr/bin/env python3
"""WRE v6 MCP server — agent-first front end (design §9).

One tool per mental unit; tool arguments are structured data, so the agent
never assembles CLI strings or nested JSON quoting. Every tool returns a JSON
string: the RPC `data` on success, or {"error": errorClass, "message": ...}.

Run (stdio):
    python3 -m v6.mcp.wre_mcp                 # repo root on PYTHONPATH
    pip install mcp  (the only dependency; controller code is stdlib-only)

Sessions: one ssh connection per (target, entry_root) is held open (native
loop mode) and auto-reconnects once on failure. Set WRE_ENTRY to point every
tool at a sidecar tree (e.g. C:/WRE/wre6).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

try:
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore  # mcp 1.x
except ImportError:
    try:
        from mcp.server.mcpserver import MCPServer as _Server  # type: ignore  # mcp 2.x
    except ImportError:  # pragma: no cover
        sys.stderr.write(
            "wre_mcp: the 'mcp' package is required (pip install mcp). "
            "The CLI (python3 -m v6.controller.shell) works without it.\n"
        )
        raise SystemExit(2)

from v6.controller import client as rpc  # noqa: E402
from v6.controller import targets as tgt  # noqa: E402

mcp = _Server("wre-v6")

_sessions: dict[tuple[str, str], rpc.Session] = {}


def _session(target_name: str, entry_root: str | None) -> rpc.Session:
    key = (target_name, rpc.resolve_entry_root(entry_root))
    sess = _sessions.get(key)
    if sess is None:
        target = tgt.load_target(target_name)
        sess = rpc.Session(target, entry_root=entry_root)
        sess._open()
        _sessions[key] = sess
    return sess


def _call(target_name: str, action: str, payload: dict[str, Any],
          entry_root: str | None = None, timeout_ms: int | None = None) -> str:
    try:
        call = _session(target_name, entry_root).call(action, payload, timeout_ms=timeout_ms)
    except rpc.RpcError as exc:
        return json.dumps({"error": "transport", "message": str(exc)}, ensure_ascii=False)
    if not call.ok:
        return json.dumps({"error": call.error_class, "message": call.message,
                           "didYouMean": call.response.get("didYouMean", "")},
                          ensure_ascii=False)
    return json.dumps(call.data, ensure_ascii=False)


# ---------- process ----------

@mcp.tool()
def wre_run(target: str, exe: str, args: list[str] | None = None,
            cwd: str | None = None, stdin_text: str | None = None,
            env: dict[str, str] | None = None, timeout_ms: int | None = None,
            capture_kb: int | None = None, entry_root: str | None = None) -> str:
    """Run an exe on the host synchronously; returns exitCode/stdout/stderr/
    timedOut/durationMs. exe must be an absolute path; args is an argv array
    (never a shell string). Server-side timeout kills the process."""
    payload: dict[str, Any] = {"exe": exe}
    if args is not None:
        payload["args"] = args
    if cwd:
        payload["cwd"] = cwd
    if stdin_text is not None:
        payload["stdinText"] = stdin_text
    if env:
        payload["env"] = env
    if timeout_ms:
        payload["timeoutMs"] = timeout_ms
    if capture_kb:
        payload["captureKB"] = capture_kb
    return _call(target, "process.run", payload, entry_root,
                 timeout_ms=(timeout_ms + 60_000) if timeout_ms else None)


@mcp.tool()
def wre_start(target: str, exe: str, args: list[str] | None = None,
              cwd: str | None = None, timeout_ms: int | None = None,
              capture_kb: int | None = None, entry_root: str | None = None) -> str:
    """Start an async job; returns {jobId, outputPath}. Output ring-buffers to
    WRE_ROOT/jobs/<jobId>.log; jobs survive rpc restarts (24h retention)."""
    payload: dict[str, Any] = {"exe": exe}
    if args is not None:
        payload["args"] = args
    if cwd:
        payload["cwd"] = cwd
    if timeout_ms:
        payload["timeoutMs"] = timeout_ms
    if capture_kb:
        payload["captureKB"] = capture_kb
    return _call(target, "process.start", payload, entry_root)


@mcp.tool()
def wre_wait(target: str, job_id: str, timeout_ms: int = 60000,
             tail_kb: int = 32, entry_root: str | None = None) -> str:
    """Long-poll a job until exit or timeout; returns exitCode + logTail."""
    return _call(target, "process.wait",
                 {"jobId": job_id, "timeoutMs": timeout_ms, "tailKB": tail_kb},
                 entry_root, timeout_ms=timeout_ms + 60_000)


@mcp.tool()
def wre_status(target: str, job_id: str, entry_root: str | None = None) -> str:
    """Job state: running/exited, durationMs, exitCode, outputSizeBytes."""
    return _call(target, "process.status", {"jobId": job_id}, entry_root)


@mcp.tool()
def wre_kill(target: str, job_id: str, force: bool = False,
             entry_root: str | None = None) -> str:
    """Terminate a job; force=True kills the whole process tree."""
    return _call(target, "process.kill", {"jobId": job_id, "force": force}, entry_root)


# ---------- wsl ----------

@mcp.tool()
def wre_wsl(target: str, argv: list[str] | None = None, shell: str | None = None,
            distro: str | None = None, user: str | None = None,
            win_cwd: str | None = None, timeout_ms: int | None = None,
            entry_root: str | None = None) -> str:
    """Run inside WSL. argv -> wsl --exec passthrough (no shell, preferred);
    shell -> bash -lc "<string>". WSL_UTF8=1 always injected; default 60s."""
    payload: dict[str, Any] = {}
    if argv is not None:
        payload["argv"] = argv
    if shell is not None:
        payload["shell"] = shell
    if distro:
        payload["distro"] = distro
    if user:
        payload["user"] = user
    if win_cwd:
        payload["winCwd"] = win_cwd
    if timeout_ms:
        payload["timeoutMs"] = timeout_ms
    return _call(target, "wsl.run", payload, entry_root,
                 timeout_ms=(timeout_ms + 60_000) if timeout_ms else 120_000)


@mcp.tool()
def wre_wsl_list(target: str, entry_root: str | None = None) -> str:
    """List WSL distros with state/default/wslVersion."""
    return _call(target, "wsl.list", {}, entry_root, timeout_ms=60_000)


@mcp.tool()
def wre_wsl_status(target: str, entry_root: str | None = None) -> str:
    """WSL availability + VM running (also warms an idle VM)."""
    return _call(target, "wsl.status", {}, entry_root, timeout_ms=60_000)


# ---------- files ----------

@mcp.tool()
def wre_read(target: str, path: str, offset: int | None = None,
             tail: int | None = None, max_bytes: int | None = None,
             entry_root: str | None = None) -> str:
    """Read a UTF-8 file. offset/tail (bytes) for logs; supports
    //wsl.localhost/<distro>/... UNC paths."""
    payload: dict[str, Any] = {"path": path}
    if offset is not None:
        payload["offset"] = offset
    if tail is not None:
        payload["tail"] = tail
    if max_bytes:
        payload["maxBytes"] = max_bytes
    return _call(target, "file.readText", payload, entry_root)


@mcp.tool()
def wre_write(target: str, path: str, text: str,
              entry_root: str | None = None) -> str:
    """Atomic UTF-8 write (tmp+replace); returns a sha256 proof."""
    return _call(target, "file.writeText", {"path": path, "text": text}, entry_root)


@mcp.tool()
def wre_list(target: str, path: str, pattern: str = "*", recursive: bool = False,
             max_entries: int = 500, entry_root: str | None = None) -> str:
    """Directory listing with glob pattern."""
    return _call(target, "file.list", {"path": path, "pattern": pattern,
                                       "recursive": recursive, "maxEntries": max_entries},
                 entry_root)


@mcp.tool()
def wre_search(target: str, root: str, name_glob: str | None = None,
               content_regex: str | None = None, max_matches: int = 100,
               entry_root: str | None = None) -> str:
    """Search by filename glob and/or content regex."""
    payload: dict[str, Any] = {"root": root, "maxMatches": max_matches}
    if name_glob:
        payload["nameGlob"] = name_glob
    if content_regex:
        payload["contentRegex"] = content_regex
    return _call(target, "file.search", payload, entry_root)


# ---------- tasks (SYSTEM lane) ----------

@mcp.tool()
def wre_tasks(target: str, prefix: str | None = None, verbose: bool = False,
              limit: int | None = None, older_than_days: int | None = None,
              entry_root: str | None = None) -> str:
    """List scheduled tasks (summary by default; verbose adds actions/principal)."""
    payload: dict[str, Any] = {"verbose": verbose}
    if prefix is not None:
        payload["prefix"] = prefix
    if limit:
        payload["limit"] = limit
    if older_than_days is not None:
        payload["olderThanDays"] = older_than_days
    return _call(target, "host.tasks.list", payload, entry_root, timeout_ms=120_000)


@mcp.tool()
def wre_task_run(target: str, name: str, entry_root: str | None = None) -> str:
    """Trigger a scheduled task now (SYSTEM lane; leaves an audit trail)."""
    return _call(target, "host.task.run", {"name": name}, entry_root)


@mcp.tool()
def wre_task_create(target: str, name: str, exe: str,
                    args: list[str] | None = None, cwd: str | None = None,
                    trigger: str = "manual", run_as_user: str = "SYSTEM",
                    delete_after_run: bool = False,
                    expires_at: str | None = None,
                    entry_root: str | None = None) -> str:
    """Register a scheduled task via COM. delete_after_run self-deletes at
    expires_at (default +24h). Runs as SYSTEM unless run_as_user overrides."""
    payload: dict[str, Any] = {"name": name, "exe": exe, "trigger": trigger,
                               "run_as_user": run_as_user}
    if args is not None:
        payload["args"] = args
    if cwd:
        payload["cwd"] = cwd
    if delete_after_run:
        payload["deleteAfterRun"] = True
    if expires_at:
        payload["expiresAt"] = expires_at
    return _call(target, "host.task.create", payload, entry_root, timeout_ms=60_000)


# ---------- host ----------

@mcp.tool()
def wre_info(target: str, entry_root: str | None = None) -> str:
    """Cheap pre-flight self-check: whoami/protocol/build/policy/uptime."""
    return _call(target, "host.info", {}, entry_root)


@mcp.tool()
def wre_probe(target: str, categories: list[str] | None = None,
              entry_root: str | None = None) -> str:
    """Host snapshot: subset of os/sshd/policy/tasks/python."""
    return _call(target, "host.probe",
                 {"categories": categories or ["os", "sshd", "policy"]}, entry_root,
                 timeout_ms=60_000)


@mcp.tool()
def wre_guard(target: str, entry_root: str | None = None) -> str:
    """sshd exposure diagnostics, read-only (noDisable=true)."""
    return _call(target, "host.guard", {"noDisable": True}, entry_root, timeout_ms=60_000)


@mcp.tool()
def wre_help(target: str, action: str | None = None,
             entry_root: str | None = None) -> str:
    """Machine+human docs for one action (or the whole surface)."""
    return _call(target, "system.help", {"action": action} if action else {}, entry_root)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
