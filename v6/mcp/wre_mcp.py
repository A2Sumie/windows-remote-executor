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

from v6.controller import client as rpc
from v6.controller import targets as tgt

_INSTRUCTIONS = """\
WRE v6 bridge - Windows hosts X570 (100.119.106.8) and nuc-8-sumie (100.86.160.35).
Structured arguments only; never shell strings or PowerShell one-liners.

- File search on X570: use wre_find / wre_find_check / wre_find_catchup FIRST
  (wreq.exe = our Everything-RE MFT engine, D:/H3200, ~2.4M files, instant).
  Do NOT deep-scan with wre_search / file.search - the bounded walk hits scan
  caps and returns truncated results.
- Process model: wre_run (sync) or wre_start + wre_wait/wre_status/wre_kill (jobs).
- Files: wre_read / wre_write / wre_list / wre_search (bounded walks only).
- Scheduled tasks: wre_tasks / wre_task_run / wre_task_create (SYSTEM via scheduler).
- WSL: wre_wsl / wre_wsl_list / wre_wsl_status. Diagnostics: wre_info / wre_probe /
  wre_guard / wre_help.
- Failure semantics: {"error":"transport"} means the ssh session broke (Mac sleep,
  host reboot, tailscale flap). The bridge auto-reconnects+resends once ONLY when
  the request provably never left the bridge (write break); read-phase breaks
  surface {"error":"transport"} without an automatic resend, so RETRY once before
  concluding anything (the peer may or may not have processed the first attempt).
  Persistent failure: run wre_info as a preflight;
  quick ops carry a default deadline so a wedged peer fails in seconds, not 10 min.
- wre MCP missing from the tool catalog? Hot-reconnect, no opencode restart:
  opencode2 api post '/api/mcp/wre/disconnect?location%5Bdirectory%5D=<absdir>' then
  the same for /connect. CLI fallback from the repo root:
  PYTHONPATH=. python3 -m v6.controller.shell <target> <action> '<json>'.
- Full protocol + elevation boundary: workspace skill `wre` and v6/V6.md.
"""

mcp = _Server("wre-v6", instructions=_INSTRUCTIONS)

_sessions: dict[tuple[str, str], rpc.Session] = {}


def _session(target_name: str, entry_root: str | None) -> rpc.Session:
    key = (target_name, rpc.resolve_entry_root(entry_root))
    sess = _sessions.get(key)
    if sess is None:
        try:
            target = tgt.load_target(target_name)
        except Exception as exc:  # unknown target / unreadable env file
            raise rpc.RpcError(
                f"cannot load target {target_name!r}: {exc} "
                "(valid targets: X570, nuc-8-sumie)") from exc
        sess = rpc.Session(target, entry_root=entry_root)
        try:
            sess._open()
        except Exception as exc:  # ssh spawn failure IS a transport failure
            raise rpc.RpcTransportError(
                f"cannot open session to {target_name!r}: {exc}") from exc
        _sessions[key] = sess
    return sess


