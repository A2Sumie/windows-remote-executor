"""process.* — v6 core addition (design §6.1).

Synchronous `process.run` and the async job model
(`process.start/wait/status/kill`). Pure stdlib subprocess; no shell strings
anywhere — args are argv arrays, composed by Python's own quoting rules.

Security notes (design §10.2):
  - `exe` MUST be an absolute path (Windows `C:/...` or UNC `//...`; POSIX
    `/...` on the macOS/Linux loopback). No PATH search — anti PATH-hijack.
  - exe+args go into the rpc audit log as sha256 + full text.
  - Job output is ring-buffered to `WRE_ROOT/jobs/<jobId>.log` (tail kept,
    captureKB bound) with metadata in `<jobId>.json` so a restarted rpc
    process can reap orphans. Job metadata files are guard-protected
    (file.* cannot overwrite them without allowProtected).
  - Identity: everything here runs as the SSH logon user (non-elevated).
    There is deliberately NO runAs/system switch — escalation must go through
    host.task.* and leave an audit trail (design §7).

Cross-platform: the same code drives the macOS loopback test fixtures
(/bin/echo, python3, sleep) and the Windows target.
"""

from __future__ import annotations

import collections
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, IO

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_THIS_DIR))

from version import JOBS_DIR  # noqa: E402
from auditlog import audit_job, audit_job_history, now_iso  # noqa: E402

if os.name == "nt":
    import msvcrt  # noqa: E402
else:
    import fcntl  # noqa: E402

DEFAULT_TIMEOUT_MS = 120_000
DEFAULT_CAPTURE_KB = 256
DEFAULT_RETENTION_HOURS = 24
# H3: a "running" job whose meta has no pids yet may simply be a supervisor
# that has not booted (cold Python start = hundreds of ms). Only reap once
# the meta is older than this grace window (or has a recorded dead pid).
REAP_GRACE_SECONDS = 90
_JOB_ID_RE = re.compile(r"^j-[0-9a-f]{16}$")

# In-process registry: jobId -> {"proc": Popen, "meta": dict, "writer": _RingLog}
_LIVE: dict[str, dict[str, Any]] = {}
_LIVE_LOCK = threading.Lock()
_REGISTRY_LOADED = False


# ---------- validation ----------

def _validate_exe(exe: Any) -> str:
    exe = str(exe or "").strip()
    if not exe:
        raise ValueError("process.* requires payload.exe (absolute path)")
    if os.name == "nt":
        if not (re.match(r"^[A-Za-z]:[/\\]", exe) or exe.startswith("//") or exe.startswith("\\\\")):
            raise ValueError(
                f"exe {exe!r} is not an absolute path; PATH search is disabled "
                "by policy (pass a full path like C:/Windows/System32/cmd.exe)"
            )
        # Normalize to backslashes: CreateProcess tolerates forward slashes,
        # but a child that re-parses GetCommandLine() with '/' as its switch
        # prefix (cmd.exe!) mangles an unquoted forward-slash program path
        # (observed on X570: cmd /d /c echo x -> "syntax of the command is
        # incorrect" until the exe was given as C:\...\cmd.exe).
        exe = exe.replace("/", "\\")
    else:
        if not exe.startswith("/"):
            raise ValueError(f"exe {exe!r} is not an absolute path")
    return exe


def _validate_args(args: Any) -> list[str]:
    if args is None:
        return []
    if isinstance(args, str):
        raise ValueError(
            "args must be a JSON array of strings, not a shell string "
            "(v6 never passes shell text; compose argv explicitly)"
        )
    if not isinstance(args, list):
        raise ValueError("args must be a JSON array of strings")
    return [str(a) for a in args]


def _validate_timeout(payload: dict[str, Any], default_ms: int = DEFAULT_TIMEOUT_MS) -> int:
    raw = payload.get("timeoutMs")
    if raw is None:
        return default_ms
    ms = int(raw)
    if ms <= 0:
        raise ValueError("timeoutMs must be positive")
    return ms


def _capture_cap_bytes(payload: dict[str, Any]) -> int:
    kb = int(payload.get("captureKB") or DEFAULT_CAPTURE_KB)
    return max(1, kb) * 1024


