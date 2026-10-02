"""Controller-side protocol tests: build_request / parse_response /
_upgrade_hint / one-shot call_request / Session reconnect+deadline semantics.
Transport is a scripted FakePopen — zero network."""

from __future__ import annotations

import json
import subprocess

import pytest

from v6.controller import client as rpc

# ---------- build_request ----------

def test_build_request_shape(fake_target):
    req = rpc.build_request("host.info", None, access_token=fake_target.access_token)
    assert req["action"] == "host.info" and req["payload"] == {}
    assert req["id"].startswith("rpc-")
    assert req["accessToken"] == "test-token"
    assert "timeoutMs" not in req


def test_build_request_requires_action():
    with pytest.raises(rpc.RpcError):
        rpc.build_request("", {}, access_token=None)


def test_build_request_timeout_aliases():
    assert rpc.build_request("a", {}, timeout_ms=5000, access_token=None)["timeoutMs"] == 5000
    assert rpc.build_request("a", {}, timeout_seconds=7, access_token=None)["timeoutMs"] == 7000
    # timeout_ms wins when both are given
    assert rpc.build_request("a", {}, timeout_ms=1, timeout_seconds=9,
                             access_token=None)["timeoutMs"] == 1


def test_build_request_rejects_non_dict_payload():
    with pytest.raises(rpc.RpcError):
        rpc.build_request("a", "echo hi", access_token=None)
    with pytest.raises(rpc.RpcError):
        rpc.build_request("a", ["x"], access_token=None)


def test_build_request_no_token_omitted():
    req = rpc.build_request("a", {}, access_token=None)
    assert "accessToken" not in req


# ---------- parse_response ----------

def test_parse_response_simple():
    resp = rpc.parse_response('{"ok":true,"data":{}}\n', "", 0)
    assert resp["ok"] is True


def test_parse_response_bom_and_whitespace():
    resp = rpc.parse_response('\ufeff  {"ok":true} \n', "", 0)
    assert resp["ok"] is True


def test_parse_response_multiline_prefers_last_json_line():
    stdout = 'warning: something\n{"ok":true,"data":{"x":1}}\n'
    resp = rpc.parse_response(stdout, "", 0)
    assert resp["data"]["x"] == 1


def test_parse_response_garbage_raises_protocol_error():
    with pytest.raises(rpc.RpcProtocolError, match="did not return JSON"):
        rpc.parse_response("total garbage\n", "", 0)


def test_parse_response_non_object_json_raises():
    with pytest.raises(rpc.RpcProtocolError, match="not a JSON object"):
        rpc.parse_response("[1,2,3]\n", "", 0)
    with pytest.raises(rpc.RpcProtocolError):
        rpc.parse_response("42\n", "", 0)


def test_parse_response_empty_uses_stderr_detail():
    with pytest.raises(rpc.RpcProtocolError, match="ssh said no"):
        rpc.parse_response("", "ssh said no", 255)
    with pytest.raises(rpc.RpcProtocolError, match="exit code 255"):
        rpc.parse_response("", "", 255)


# ---------- _upgrade_hint ----------

def _v5_unsupported(action: str) -> dict:
    return {"ok": False, "errorClass": "unsupported",
            "message": f"unsupported action: {action}", "protocolVersion": 5}


def test_upgrade_hint_rewrites_v6_only_action():
    resp = _v5_unsupported("process.run")
    rpc._upgrade_hint(resp, "process.run")
    assert "requires a v6+ native" in resp["message"]


def test_upgrade_hint_leaves_v4_action_alone():
    resp = _v5_unsupported("file.readText")
    rpc._upgrade_hint(resp, "file.readText")
    assert resp["message"] == "unsupported action: file.readText"


def test_upgrade_hint_tolerates_malformed_protocol_version():
    resp = _v5_unsupported("wsl.run")
    resp["protocolVersion"] = "garbage"
    rpc._upgrade_hint(resp, "wsl.run")  # must not raise
    assert "requires a v6+ native" in resp["message"]


def test_upgrade_hint_ignores_ok_responses():
    resp = {"ok": True, "protocolVersion": 4}
    rpc._upgrade_hint(resp, "process.run")
    assert "message" not in resp