def _call(target_name: str, action: str, payload: dict[str, Any],
          entry_root: str | None = None, timeout_ms: int | None = None) -> str:
    try:
        call = _session(target_name, entry_root).call(action, payload, timeout_ms=timeout_ms)
    except rpc.RpcError as exc:
        return json.dumps({"error": "transport", "message": str(exc)}, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001 - containment: never kill the stdio transport
        return json.dumps({"error": "internal",
                           "message": f"{type(exc).__name__}: {exc}"},
                          ensure_ascii=False)
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
    timedOut/durationMs.

    Conventions (avoids all backslash/quote failures):
    - exe: absolute path, FORWARD slashes preferred (C:/Windows/System32/cmd.exe) —
      JSON-safe with zero escaping.
    - args: argv ARRAY, one element per token. NEVER stuff a whole command line
      into one element and never use shell syntax ('|', '>', ';', quotes).
      For cmd.exe built-ins pass ["C:/Windows/System32/cmd.exe","/d","/c","prog","arg1","arg2"].
    - Paths in args: forward slashes are accepted by virtually every Windows program;
      use them instead of backslashes to sidestep escaping entirely."""
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
    WRE_ROOT/jobs/<jobId>.log; jobs survive rpc restarts (24h retention).

    NOTE: never launch a long-lived SERVER via wre_start with timeout_ms set —
    the supervisor kills the process at timeout (vmcp died 5h44m that way on
    2026-09-07). For daemons use a scheduled task (wre_task_create) instead."""
    payload: dict[str, Any] = {"exe": exe}
    if args is not None:
        payload["args"] = args
    if cwd:
        payload["cwd"] = cwd
    if timeout_ms:
        payload["timeoutMs"] = timeout_ms
    if capture_kb:
        payload["captureKB"] = capture_kb
    return _call(target, "process.start", payload, entry_root, timeout_ms=60_000)


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
    return _call(target, "process.status", {"jobId": job_id}, entry_root,
                 timeout_ms=30_000)


@mcp.tool()
def wre_kill(target: str, job_id: str, force: bool = False,
             entry_root: str | None = None) -> str:
    """Terminate a job; force=True kills the whole process tree."""
    return _call(target, "process.kill", {"jobId": job_id, "force": force}, entry_root,
                 timeout_ms=30_000)


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
    return _call(target, "file.readText", payload, entry_root, timeout_ms=60_000)


@mcp.tool()
def wre_write(target: str, path: str, text: str,
              entry_root: str | None = None) -> str:
    """Atomic UTF-8 write (tmp+replace); returns a sha256 proof."""
    return _call(target, "file.writeText", {"path": path, "text": text}, entry_root,
                 timeout_ms=60_000)


@mcp.tool()
def wre_list(target: str, path: str, pattern: str = "*", recursive: bool = False,
             max_entries: int = 500, entry_root: str | None = None) -> str:
    """Directory listing with glob pattern."""
    return _call(target, "file.list", {"path": path, "pattern": pattern,
                                       "recursive": recursive, "maxEntries": max_entries},
                 entry_root, timeout_ms=60_000)


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
    return _call(target, "host.task.run", {"name": name}, entry_root, timeout_ms=30_000)


@mcp.tool()
def wre_task_create(target: str, name: str, exe: str,
                    args: list[str] | None = None, cwd: str | None = None,
                    trigger: str = "manual", run_as_user: str = "SYSTEM",
                    delete_after_run: bool = False,
                    expires_at: str | None = None,
                    entry_root: str | None = None) -> str:
    """Register a scheduled task via COM. delete_after_run self-deletes at
    expires_at (default +24h). Runs as SYSTEM unless run_as_user overrides.
    Paths: prefer forward slashes (JSON-safe). NOTE v6.1 finding: 'interval'
    triggers fail with a TaskScheduler COM StartBoundary error — register
    interval/repetition tasks via schtasks /Create /XML instead."""
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
    return _call(target, "host.info", {}, entry_root, timeout_ms=30_000)


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
    return _call(target, "system.help", {"action": action} if action else {},
                 entry_root, timeout_ms=30_000)


# ---------- wreq: USN-fresh whole-volume file search ----------

_WREQ = "C:/WRE/tmp/wreq.exe"


@mcp.tool()
def wre_find(target: str, query: str, db: str | None = None,
             n: int = 50, fresh: bool = False, path_mode: bool = False,
             json_out: bool = True, sort_name: bool = False,
             entry_root: str | None = None) -> str:
    """Fast file search over the wreq snapshot index (Everything-RE derived).

    Query syntax: substrings AND together ("neo mkv"), "quoted phrase",
    *.wild cards, ext:mp4|mkv, size:>4gb / 500mb..2gb,
    dm:>7d|today|2026-08-01..2026-08-15, path:D:\\dir\\ , dir: / file:, case:.
    Returns NDJSON hits {name,path,size,mtime_unix,is_dir} + a stderr count
    line. Millisecond-scale on million-entry indexes; `fresh` folds the USN
    journal delta first so just-created files are visible (needs admin).
    Falls back to wre_search for non-NTFS volumes or when no index exists.
    """
    args = ["find", query]
    if db:
        args += ["-db", db]
    if n != 50:
        args += ["-n", str(n)]
    if fresh:
        args.append("-fresh")
    if path_mode:
        args.append("-path")
    if json_out:
        args.append("-json")
    if sort_name:
        args.append("-sort")
    return _call(target, "process.run",
                 {"exe": _WREQ, "args": args, "captureKB": 64,
                  "timeoutMs": 90_000 if fresh else 60_000},
                 entry_root, timeout_ms=120_000)


@mcp.tool()
def wre_find_check(target: str, db: str | None = None,
                   entry_root: str | None = None) -> str:
    """wreq index health: age, files/dirs counts, cursor freshness."""
    args = ["check"] + (["-db", db] if db else [])
    return _call(target, "process.run",
                 {"exe": _WREQ, "args": args, "captureKB": 8, "timeoutMs": 30_000},
                 entry_root, timeout_ms=60_000)


@mcp.tool()
def wre_find_catchup(target: str, max_age_min: float = 0,
                     db: str | None = None,
                     entry_root: str | None = None) -> str:
    """Fold the USN journal delta into the wreq index now (admin).
    Zero-cost when already fresh; auto-rescans when the journal wrapped."""
    args = ["catchup", "--max-age", str(max_age_min)]
    if db:
        args += ["-db", db]
    return _call(target, "process.run",
                 {"exe": _WREQ, "args": args, "captureKB": 8, "timeoutMs": 180_000},
                 entry_root, timeout_ms=240_000)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