def _merged_env(extra: Any) -> dict[str, str]:
    env = dict(os.environ)
    if isinstance(extra, dict):
        for k, v in extra.items():
            env[str(k)] = str(v)
    return env


# ---------- ring-buffer capture ----------

class _TailBuffer:
    """Bounded tail of a byte stream (deque of chunks)."""

    def __init__(self, cap: int) -> None:
        self.cap = cap
        self.chunks: collections.deque[bytes] = collections.deque()
        self.len = 0
        self.total = 0

    def append(self, data: bytes) -> None:
        if not data:
            return
        self.total += len(data)
        if len(data) > self.cap:
            data = data[-self.cap:]  # a single oversized chunk still keeps its tail
        self.chunks.append(data)
        self.len += len(data)
        while self.len > self.cap and self.chunks:
            excess = self.len - self.cap
            first = self.chunks[0]
            if len(first) <= excess:
                self.len -= len(self.chunks.popleft())
            else:
                # split: keep only the tail of the oldest chunk (exact fill)
                self.chunks[0] = first[excess:]
                self.len -= excess

    def bytes(self) -> bytes:
        out = b"".join(self.chunks)
        if len(out) > self.cap:
            out = out[-self.cap:]
        return out


def _reader_thread(pipe: IO[bytes], sink: "_RingLog | None", tail: _TailBuffer) -> threading.Thread:
    def _run() -> None:
        # BUG-D fix: use read1 (buffered) / os.read (raw) — ONE raw read per
        # call, returning as soon as ANY data is available. The previous
        # pipe.read(65536) on a BufferedReader loops until the buffer is FULL
        # or EOF: a quiet long-running job never fills 64 KiB, so its log
        # stayed 0 bytes for its whole runtime (observed on X570: a 7-day
        # service job with fileBytes=0), and a killed job whose pipe never
        # reaches EOF (a grandchild — e.g. the WSL VM backend — still holds
        # the write end) lost the entire user-space buffer when the daemon
        # reader thread died with the supervisor. With per-chunk arrival the
        # _RingLog (which write+flushes every chunk) makes job output visible
        # while running and durable before a kill lands. captureKB ring
        # semantics are unchanged — only the flush TIMING moved.
        read1 = getattr(pipe, "read1", None)  # BufferedReader only; raw FileIO lacks it
        while True:
            if read1 is not None:
                chunk = read1(65536)
            else:
                chunk = os.read(pipe.fileno(), 65536)
            if not chunk:
                break
            tail.append(chunk)
            if sink is not None:
                sink.write(chunk)
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


class _RingLog:
    """Job log file with ring-buffer semantics: once the file grows past
    2x cap it is compacted to the tail, so disk usage stays bounded."""

    def __init__(self, path: str, cap: int) -> None:
        self.path = path
        self.cap = cap
        self._lock = threading.Lock()
        self._fh: IO[bytes] | None = open(path, "wb")
        self._size = 0
        self._tail = _TailBuffer(cap)

    def write(self, data: bytes) -> None:
        with self._lock:
            self._tail.append(data)
            if self._fh is None:
                return
            try:
                self._fh.write(data)
                self._fh.flush()
                self._size += len(data)
                if self._size > self.cap * 2:
                    self._fh.seek(0)
                    self._fh.truncate()
                    tail = self._tail.bytes()
                    self._fh.write(tail)
                    self._fh.flush()
                    self._size = len(tail)
            except OSError:
                pass

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                try:
                    self._fh.close()
                except OSError:
                    pass
                self._fh = None


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


# ---------- synchronous run ----------

def _spawn(exe: str, args: list[str], cwd: str | None, env: dict[str, str],
           stdin_text: str | None, log: "_RingLog | None") -> "subprocess.Popen[bytes]":
    kwargs: dict[str, Any] = dict(
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if log is not None else subprocess.PIPE,
        stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
        cwd=cwd or None,
        env=env,
    )
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kwargs["start_new_session"] = True  # killpg-able for force kill
    proc: subprocess.Popen[bytes] = subprocess.Popen([exe, *args], **kwargs)
    if stdin_text is not None and proc.stdin is not None:
        try:
            proc.stdin.write(stdin_text.encode("utf-8"))
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
    return proc


