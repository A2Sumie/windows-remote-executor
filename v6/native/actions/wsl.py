"""wsl.* — thin convenience layer over the process primitive (design §6.2).

wsl.exe is a regular exe, so everything routes through process.run_capture;
this module only owns the argument marshalling an agent is likely to fumble:

  - argv form  -> wsl.exe [-d distro] [-u user] [--cd winCwd] --exec <argv...>
                  (zero shell, zero quoting)
  - shell form -> wsl.exe ... --exec bash -lc "<single string>"
                  (exactly one mechanical layer)
  - WSL_UTF8=1 is ALWAYS injected (kills the historical UTF-16 redirect bug)
  - default timeout 60s (idle WSL VM spin-up eats several seconds)

Identity boundary (design, written in stone): WSL distros are registered
per-user; a SYSTEM task cannot see the SSH user's distros. wsl.* therefore
runs ONLY in the SSH-user context via process.* — never via host.task.*.

Mockability: set WRE_WSL_EXE to any executable (e.g. /bin/echo) to unit-test
argv assembly and WSL_UTF8 injection off-Windows.
"""

from __future__ import annotations

import os
import sys
from typing import Any

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_THIS_DIR))

from actions import process as proc  # noqa: E402

DEFAULT_WSL_EXE = "C:/Windows/System32/wsl.exe"
DEFAULT_TIMEOUT_MS = 60_000


def _wsl_exe() -> str:
    return os.environ.get("WRE_WSL_EXE") or DEFAULT_WSL_EXE


def build_argv(payload: dict[str, Any]) -> list[str]:
    """Pure argv assembly — unit-tested on macOS via WRE_WSL_EXE."""
    argv_in = payload.get("argv")
    shell = payload.get("shell")
    if argv_in and shell:
        raise ValueError("wsl.run: pass argv (preferred, no shell) OR shell, not both")
    if not argv_in and not shell:
        raise ValueError("wsl.run requires payload.argv (array) or payload.shell (string)")
    args: list[str] = []
    distro = str(payload.get("distro") or "").strip()
    user = str(payload.get("user") or "").strip()
    win_cwd = str(payload.get("winCwd") or "").strip()
    if distro:
        args += ["-d", distro]
    if user:
        args += ["-u", user]
    if win_cwd:
        args += ["--cd", win_cwd]
    if argv_in:
        if not isinstance(argv_in, list):
            raise ValueError("wsl.run argv must be a JSON array")
        args += ["--exec", *[str(a) for a in argv_in]]
    else:
        args += ["--exec", "bash", "-lc", str(shell)]
    return args


def build_env(payload: dict[str, Any]) -> dict[str, str]:
    env = dict(os.environ)
    env["WSL_UTF8"] = "1"  # always; see module docstring
    if isinstance(payload.get("env"), dict):
        for k, v in payload["env"].items():
            env[str(k)] = str(v)
    return env


def _ensure_wsl() -> str:
    exe = _wsl_exe()
    if os.name != "nt" and not os.environ.get("WRE_WSL_EXE"):
        raise FileNotFoundError(
            "wsl.exe is Windows-only; on the controller-side loopback set "
            "WRE_WSL_EXE to a mock executable"
        )
    if os.name == "nt" and not os.path.isfile(exe.replace("/", os.sep)):
        raise FileNotFoundError(f"wsl.exe not found at {exe}; WSL is not installed")
    return exe


def wsl_run(payload: dict[str, Any]) -> dict[str, Any]:
    exe = _ensure_wsl()
    args = build_argv(payload)
    timeout_ms = int(payload.get("timeoutMs") or DEFAULT_TIMEOUT_MS)
    capture_kb = int(payload.get("captureKB") or 256)
    result = proc.run_capture(
        exe, args,
        cwd=None,  # winCwd is translated to --cd instead (wsl owns the chdir)
        stdin_text=payload.get("stdinText"),
        env=build_env(payload),
        timeout_ms=timeout_ms,
        capture_kb=capture_kb,
    )
    result["argv"] = [exe, *args]
    return {"data": result, "stdout_text": result["stdout"],
            "evidence_extra": ["wsl.run"]}


def _decode_wsl_listing(raw: str) -> str:
    """wsl.exe -l -v may still emit UTF-16 (embedded NULs) on some builds."""
    if "\x00" in raw:
        try:
            return raw.encode("utf-8", errors="ignore").decode("utf-16-le", errors="replace")
        except Exception:  # noqa: BLE001
            return raw.replace("\x00", "")
    return raw