def test_is_v6_only():
    assert rpc._is_v6_only("process.run") and rpc._is_v6_only("wsl.list")
    assert rpc._is_v6_only("host.info") and rpc._is_v6_only("system.help")
    assert rpc._is_v6_only("host.tasks.clean")
    assert not rpc._is_v6_only("file.readText") and not rpc._is_v6_only("host.probe")


# ---------- Call accessors ----------

def _call_with(response: dict) -> rpc.Call:
    return rpc.Call(target=None, request={}, response=response, argv=[], returncode=0)  # type: ignore[arg-type]


def test_call_data_must_be_dict():
    call = _call_with({"ok": True, "data": [1, 2, 3]})
    assert call.data == {}  # non-dict data is contained, not leaked
    assert call.stdout_text == ""


def test_call_stdout_fallbacks():
    assert _call_with({"ok": True, "stdoutText": "a"}).stdout_text == "a"
    assert _call_with({"ok": True, "data": {"stdout": "b"}}).stdout_text == "b"
    assert _call_with({"ok": True, "data": {"text": "c"}}).stdout_text == "c"
    assert _call_with({"ok": True, "data": {"stderr": "e"}}).stderr_text == "e"


def test_require_ok_raises_with_class_and_message():
    call = _call_with({"ok": False, "errorClass": "auth", "message": "denied"})
    with pytest.raises(rpc.RpcError, match="auth: denied"):
        call.require_ok()
    assert _call_with({"ok": True}).require_ok().ok is True


# ---------- one-shot call_request ----------

def test_call_request_transport_timeout(monkeypatch, fake_target):
    def _raise_timeout(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="ssh", timeout=1)
    monkeypatch.setattr(rpc.subprocess, "run", _raise_timeout)
    with pytest.raises(rpc.RpcTransportError, match="timed out"):
        rpc.call(fake_target, "host.info", {})


def test_call_request_happy_path(monkeypatch, fake_target):
    seen = {}

    class _Done:
        stdout = '{"ok":true,"data":{"v":1}}\n'
        stderr = ""
        returncode = 0

    def _run(argv, **kw):
        seen["argv"] = argv
        seen["input"] = kw.get("input")
        seen["timeout"] = kw.get("timeout")
        return _Done()

    monkeypatch.setattr(rpc.subprocess, "run", _run)
    call = rpc.call(fake_target, "host.info", {"x": 1}, timeout_seconds=30)
    assert call.ok is True and call.data == {"v": 1}
    assert seen["timeout"] == 60  # 30s + 30s margin
    sent = json.loads(seen["input"])
    assert sent["action"] == "host.info" and sent["accessToken"] == "test-token"
    # payload never travels in argv — only the fixed remote command does
    assert "host.info" not in " ".join(seen["argv"][:-1])
    assert seen["argv"][-1].endswith('rpc.py rpc-stdio"')


def test_call_default_transport_deadline(monkeypatch, fake_target):
    seen = {}

    class _Done:
        stdout = '{"ok":true}'
        stderr = ""
        returncode = 0

    def _run(argv, **kw):
        seen["t"] = kw.get("timeout")
        return _Done()

    monkeypatch.setattr(rpc.subprocess, "run", _run)
    rpc.call(fake_target, "host.info", {})
    assert seen["t"] == rpc.DEFAULT_TRANSPORT_TIMEOUT_S  # H9: never no-deadline


# ---------- Session: reconnect-once semantics ----------

def _ok_line(data: dict | None = None) -> str:
    return json.dumps({"ok": True, "data": data or {}}) + "\n"


def test_session_happy_path(fake_target, popen_patcher):
    popen_patcher([{"stdout_lines": [_ok_line({"n": 1})]}])
    sess = rpc.Session(fake_target)
    call = sess.call("host.info")
    assert call.ok is True and call.data == {"n": 1}
    sess.close()


def test_session_reconnect_once_on_broken_pipe_then_success(fake_target, popen_patcher):
    factory = popen_patcher([
        {"broken_writes": 1},                      # first proc: write breaks
        {"stdout_lines": [_ok_line({"re": 1})]},   # reconnect: answers
    ])
    sess = rpc.Session(fake_target)
    call = sess.call("host.info")
    assert call.ok is True and call.data == {"re": 1}
    assert len(factory.spawned) == 2
    # the SAME request was re-sent after reconnect
    assert factory.spawned[1].stdin.writes == [l for l in factory.spawned[1].stdin.writes]
    assert json.loads(factory.spawned[1].stdin.writes[0])["action"] == "host.info"
    sess.close()