def run_capture(exe: str, args: list[str], *, cwd: str | None, stdin_text: str | None,
                env: dict[str, str], timeout_ms: int, capture_kb: int) -> dict[str, Any]:
    """Shared by process.run and wsl.run. Enforces the timeout server-side:
    on expiry the process is killed and the result carries timedOut=true."""
    cap = capture_kb * 1024
    out_tail, err_tail = _TailBuffer(cap), _TailBuffer(cap)
    started = time.perf_counter()
    proc = _spawn(exe, args, cwd, env, stdin_text, None)
    readers = [_reader_thread(proc.stdout, None, out_tail)]  # type: ignore[arg-type]
    if proc.stderr is not None:
        readers.append(_reader_thread(proc.stderr, None, err_tail))
    timed_out = False
    kill_verified = True
    try:
        proc.wait(timeout=timeout_ms / 1000.0)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_verified = _terminate_verified(proc)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
    for r in readers:
        r.join(timeout=5)
    duration_ms = int((time.perf_counter() - started) * 1000)
    stdout_b, stderr_b = out_tail.bytes(), err_tail.bytes()
    result = {
        "exitCode": proc.returncode if proc.returncode is not None else None,
        "stdout": _decode(stdout_b),
        "stderr": _decode(stderr_b),
        "durationMs": duration_ms,
        "timedOut": timed_out,
        "killVerified": kill_verified,
        "stdoutTruncated": out_tail.total > len(stdout_b),
        "stderrTruncated": err_tail.total > len(stderr_b),
        "pid": proc.pid,
    }
    return result


def _terminate(proc: "subprocess.Popen[bytes]", *, force: bool) -> None:
    """Terminate; force=True kills the whole process tree."""
    if proc.poll() is not None:
        return
    if force:
        if os.name == "nt":
            try:
                subprocess.run(
                    [os.path.join(os.environ.get("SystemRoot", "C:/Windows"),
                                  "System32", "taskkill.exe"),
                     "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15,
                )
                return
            except Exception:  # noqa: BLE001
                pass
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                return
            except Exception:  # noqa: BLE001
                pass
    try:
        proc.terminate()
    except Exception:  # noqa: BLE001
        pass


def _terminate_verified(proc: "subprocess.Popen[bytes]", timeout_s: float = 15.0) -> bool:
    """H1: kill and then PROVE it. taskkill can fail silently (denied, race
    with an exiting pid, WSL grandchild holding handles) — a timed-out
    process.run must never return with the child still alive. Retries the
    tree-kill while the pid stays alive within the deadline; returns True
    when the process is confirmed gone."""
    deadline = time.perf_counter() + timeout_s
    _terminate(proc, force=True)
    while time.perf_counter() < deadline:
        if proc.poll() is not None:
            return True
        if not _pid_alive(proc.pid):
            return True
        _terminate(proc, force=True)  # retry (first attempt may have raced)
        time.sleep(0.25)
    return proc.poll() is not None or not _pid_alive(proc.pid)


# ---------- job model (process.start/wait/status/kill) ----------
#
# Architecture: every job is supervised by a DETACHED per-job monitor process
# (the same interpreter running `rpc.py job-run --spec <file>`). The rpc
# process is allowed to exit right after answering process.start — the
# supervisor owns the child's pipes, the ring-buffered log file, timeout
# enforcement, and the final metadata write. rpc restarts therefore lose
# NOTHING: status/wait read the on-disk metadata; kill targets the recorded
# child pid (tree-kill with force).
#
# (The v6.0 draft supervised jobs with in-rpc threads; that failed on the
# first one-shot call — rpc exits at stdin EOF, the reader thread died, the
# child's pipe broke and no output was captured. X570 verify caught it.)

def _jobs_dir() -> str:
    os.makedirs(JOBS_DIR.replace("/", os.sep), exist_ok=True)
    return JOBS_DIR


def _meta_path(job_id: str) -> str:
    return f"{_jobs_dir()}/{job_id}.json".replace("/", os.sep)


def _log_path(job_id: str) -> str:
    return f"{_jobs_dir()}/{job_id}.log".replace("/", os.sep)


def _spec_path(job_id: str) -> str:
    return f"{_jobs_dir()}/{job_id}.spec.json".replace("/", os.sep)


def _write_meta(meta: dict[str, Any]) -> None:
    try:
        tmp = _meta_path(meta["jobId"]) + f".tmp-{os.getpid()}"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, _meta_path(meta["jobId"]))
    except OSError:
        pass


