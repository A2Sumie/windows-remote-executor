"""Second coverage-fill batch: auditlog rotation, the oversized-line drain,
process terminate/pid-alive paths, and rpc.main rpc-stdio in-process."""

from __future__ import annotations

import io
import json
import os
import signal

import actions.process as proc
import auditlog
import pytest
import rpc as native_rpc

# ---------- auditlog (rotation is pure stdlib; only the nt gate is Windows) ----------

def test_rotate_if_large(tmp_path):
    log = tmp_path / "audit.log"
    log.write_bytes(b"x" * 100)
    auditlog._rotate_if_large(str(log), 50)
    assert not log.exists()
    assert (tmp_path / "audit.log.1").stat().st_size == 100
    # second rotation drops the oldest generation
    log.write_bytes(b"y" * 80)
    auditlog._rotate_if_large(str(log), 50)
    assert (tmp_path / "audit.log.1").read_bytes() == b"y" * 80
    assert not log.exists()  # moved aside again
    # under the limit: untouched
    log.write_bytes(b"z" * 10)
    auditlog._rotate_if_large(str(log), 500)
    assert log.read_bytes() == b"z" * 10


def test_append_jsonl_and_missing_file_rotation(tmp_path):
    log = tmp_path / "a.log"
    auditlog._append_jsonl(str(log), {"k": "雪"})
    assert json.loads(log.read_text().strip()) == {"k": "雪"}
    # rotating a nonexistent file is a no-op, not an error
    auditlog._rotate_if_large(str(tmp_path / "gone.log"), 1)


def test_audit_write_noop_off_windows(monkeypatch):
    monkeypatch.setattr(auditlog.os, "name", "posix")
    auditlog.write_audit({"ts": "x"})  # must not raise or write


# ---------- oversized line drain ----------

def test_drain_oversized_line_consumes_remainder(monkeypatch):
    rest = "A" * 5000 + "\n" + '{"next": true}\n'
    monkeypatch.setattr(native_rpc.sys, "stdin", io.StringIO(rest))
    native_rpc._drain_oversized_line()
    assert native_rpc.sys.stdin.readline() == '{"next": true}\n'


def test_drain_oversized_line_bounded_on_endless_line(monkeypatch):
    # a peer that never sends a newline: the drain gives up at the cap
    # instead of hanging the loop
    class _Endless:
        def __init__(self):
            self.calls = 0

        def readline(self, n=-1):
            self.calls += 1
            return "A" * n  # never a newline

    endless = _Endless()
    monkeypatch.setattr(native_rpc.sys, "stdin", endless)
    native_rpc._drain_oversized_line()
    expected = native_rpc.MAX_REQUEST_LINE_BYTES // (1024 * 1024)
    assert endless.calls == expected  # 56 one-MB reads, then stop


# ---------- _terminate / _pid_alive (posix branch) ----------

class _FakeProc:
    def __init__(self, alive=True):
        self._alive = alive
        self.pid = os.getpid()
        self.terminated = False

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated = True
        self._alive = False


@pytest.mark.skipif(os.name == "nt", reason="posix killpg branch")
def test_terminate_force_uses_killpg(monkeypatch):
    calls = []
    monkeypatch.setattr(proc.os, "killpg", lambda pgid, sig: calls.append((pgid, sig)))
    monkeypatch.setattr(proc.os, "getpgid", lambda pid: 4242)
    p = _FakeProc(alive=True)
    proc._terminate(p, force=True)
    assert calls == [(4242, signal.SIGKILL)]


def test_terminate_soft_and_already_dead():
    p = _FakeProc(alive=True)
    proc._terminate(p, force=False)
    assert p.terminated is True
    dead = _FakeProc(alive=False)
    proc._terminate(dead, force=True)  # no-op, no raise
    assert dead.terminated is False


def test_pid_alive_self_and_dead():
    assert proc._pid_alive(os.getpid()) is True
    assert proc._pid_alive(0) is False
    assert proc._pid_alive(-5) is False
    assert proc._pid_alive(2 ** 24) is False  # no such pid


# ---------- rpc.main rpc-stdio in-process ----------

def test_main_rpc_stdio_in_process(monkeypatch):
    monkeypatch.setattr(native_rpc.sys, "argv", ["rpc.py", "rpc-stdio"])
    monkeypatch.setattr(native_rpc.sys, "stdin",
                        io.StringIO('{"action":"host.capabilities","id":"m"}\n'))
    out = io.StringIO()
    monkeypatch.setattr(native_rpc.sys, "stdout", out)
    assert native_rpc.main() == 0
    resp = json.loads(out.getvalue().strip())
    assert resp["ok"] is True and resp["id"] == "m"


def test_main_help_and_job_run_without_spec(monkeypatch, capsys):
    monkeypatch.setattr(native_rpc.sys, "argv", ["rpc.py"])
    assert native_rpc.main() == 0
    monkeypatch.setattr(native_rpc.sys, "argv", ["rpc.py", "job-run"])
    assert native_rpc.main() == 2
    assert "job-run requires --spec" in capsys.readouterr().err
