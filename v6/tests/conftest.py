"""Shared fixtures for the WRE v6 test suite.

Two import roots are set up here:
  - REPO_ROOT  -> `v6.controller.*`, `v6.mcp.*` (controller side, runs on macOS)
  - NATIVE_DIR -> `rpc`, `schemas`, `actions.*`, `win32.*` (the native tree,
                  pure stdlib; pywin32/comtypes imports are lazy so every
                  module imports cleanly off-Windows)

Zero-network contract: no test may open a socket or contact X570/nuc-8-sumie.
The native rpc loop is exercised as a local stdio subprocess (pipes only);
the controller transport is replaced by a scripted FakePopen.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

TESTS_DIR = Path(__file__).resolve().parent
V6_DIR = TESTS_DIR.parent
REPO_ROOT = V6_DIR.parent
NATIVE_DIR = V6_DIR / "native"

for _p in (str(REPO_ROOT), str(NATIVE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from v6.controller import targets as tgt


@pytest.fixture()
def fake_target() -> tgt.Target:
    """A Target that is never connected to anything (transport is mocked)."""
    return tgt.Target(
        name="TEST",
        env_file=Path("/nonexistent/TEST.env"),
        host="192.0.2.1",  # TEST-NET-1, unroutable by convention
        user="tester",
        port="22",
        key=None,
        access_token="test-token",
        expected_listen_address=None,
        exposure_mode="private-only",
    )


# ---------- scripted FakePopen for v6.controller.client.Session ----------

class FakeStream:
    """Scripted text pipe. readline() pops scripted lines; write() can be
    armed to raise BrokenPipeError a given number of times."""

    def __init__(self, lines: list[str] | None = None, broken_writes: int = 0,
                 read_exception: BaseException | None = None) -> None:
        self._lines = list(lines or [])
        self.broken_writes = broken_writes
        self.read_exception = read_exception
        self.writes: list[str] = []
        self.closed = False

    # stdin side
    def write(self, data: str) -> int:
        if self.closed:
            raise BrokenPipeError("write to closed FakeStream")
        if self.broken_writes > 0:
            self.broken_writes -= 1
            raise BrokenPipeError("scripted broken pipe")
        self.writes.append(data)
        return len(data)

    def flush(self) -> None:
        if self.closed:
            raise BrokenPipeError("flush of closed FakeStream")

    def close(self) -> None:
        self.closed = True

    # stdout side
    def readline(self) -> str:
        if self.read_exception is not None:
            raise self.read_exception
        if self._lines:
            return self._lines.pop(0)
        return ""  # EOF

    def read(self) -> str:
        rest = "".join(self._lines)
        self._lines.clear()
        return rest


class FakePopen:
    """Minimal stand-in for subprocess.Popen[str] used by client.Session."""

    def __init__(self, argv: list[str], *, script: dict[str, Any],
                 kwargs: dict[str, Any] | None = None) -> None:
        self.argv = argv
        self.popen_kwargs = dict(kwargs or {})
        self.stdin = FakeStream(broken_writes=script.get("broken_writes", 0))
        self.stdout = FakeStream(lines=script.get("stdout_lines", []),
                                 read_exception=script.get("read_exception"))
        # honor stderr=None like the real Popen (stderr_echo sessions)
        if self.popen_kwargs.get("stderr", "unset") is None:
            self.stderr = None
        else:
            self.stderr = FakeStream(lines=script.get("stderr_lines", []))
        self._returncode: int | None = script.get("returncode")
        self._wait_raises = script.get("wait_raises", 0)
        self.killed = False
        self.wait_calls = 0
        self.wait_timeouts: list[float | None] = []

    def poll(self) -> int | None:
        return self._returncode

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls += 1
        self.wait_timeouts.append(timeout)
        if self._wait_raises > 0:
            self._wait_raises -= 1
            raise RuntimeError("scripted stubborn wait")
        if self._returncode is None:
            self._returncode = 0
        return self._returncode

    def kill(self) -> None:
        self.killed = True
        self._returncode = -9


class FakePopenFactory:
    """Patches client.subprocess.Popen; each Session._open() pops one script."""

    def __init__(self, scripts: list[dict[str, Any]]) -> None:
        self._scripts = list(scripts)
        self.spawned: list[FakePopen] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> FakePopen:
        script = self._scripts.pop(0) if self._scripts else {}
        proc = FakePopen(argv, script=script, kwargs=kwargs)
        self.spawned.append(proc)
        return proc


@pytest.fixture()
def popen_patcher(monkeypatch: pytest.MonkeyPatch):
    def _install(scripts: list[dict[str, Any]]) -> FakePopenFactory:
        from v6.controller import client
        factory = FakePopenFactory(scripts)
        monkeypatch.setattr(client.subprocess, "Popen", factory)
        return factory
    return _install


# ---------- native rpc loop as a real local stdio subprocess ----------

class NativeRpc:
    """Drives v6/native/rpc.py rpc-stdio over plain pipes (no network)."""

    def __init__(self, proc: subprocess.Popen[str]) -> None:
        self.proc = proc

    def send_line(self, line: str) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()

    def send_json(self, obj: Any) -> None:
        self.send_line(json.dumps(obj, ensure_ascii=False))

    def read_response(self, timeout_s: float = 30.0) -> dict[str, Any]:
        assert self.proc.stdout is not None
        import queue
        import threading
        q: queue.Queue[str] = queue.Queue()

        def _read() -> None:
            q.put(self.proc.stdout.readline())  # type: ignore[union-attr]

        t = threading.Thread(target=_read, daemon=True)
        t.start()
        t.join(timeout=timeout_s)
        if t.is_alive():
            raise TimeoutError("native rpc did not answer in time")
        line = q.get()
        assert line, f"native rpc closed stdout unexpectedly (poll={self.proc.poll()})"
        return json.loads(line)

    def exchange(self, obj: Any) -> dict[str, Any]:
        self.send_json(obj)
        return self.read_response()

    def alive(self) -> bool:
        return self.proc.poll() is None


@pytest.fixture()
def native_rpc(tmp_path: Path):
    """Function-scoped native rpc loop with an isolated WRE_ROOT."""
    env = dict(os.environ)
    env["WRE_ROOT"] = str(tmp_path / "wre-root")
    env["PYTHONUTF8"] = "1"
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", str(NATIVE_DIR / "rpc.py"), "rpc-stdio"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", env=env,
    )
    rpc = NativeRpc(proc)
    yield rpc
    try:
        if rpc.alive() and proc.stdin:
            proc.stdin.close()
            proc.wait(timeout=10)
    except Exception:  # noqa: BLE001, S110 - teardown best-effort
        pass
    if rpc.alive():
        proc.kill()
        proc.wait(timeout=10)


@pytest.fixture()
def tmp_jobs_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Points the native process action module's JOBS_DIR at a tmp dir and
    exports WRE_ROOT so any spawned supervisor subprocess agrees."""
    import actions.process as proc_mod
    jobs = tmp_path / "jobs"
    monkeypatch.setattr(proc_mod, "JOBS_DIR", str(jobs))
    monkeypatch.setenv("WRE_ROOT", str(tmp_path))
    return jobs