import contextlib  # noqa: E402

from auditlog import JOBS_HISTORY_TAIL_BYTES  # noqa: E402


@contextlib.contextmanager
def _meta_lock(job_id: str):
    """Cross-process per-job metadata mutex. EVERY read-modify-write of a
    job's meta file (start/kill/supervisor/reaper) must hold it: the rpc
    process and the detached supervisor both write the same file, and
    unprotected RMW cycles lose each other's fields (NIT-1 completion — the
    kill flag and supervisorPid were clobbered by stale-snapshot writes;
    reproduced on macOS 5/8 iterations)."""
    path = f"{_jobs_dir()}/{job_id}.lock".replace("/", os.sep)
    with open(path, "a+b") as fh:
        if os.name == "nt":
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _update_meta(job_id: str, updates: dict[str, Any],
                 fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    """Atomically merge `updates` onto the on-disk meta under _meta_lock.
    Returns the merged meta."""
    with _meta_lock(job_id):
        try:
            cur = _read_meta(job_id)
        except (FileNotFoundError, ValueError):
            cur = dict(fallback) if fallback else {"jobId": job_id}
        cur.update(updates)
        _write_meta(cur)
        return cur


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            try:
                code = ctypes.c_ulong(0)
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return code.value == 259  # STILL_ACTIVE
                return False
            finally:
                kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _meta_started_age_s(meta: dict[str, Any]) -> float | None:
    """Seconds since the job's startedAt, or None when unparsable."""
    try:
        started_dt = datetime.fromisoformat(str(meta.get("startedAt")))
        return (datetime.now(timezone.utc) - started_dt).total_seconds()
    except (ValueError, TypeError):
        return None


def _looks_abandoned(meta: dict[str, Any]) -> bool:
    """H3: a running job is only 'abandoned' when we have positive evidence —
    recorded pids that are dead, OR a meta older than REAP_GRACE_SECONDS with
    no pids at all (supervisor never wrote them). A fresh meta in the
    supervisor boot window is left alone."""
    sup_pid = int(meta.get("supervisorPid") or 0)
    child_pid = int(meta.get("pid") or 0)
    if sup_pid or child_pid:
        return not _pid_alive(sup_pid) and not _pid_alive(child_pid)
    age = _meta_started_age_s(meta)
    return age is not None and age > REAP_GRACE_SECONDS


def _load_registry() -> None:
    """Reap orphan job metadata left by dead supervisors + enforce retention
    (default 24h) on exited jobs."""
    global _REGISTRY_LOADED
    with _LIVE_LOCK:
        if _REGISTRY_LOADED:
            return
        _REGISTRY_LOADED = True
    try:
        entries = [e for e in os.listdir(_jobs_dir())
                   if e.endswith(".json") and not e.endswith(".spec.json")]
    except OSError:
        return
    cutoff = time.time() - DEFAULT_RETENTION_HOURS * 3600
    for entry in entries:
        path = os.path.join(_jobs_dir(), entry)
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                meta = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        job_id = str(meta.get("jobId") or entry[:-5])
        if meta.get("state") == "running":
            if _looks_abandoned(meta):
                with _meta_lock(job_id):
                    try:
                        fresh = _read_meta(job_id)
                    except (FileNotFoundError, ValueError):
                        continue
                    if fresh.get("state") != "running":
                        continue  # a terminal write landed in between
                    if not _looks_abandoned(fresh):
                        continue  # pids appeared under the lock — it was booting
                    fresh["state"] = "exited"
                    fresh["exitCode"] = None
                    fresh["endedAt"] = now_iso()
                    fresh["note"] = "reaped: supervisor gone before final write"
                    _write_meta(fresh)
                audit_job("reap", jobId=job_id, supervisorPid=int(meta.get("supervisorPid") or 0),
                           pid=int(meta.get("pid") or 0))
        else:
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    for suffix in (".log", ".spec.json", ".lock"):
                        extra = os.path.join(_jobs_dir(), job_id + suffix)
                        if os.path.isfile(extra):
                            os.remove(extra)
                    audit_job("retention-cleanup", jobId=job_id)
            except OSError:
                pass


def job_run_main(spec_path: str) -> int:
    """Detached per-job supervisor (subcommand `job-run`). Owns the child
    process, the log, the timeout, and the final metadata write."""
    with open(spec_path, "r", encoding="utf-8-sig") as fh:
        spec = json.load(fh)
    meta: dict[str, Any] = spec["meta"]
    job_id = meta["jobId"]
    cap = int(meta.get("captureKB") or DEFAULT_CAPTURE_KB) * 1024
    timeout_ms = spec.get("timeoutMs")
    log = _RingLog(_log_path(job_id), cap)
    started = time.perf_counter()
    timed_out = False
    exit_code: int | None = None
    error = ""
    try:
        proc = subprocess.Popen(
            [spec["exe"], *spec["args"]],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE if spec.get("stdinText") is not None else subprocess.DEVNULL,
            cwd=spec.get("cwd") or None,
            env=spec.get("env") or None,
            creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0),
            start_new_session=(os.name != "nt"),
        )
        meta["pid"] = proc.pid
        # child pid now on disk for cross-process kill — merge under the
        # per-job lock so process.start's supervisorPid survives (the
        # spec-snapshot meta this function started from predates that write)
        meta = _update_meta(job_id, {"pid": proc.pid}, fallback=meta)
        if spec.get("stdinText") is not None and proc.stdin is not None:
            try:
                proc.stdin.write(str(spec["stdinText"]).encode("utf-8"))
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        reader = _reader_thread(proc.stdout, log, _TailBuffer(1))  # type: ignore[arg-type]
        try:
            proc.wait(timeout=(timeout_ms / 1000.0) if timeout_ms else None)
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate(proc, force=True)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        reader.join(timeout=10)
        exit_code = proc.returncode
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    log.close()
    # NIT-1 completion: the terminal write merges under the per-job lock.
    # A blind write of the in-memory meta (a spec-time snapshot) clobbered
    # supervisorPid (-> reapers misread the job as orphaned, exitCode=None)
    # and, whenever process.kill's write landed inside this supervisor's
    # read-modify-write window, the killed/killedAt flag (lost update).
    # Fields owned by other writers survive; this function owns the
    # terminal fields.
    updates: dict[str, Any] = {
        "exitCode": exit_code,
        "endedAt": now_iso(),
        "durationMs": int((time.perf_counter() - started) * 1000),
    }
    if timed_out:
        updates["timedOut"] = True
    if error:
        updates["error"] = error
    with _meta_lock(job_id):
        try:
            fresh = _read_meta(job_id)
        except (FileNotFoundError, ValueError):
            fresh = meta
        for key in ("killed", "killedAt", "supervisorPid", "pid"):
            if fresh.get(key) is not None:
                meta[key] = fresh[key]
        meta.update(updates)
        meta["state"] = "killed" if meta.get("killed") else "exited"
        _write_meta(meta)
    try:
        os.remove(spec_path)
    except OSError:
        pass
    # H14: durable history line (job log itself expires with retention).
    # (2026-08-25 nuc live-run caught a NameError here — the bare except
    # swallowed a wrong constant name and shipped an empty logTail; the
    # except is now OSError-only so a bug like that CRASHES the supervisor
    # loudly instead of silently losing the record.)
    try:
        log_tail = _decode(_log_tail_bytes(job_id, JOBS_HISTORY_TAIL_BYTES))
    except OSError:
        log_tail = ""
    audit_job_history(meta, log_tail)
    audit_job("exit", jobId=job_id, pid=meta.get("pid"), exitCode=exit_code,
              timedOut=timed_out, error=error)
    return 0