def parse_list_output(text: str) -> list[dict[str, Any]]:
    """Parse `wsl.exe -l -v` table output (pure function, unit-tested)."""
    distros: list[dict[str, Any]] = []
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    started = False
    for ln in lines:
        cols = ln.split()
        if not started:
            if cols[:2] == ["NAME", "STATE"] or (cols and cols[0].upper() == "NAME"):
                started = True
            continue
        default = ln.startswith("*") or (cols and cols[0] == "*")
        if cols and cols[0] == "*":
            cols = cols[1:]
        if len(cols) >= 3:
            name, state, version = cols[0], cols[1], cols[2]
        elif len(cols) == 2:
            name, state, version = cols[0], cols[1], ""
        else:
            continue
        distros.append({
            "name": name,
            "state": state,
            "default": default,
            "wslVersion": int(version) if version.isdigit() else None,
        })
    return distros


def wsl_list(_payload: dict[str, Any]) -> dict[str, Any]:
    exe = _ensure_wsl()
    result = proc.run_capture(exe, ["-l", "-v"], cwd=None, stdin_text=None,
                              env=build_env({}), timeout_ms=30_000, capture_kb=64)
    text = _decode_wsl_listing(result["stdout"])
    if result["exitCode"] != 0 and not text.strip():
        combined = (result["stdout"] + result["stderr"]).strip()
        if "no installed distributions" in combined.lower() or "没有" in combined:
            return {"data": {"distros": [], "note": "no distributions installed"},
                    "stdout_text": "", "evidence_extra": ["wsl.list"]}
        raise RuntimeError(f"wsl.exe -l -v failed (exit {result['exitCode']}): {combined[:300]}")
    distros = parse_list_output(text)
    return {"data": {"distros": distros},
            "stdout_text": text, "evidence_extra": ["wsl.list"]}


def wsl_wslpath(payload: dict[str, Any]) -> dict[str, Any]:
    exe = _ensure_wsl()
    path = str(payload.get("path") or "").strip()
    direction = str(payload.get("to") or "").strip().lower()
    if not path:
        raise ValueError("wsl.wslpath requires payload.path")
    if direction not in ("u", "w"):
        raise ValueError("wsl.wslpath payload.to must be 'u' (to Linux) or 'w' (to Windows)")
    # BUG-1 fix: without --exec, wsl.exe re-joins the tail into one command
    # line run by the distro's default shell, which eats backslash sequences
    # ("\C" -> "C") — mangling Windows paths like C:\WRE\inbox.
    # --exec hands argv straight to the binary (same safe form as wsl.run's
    # argv mode in build_argv), so backslashes/spaces/non-ASCII survive.
    result = proc.run_capture(exe, ["--exec", "wslpath", f"-{direction}", path], cwd=None,
                              stdin_text=None, env=build_env({}),
                              timeout_ms=30_000, capture_kb=16)
    out = result["stdout"].strip().splitlines()
    if result["exitCode"] != 0 or not out:
        raise RuntimeError(f"wslpath failed (exit {result['exitCode']}): "
                           f"{(result['stdout'] + result['stderr']).strip()[:200]}")
    return {"data": {"input": path, "to": direction, "path": out[0]},
            "stdout_text": out[0], "evidence_extra": ["wsl.wslpath"]}


def wsl_status(_payload: dict[str, Any]) -> dict[str, Any]:
    """Availability probe; calling it also warms an idle WSL VM."""
    try:
        listed = wsl_list({})
        distros = listed["data"]["distros"]
    except FileNotFoundError as exc:
        return {"data": {"available": False, "vmRunning": None, "note": str(exc)},
                "stdout_text": "", "evidence_extra": ["wsl.status"]}
    except Exception as exc:  # noqa: BLE001
        return {"data": {"available": False, "vmRunning": None,
                         "note": f"{type(exc).__name__}: {exc}"},
                "stdout_text": "", "evidence_extra": ["wsl.status"]}
    running = [d["name"] for d in distros if str(d.get("state", "")).lower() == "running"]
    default = next((d["name"] for d in distros if d.get("default")), None)
    return {"data": {"available": True, "vmRunning": bool(running),
                     "runningDistros": running, "defaultDistro": default,
                     "distroCount": len(distros)},
            "stdout_text": "", "evidence_extra": ["wsl.status"]}


REGISTRATIONS = (
    ("wsl.run", wsl_run),
    ("wsl.list", wsl_list),
    ("wsl.wslpath", wsl_wslpath),
    ("wsl.status", wsl_status),
)