def test_session_second_failure_surfaces_transport_error(fake_target, popen_patcher):
    factory = popen_patcher([{"broken_writes": 1}, {"broken_writes": 1}])
    sess = rpc.Session(fake_target)
    with pytest.raises(rpc.RpcTransportError):
        sess.call("host.info")
    assert len(factory.spawned) == 2  # exactly ONE reconnect, not a loop
    sess.close()


def test_session_peer_eof_raises_transport(fake_target, popen_patcher):
    popen_patcher([{"stdout_lines": [], "stderr_lines": ["bye"], "returncode": 255}])
    sess = rpc.Session(fake_target)
    with pytest.raises(rpc.RpcTransportError, match="closed by peer"):
        sess.call("host.info")
    sess.close()


def test_session_reopens_dead_proc_before_write(fake_target, popen_patcher):
    factory = popen_patcher([
        {"returncode": 0},                     # dead-on-arrival first proc
        {"stdout_lines": [_ok_line()], "returncode": None},
    ])
    sess = rpc.Session(fake_target)
    sess._open()  # session now HOLDS the dead proc
    call = sess.call("host.info")  # call-time check reopens before writing
    assert call.ok is True
    assert len(factory.spawned) == 2
    sess.close()


def test_session_read_deadline_kills_and_raises(fake_target, popen_patcher, monkeypatch):
    """A wedged peer must hit the read deadline: the ssh proc is closed/killed
    and a transport error surfaces — the call must never hang."""
    popen_patcher([{}])  # stdout with no lines would block forever on a real pipe

    class _NeverFinishingThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            pass  # never run the reader -> simulates a blocked readline

        def join(self, timeout=None):
            self.join_timeout = timeout

        def is_alive(self):
            return True

    monkeypatch.setattr(rpc.threading, "Thread", _NeverFinishingThread)
    sess = rpc.Session(fake_target)
    sess._open()
    with pytest.raises(rpc.RpcTransportError, match="read timed out"):
        sess.call("host.info", timeout_ms=1000)
    # the stuck proc was discarded so the NEXT call reconnects cleanly
    assert sess._proc is None
    sess.close()


def test_session_close_is_idempotent_and_kills_stubborn(fake_target, popen_patcher):
    factory = popen_patcher([{}])
    sess = rpc.Session(fake_target)
    sess._open()
    proc = factory.spawned[0]
    sess.close()
    sess.close()  # no raise
    assert proc.wait_calls >= 1


def test_session_serializes_request_line_compact(fake_target, popen_patcher):
    factory = popen_patcher([{"stdout_lines": [_ok_line()]}])
    sess = rpc.Session(fake_target)
    sess.call("file.writeText", {"path": "C:/x", "text": "雪"})
    line = factory.spawned[0].stdin.writes[0]
    assert line.endswith("\n") and line.count("\n") == 1
    assert ',"action":"file.writeText"' in line  # compact separators
    assert "雪" in line  # ensure_ascii=False keeps UTF-8 inline
    sess.close()


def test_session_upgrade_hint_applied(fake_target, popen_patcher):
    resp = {"ok": False, "errorClass": "unsupported", "protocolVersion": 5,
            "message": "unsupported action: process.run"}
    popen_patcher([{"stdout_lines": [json.dumps(resp) + "\n"]}])
    sess = rpc.Session(fake_target)
    call = sess.call("process.run", {"exe": "C:/x"})
    assert "requires a v6+ native" in call.message
    sess.close()


def test_entry_root_resolution(monkeypatch):
    assert rpc.resolve_entry_root(None) == "C:/WRE/wre"
    monkeypatch.setenv("WRE_ENTRY", "C:/WRE/wre6/")
    assert rpc.resolve_entry_root(None) == "C:/WRE/wre6"  # trailing slash stripped
    assert rpc.resolve_entry_root("D:/explicit/") == "D:/explicit"
    cmd = rpc.remote_command("C:/WRE/wre6")
    assert cmd == 'cmd.exe /d /s /c "C:/WRE/wre6/python/python.exe -I -X utf8 C:/WRE/wre6/rpc.py rpc-stdio"'