def _read_meta(job_id: str) -> dict[str, Any]:
    path = _meta_path(job_id)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"unknown jobId: {job_id} (no {_meta_path(job_id)})")
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise ValueError(f"corrupt job metadata for {job_id}: {exc}") from exc


def _log_tail_bytes(job_id: str, cap: int) -> bytes:
    path = _log_path(job_id)
    if not os.path.isfile(path):
        return b""
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        if size > cap:
            fh.seek(-cap, os.SEEK_END)
        return fh.read(cap)


def _check_job_id(payload: dict[str, Any]) -> str:
    job_id = str(payload.get("jobId") or "")
    if not _JOB_ID_RE.match(job_id):
        raise ValueError("jobId must look like j-<16 hex chars>")
    return job_id


# ---------- action handlers ----------

_SHELL_WRAPPER_EXES = {"bash", "sh", "zsh", "wsl", "cmd", "powershell", "pwsh"}
_SHELL_WRAPPER_FLAGS = {"-c", "-lc", "-command", "/c", "/k"}


def _shell_wrapper_note(exe: str, args: list[str]) -> str:
    """Advisory attached to job metadata when the spawned argv is a shell
    composite (BUG-B): a job's exitCode is faithfully the spawned process's
    own exit status — but for `bash -lc "a; b"` (and cmd /c, wsl.exe shell
    form) that status is only the LAST command's, so an inner failure is
    masked whenever the composite ends in a succeeding command (e.g. a
    trailing `echo EXIT=$?`). Direct-exe jobs need no note: their exitCode
    is the program's own."""
    base = exe.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if base.endswith(".exe"):
        base = base[:-4]
    if base not in _SHELL_WRAPPER_EXES:
        return ""
    if not ({a.lower() for a in args} & _SHELL_WRAPPER_FLAGS):
        return ""
    return ("exitCode is the shell wrapper's own status: ';'-joined commands "
            "report only the LAST command, masking earlier failures. Use "
            "'set -e' or capture+re-exit (cmd; rc=$?; echo EXIT=$rc; exit $rc) "
            "so job exitCode reflects the real result.")


