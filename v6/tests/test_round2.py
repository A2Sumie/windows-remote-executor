"""Round-2 regression tests (fresh-eyes review findings):
  - duplicate-submission guard: read-phase transport failures must NOT
    auto-resend (the peer may have processed the request); write-phase
    failures still auto-reconnect+resend exactly once
  - session close reaps a stubborn child (no zombie)
  - UNC path head collapse (Windows path edge)
  - putBinary strict base64 (silent corruption guard)
  - writeText text type check
"""

from __future__ import annotations

import base64
import json

import pytest

from v6.controller import client as rpc


def _ok_line(data: dict | None = None) -> str:
    return json.dumps({"ok": True, "data": data or {}}) + "\n"


# ---------- duplicate-submission guard ----------

def test_read_phase_eof_does_not_auto_resend(fake_target, popen_patcher):
    factory = popen_patcher([
        {"stdout_lines": [], "stderr_lines": ["gone"], "returncode": 1},
        {"stdout_lines": [_ok_line({"retry": "manual"})]},
    ])
    sess = rpc.Session(fake_target)
    with pytest.raises(rpc.RpcTransportError, match="closed by peer"):
        sess.call("process.start", {"exe": "C:/x"})
    # NO automatic second submission of a non-idempotent action...
    assert len(factory.spawned) == 1
    assert len(factory.spawned[0].stdin.writes) == 1
    # ...but the broken session was dropped, so a deliberate retry reconnects
    call = sess.call("process.start", {"exe": "C:/x"})
    assert call.ok is True and call.data == {"retry": "manual"}
    assert len(factory.spawned) == 2
    sess.close()


def test_read_phase_exception_does_not_auto_resend(fake_target, popen_patcher):
    factory = popen_patcher([
        {"read_exception": OSError("pipe tore")},
    ])
    sess = rpc.Session(fake_target)
    with pytest.raises(rpc.RpcTransportError, match="read failed"):
        sess.call("file.putBinary", {"path": "C:/f", "base64": "AA=="})
    assert len(factory.spawned) == 1
    assert sess._proc is None  # session dropped for a clean next call
    sess.close()


def test_read_deadline_does_not_auto_resend(fake_target, popen_patcher, monkeypatch):
    factory = popen_patcher([{}])

    class _StuckThread:
        def __init__(self, target=None, daemon=None):
            pass

        def start(self):
            pass

        def join(self, timeout=None):
            pass

        def is_alive(self):
            return True

    monkeypatch.setattr(rpc.threading, "Thread", _StuckThread)
    sess = rpc.Session(fake_target)
    sess._open()
    with pytest.raises(rpc.RpcTransportError, match="read timed out"):
        sess.call("process.start", {"exe": "C:/x"}, timeout_ms=1000)
    assert len(factory.spawned) == 1  # deadline => close+raise, no blind retry
    sess.close()


def test_write_phase_failure_still_auto_resends_once(fake_target, popen_patcher):
    factory = popen_patcher([
        {"broken_writes": 1},
        {"stdout_lines": [_ok_line({"ok": 1})]},
    ])
    sess = rpc.Session(fake_target)
    call = sess.call("process.start", {"exe": "C:/x"})
    assert call.ok is True  # broken-pipe write = provable non-delivery
    assert len(factory.spawned) == 2
    sess.close()


def test_write_error_class_is_transport_subclass(fake_target, popen_patcher):
    assert issubclass(rpc.RpcTransportWriteError, rpc.RpcTransportError)
    popen_patcher([{"broken_writes": 5}, {"broken_writes": 5}])
    sess = rpc.Session(fake_target)
    with pytest.raises(rpc.RpcTransportWriteError):
        sess.call("host.info")  # write fails, reconnect, write fails again
    sess.close()


# ---------- zombie reaping on close ----------

def test_close_kills_and_reaps_stubborn_proc(fake_target, popen_patcher):
    factory = popen_patcher([{"wait_raises": 1}])
    sess = rpc.Session(fake_target)
    sess._open()
    proc = factory.spawned[0]
    sess.close()
    assert proc.killed is True
    assert proc.wait_calls == 2  # kill followed by a reaping wait


# ---------- UNC path edge ----------

def test_norm_unc_head_collapse_exactly_one_pair():
    from actions import files
    assert files._norm("\\\\server\\share") == "\\\\server\\share"
    assert files._norm("\\\\\\\\server\\\\share") == "\\\\server\\share"
    assert files._norm("\\\\\\\\\\server") == "\\\\server"
    # forward-slash UNC keeps passing through verbatim
    assert files._norm("//server/share") == "//server/share"


# ---------- putBinary strict base64 ----------

def test_putbinary_rejects_whitespace_and_junk(tmp_path):
    from actions import files
    with pytest.raises(Exception):  # noqa: B017 - binascii.Error, a ValueError
        files.put_binary({"path": str(tmp_path / "f"), "base64": "QUJD RAQ="})
    with pytest.raises(ValueError, match="base64 is required"):
        files.put_binary({"path": str(tmp_path / "f"), "base64": ["not", "str"]})


def test_putbinary_valid_base64_still_accepted(tmp_path):
    from actions import files
    out = files.put_binary({"path": str(tmp_path / "f"),
                            "base64": base64.b64encode(b"ok").decode()})
    assert out["data"]["proof"]["byteLength"] == 2


# ---------- writeText type check ----------

def test_write_text_non_string_is_request_error(tmp_path):
    from actions import files
    with pytest.raises(ValueError, match="text must be a string"):
        files.write_text({"path": str(tmp_path / "x"), "text": {"not": "text"}})