def process_run(payload: dict[str, Any]) -> dict[str, Any]:
    exe = _validate_exe(payload.get("exe"))
    args = _validate_args(payload.get("args"))
    timeout_ms = _validate_timeout(payload)
    capture_kb = int(payload.get("captureKB") or DEFAULT_CAPTURE_KB)
    audit_job("run", exe=exe, argsText=json.dumps([exe, *args], ensure_ascii=False),
              argsSha256=hashlib.sha256(json.dumps([exe, *args]).encode("utf-8")).hexdigest())
    result = run_capture(
        exe, args,
        cwd=str(payload.get("cwd") or "") or None,
        stdin_text=payload.get("stdinText"),
        env=_merged_env(payload.get("env")),
        timeout_ms=timeout_ms,
        capture_kb=capture_kb,
    )
    return {"data": result, "stdout_text": result["stdout"],
            "evidence_extra": ["process.run"]}


def process_start(payload: dict[str, Any]) -> dict[str, Any]:
    _load_registry()
    exe = _validate_exe(payload.get("exe"))
    args = _validate_args(payload.get("args"))
    capture_kb = int(payload.get("captureKB") or DEFAULT_CAPTURE_KB)
    timeout_ms = None
    if payload.get("timeoutMs") is not None:
        timeout_ms = _validate_timeout(payload)

    job_id = f"j-{uuid.uuid4().hex[:16]}"
    meta: dict[str, Any] = {
        "jobId": job_id,
        "pid": None,
        "supervisorPid": None,
        "exe": exe,
        "args": args,
        "argsSha256": hashlib.sha256(json.dumps([exe, *args]).encode("utf-8")).hexdigest(),
        "cwd": str(payload.get("cwd") or ""),
        "startedAt": now_iso(),
        "state": "running",
        "exitCode": None,
        "outputPath": _log_path(job_id).replace("\\", "/"),
        "captureKB": capture_kb,
        "timeoutMs": timeout_ms,
    }
    wrapper_note = _shell_wrapper_note(exe, args)
    if wrapper_note:
        meta["exitCodeNote"] = wrapper_note
    spec = {
        "meta": meta,
        "exe": exe,
        "args": args,
        "cwd": meta["cwd"],
        "env": _merged_env(payload.get("env")),
        "stdinText": payload.get("stdinText"),
        "timeoutMs": timeout_ms,
    }
    spec_path = _spec_path(job_id)
    with open(spec_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(spec, fh, ensure_ascii=False)

    # Detached supervisor: no inherited stdio (an inherited pipe would pin the
    # calling ssh session open), its own session/console so it outlives rpc.
    # CREATE_BREAKAWAY_FROM_JOB (0x01000000) is REQUIRED on Windows:
    # sshd-win32 places the session in a Job Object and kills the tree at
    # session close — without breakaway the supervisor dies with the session
    # (observed on X570; flag verified working there).
    rpc_path = os.path.join(os.path.dirname(_THIS_DIR), "rpc.py")
    sup_argv = [sys.executable, "-I", "-X", "utf8", rpc_path, "job-run", "--spec", spec_path]
    popen_kw: dict[str, Any] = dict(
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    breakaway_note = ""
    if os.name == "nt":
        flags = (getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
                 | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
        try:
            supervisor = subprocess.Popen(
                sup_argv, creationflags=flags | 0x01000000, **popen_kw)
        except OSError:
            # Job object without breakaway permission: degrade with a warning
            # (the job then only outlives rpc while a session stays connected).
            supervisor = subprocess.Popen(sup_argv, creationflags=flags, **popen_kw)
            breakaway_note = ("warning: CREATE_BREAKAWAY_FROM_JOB denied; job is "
                              "tied to the spawning ssh session")
    else:
        supervisor = subprocess.Popen(sup_argv, start_new_session=True, **popen_kw)
    meta = _update_meta(job_id, {"supervisorPid": supervisor.pid},
                        fallback=meta)
    audit_job("start", jobId=job_id, supervisorPid=supervisor.pid, exe=exe,
              argsText=json.dumps([exe, *args], ensure_ascii=False),
              argsSha256=hashlib.sha256(json.dumps([exe, *args]).encode("utf-8")).hexdigest())
    data = {"jobId": job_id, "supervisorPid": supervisor.pid,
            "outputPath": meta["outputPath"]}
    if breakaway_note:
        data["warning"] = breakaway_note
    return {"data": data, "stdout_text": "", "evidence_extra": ["process.start"]}


def _job_view(job_id: str) -> dict[str, Any]:
    meta = _read_meta(job_id)
    if meta.get("state") == "running" and _looks_abandoned(meta):
        sup = int(meta.get("supervisorPid") or 0)
        child = int(meta.get("pid") or 0)
        with _meta_lock(job_id):
            try:
                fresh = _read_meta(job_id)
            except (FileNotFoundError, ValueError):
                fresh = meta
            if fresh.get("state") == "running" and _looks_abandoned(fresh):
                fresh["state"] = "exited"
                fresh["exitCode"] = None
                fresh["endedAt"] = now_iso()
                fresh["note"] = "reaped: supervisor gone before final write"
                _write_meta(fresh)
            meta = fresh  # a terminal write may have landed in between
    if meta.get("state") == "running":
        try:
            started_dt = datetime.fromisoformat(str(meta.get("startedAt")))
            meta["durationMs"] = int(
                (datetime.now(timezone.utc) - started_dt).total_seconds() * 1000)
        except (ValueError, TypeError):
            pass
    log = _log_path(job_id)
    meta["outputSizeBytes"] = os.path.getsize(log) if os.path.isfile(log) else 0
    meta["outputPath"] = log.replace("\\", "/")
    # NIT-1: a kill flag that landed after the supervisor's final write still
    # means the child was killed — report state "killed", not "exited", so
    # status/wait can distinguish "killed" from "died on its own". Surface
    # `killed` unconditionally so clients never have to guess field presence.
    if meta.get("killed"):
        if meta.get("state") == "exited":
            meta["state"] = "killed"
    else:
        meta["killed"] = False
    return meta


def process_status(payload: dict[str, Any]) -> dict[str, Any]:
    _load_registry()
    job_id = _check_job_id(payload)
    meta = _job_view(job_id)
    return {"data": meta, "stdout_text": json.dumps(meta, ensure_ascii=False),
            "evidence_extra": ["process.status"]}


def process_wait(payload: dict[str, Any]) -> dict[str, Any]:
    _load_registry()
    job_id = _check_job_id(payload)
    timeout_ms = _validate_timeout(payload, default_ms=60_000)
    tail_kb = int(payload.get("tailKB") or 32)
    deadline = time.perf_counter() + timeout_ms / 1000.0
    while time.perf_counter() < deadline:
        if _read_meta(job_id).get("state") != "running":
            break
        time.sleep(0.25)
    meta = _job_view(job_id)
    tail = _decode(_log_tail_bytes(job_id, tail_kb * 1024))
    data = {
        "jobId": job_id,
        "state": meta.get("state"),
        "exitCode": meta.get("exitCode"),
        "durationMs": meta.get("durationMs"),
        "timedOut": bool(meta.get("timedOut")),
        "killed": bool(meta.get("killed")),
        "stillRunning": meta.get("state") == "running",
        "logTail": tail,
        "outputPath": meta.get("outputPath"),
    }
    if meta.get("killedAt"):
        data["killedAt"] = meta["killedAt"]
    if meta.get("exitCodeNote"):
        data["exitCodeNote"] = meta["exitCodeNote"]
    return {"data": data, "stdout_text": tail, "evidence_extra": ["process.wait"]}


def process_kill(payload: dict[str, Any]) -> dict[str, Any]:
    _load_registry()
    job_id = _check_job_id(payload)
    force = bool(payload.get("force"))
    meta = _read_meta(job_id)
    sup_pid = int(meta.get("supervisorPid") or 0)
    child_pid = int(meta.get("pid") or 0)
    was_running = meta.get("state") == "running" and (
        _pid_alive(child_pid) or _pid_alive(sup_pid))
    # The supervisor writes the child pid a beat after spawn; give it a short
    # window so an immediate kill still reaches the child.
    if was_running and not child_pid:
        wait_deadline = time.perf_counter() + 2.0
        while time.perf_counter() < wait_deadline:
            time.sleep(0.1)
            meta = _read_meta(job_id)
            child_pid = int(meta.get("pid") or 0)
            if child_pid:
                break
    kill_error = ""
    if was_running and child_pid:
        try:
            if os.name == "nt":
                subprocess.run(
                    [os.path.join(os.environ.get("SystemRoot", "C:/Windows"),
                                  "System32", "taskkill.exe"),
                     "/PID", str(child_pid), *(["/T", "/F"] if force else ["/F"])],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
            else:
                if force:
                    os.killpg(os.getpgid(child_pid), signal.SIGKILL)
                else:
                    os.kill(child_pid, signal.SIGTERM)
        except Exception as exc:  # noqa: BLE001
            kill_error = str(exc)
    # NIT-1 fix (completed): record the kill as first-class metadata
    # (`killed` + `killedAt`), merged atomically under the per-job lock.
    # The detached supervisor (job_run_main) writes its final meta while the
    # kill is in flight; an unprotected read-modify-write on either side lost
    # the other's fields — observed on X570 as state=exited, exitCode=null
    # after a successful kill, and reproduced on macOS with the killed flag
    # itself clobbered by the supervisor's terminal write.
    fresh = _update_meta(job_id, {"killed": True, "killedAt": now_iso()},
                         fallback=meta)
    audit_job("kill", jobId=job_id, force=force, wasRunning=was_running,
              childPid=child_pid, error=kill_error)
    if kill_error:
        raise RuntimeError(f"kill of job {job_id} (pid {child_pid}) failed: {kill_error}")
    return {"data": {"jobId": job_id, "killSent": was_running, "force": force,
                     "wasRunning": was_running},
            "stdout_text": "", "evidence_extra": ["process.kill"]}


REGISTRATIONS = (
    ("process.run", process_run),
    ("process.start", process_start),
    ("process.wait", process_wait),
    ("process.status", process_status),
    ("process.kill", process_kill),
)
