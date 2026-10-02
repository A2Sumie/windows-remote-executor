"""Mutation-survivor hunters (QC mutation pass).

Every test here pins a behavior that a *survived* mutmut mutant proved was
unasserted in the original suite (274 survivors over 616 test-mapped mutants;
see scratch/ubob-ai-tools/qc-wre-report.md §6.1). Grouped by source function.
Transport stays mocked (FakePopen / scripted run) — zero network.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from v6.controller import client as rpc
from v6.controller import targets as tgt

# =====================================================================
# targets.py — load_target / parse_env_file / _maybe
# =====================================================================

FULL_ENV = """\
# a comment line must be skipped
TARGET_NAME=PROD-X
TARGET_HOST=192.0.2.10
TARGET_USER=operator
TARGET_PORT=2222
TARGET_KEY=C:/keys/a=b.key
TARGET_ACCESS_TOKEN=tok-123
TARGET_EXPECTED_LISTEN_ADDRESS=100.64.0.9
TARGET_EXPOSURE_MODE=tailnet
MALFORMED LINE WITHOUT EQUALS
export TARGET_EXPORTED=exp-1
"""


def _write_env(tmp_path, text: str):
    p = tmp_path / "PROD.env"
    p.write_text(text, encoding="utf-8")
    return p


def test_load_target_full_env_every_field(tmp_path):
    p = _write_env(tmp_path, FULL_ENV)
    t = tgt.load_target(str(p))
    assert t.name == "PROD-X"
    assert t.env_file == p
    assert t.host == "192.0.2.10"
    assert t.user == "operator"
    assert t.port == "2222"
    assert t.key == "C:/keys/a=b.key"  # value containing '=' survives split("=", 1)
    assert t.access_token == "tok-123"
    assert t.expected_listen_address == "100.64.0.9"
    assert t.exposure_mode == "tailnet"


def test_load_target_defaults_and_raw_name(tmp_path):
    p = _write_env(tmp_path, "TARGET_HOST=192.0.2.1\nTARGET_USER=u\n")
    t = tgt.load_target(str(p))
    assert t.port == "22"
    assert t.exposure_mode == "private-only"
    assert t.key is None and t.access_token is None
    assert t.expected_listen_address is None
    assert t.name == str(p)  # no TARGET_NAME -> falls back to the raw argument


def test_load_target_missing_file_message(tmp_path):
    with pytest.raises(FileNotFoundError) as ei:
        tgt.load_target(str(tmp_path / "nope-zzz"))
    assert "target env not found" in str(ei.value)


def test_require_error_names_key_and_path(tmp_path):
    p = _write_env(tmp_path, "TARGET_HOST=192.0.2.1\n")  # TARGET_USER missing
    with pytest.raises(RuntimeError) as ei:
        tgt.load_target(str(p))
    assert "TARGET_USER is required" in str(ei.value)
    assert str(p) in str(ei.value)
    p2 = _write_env(tmp_path, "TARGET_USER=u\n")  # TARGET_HOST missing
    with pytest.raises(RuntimeError) as ei2:
        tgt.load_target(str(p2))
    assert "TARGET_HOST is required" in str(ei2.value)
    assert str(p2) in str(ei2.value)


def test_parse_env_file_comment_blank_export(tmp_path):
    p = _write_env(tmp_path, FULL_ENV)
    values = tgt.parse_env_file(p)
    assert values["TARGET_NAME"] == "PROD-X"
    assert values["TARGET_EXPORTED"] == "exp-1"  # export prefix stripped
    assert "# a comment line must be skipped" not in values
    assert "MALFORMED LINE WITHOUT EQUALS" not in values
    # parsing CONTINUES past a malformed line (continue, not break)
    assert values["TARGET_EXPOSURE_MODE"] == "tailnet"


def test_parse_env_file_hash_prefixed_assignment_stays_comment(tmp_path):
    p = _write_env(tmp_path, "#TARGET_SNEAK=bad\nTARGET_HOST=192.0.2.1\n")
    values = tgt.parse_env_file(p)
    assert "#TARGET_SNEAK" not in values
    assert values["TARGET_HOST"] == "192.0.2.1"


def test_parse_env_file_unbalanced_quote_does_not_stop_parsing(tmp_path):
    p = _write_env(tmp_path,
                   'TARGET_BROKEN="unclosed\nTARGET_HOST=192.0.2.1\n')
    values = tgt.parse_env_file(p)
    assert "TARGET_BROKEN" not in values  # shlex ValueError -> skip line
    assert values["TARGET_HOST"] == "192.0.2.1"  # ...and keep going


def test_load_target_empty_optional_becomes_none(tmp_path):
    p = _write_env(tmp_path,
                   "TARGET_HOST=192.0.2.1\nTARGET_USER=u\nTARGET_KEY=\n")
    assert tgt.load_target(str(p)).key is None


def test_maybe_unit():
    assert tgt._maybe("") is None
    assert tgt._maybe(None) is None
    assert tgt._maybe("x") == "x"


# =====================================================================
# client.py — resolve_entry_root / remote_command / build_request
# =====================================================================

def test_resolve_entry_root_strips_only_path_separators():
    assert rpc.resolve_entry_root("C:/WX/") == "C:/WX"
    assert rpc.resolve_entry_root("D:/ENTRYX\\") == "D:/ENTRYX"
    assert rpc.resolve_entry_root("D:/X") == "D:/X"


def test_remote_command_uses_given_root():
    cmd = rpc.remote_command("D:/E")
    assert cmd == ('cmd.exe /d /s /c "D:/E/python/python.exe -I -X utf8 '
                   'D:/E/rpc.py rpc-stdio"')


def test_build_request_error_messages_exact():
    with pytest.raises(rpc.RpcError) as ei:
        rpc.build_request("", {}, access_token=None)
    assert str(ei.value) == "action is required"
    with pytest.raises(rpc.RpcError) as ei:
        rpc.build_request("a", "echo hi", access_token=None)
    assert str(ei.value) == ("payload must be a JSON object "
                             "(structured arguments only; never shell strings)")


# =====================================================================
# client.py — parse_response candidate handling
# =====================================================================

def test_parse_response_bom_on_last_line():
    resp = rpc.parse_response('noise\n\ufeff{"ok":true}\n', "", 0)
    assert resp["ok"] is True


def test_parse_response_three_line_first_line_fallback():
    stdout = '{"ok":true,"data":{"a":1}}\nmid-junk\ntrailing junk\n'
    resp = rpc.parse_response(stdout, "", 0)
    assert resp["data"]["a"] == 1


def test_parse_response_last_line_candidate_really_is_last():
    # lines[-1] must be the candidate, not lines[+1]
    stdout = 'junk1\njunk2\n\ufeff{"ok":true,"data":{"z":9}}\n'
    resp = rpc.parse_response(stdout, "", 0)
    assert resp["data"]["z"] == 9


def test_parse_response_first_line_candidate_bom_stripped():
    # lines[0] gets the BOM lstrip (not lstrip(None) / rstrip)
    stdout = '\ufeff{"ok":true,"data":{"f":1}}\nmid\njunk\n'
    resp = rpc.parse_response(stdout, "", 0)
    assert resp["data"]["f"] == 1


def test_parse_response_does_not_strip_leading_x():
    # the BOM guard must not morph into a wider character strip
    with pytest.raises(rpc.RpcProtocolError):
        rpc.parse_response('X{"ok":true}', "", 0)
    with pytest.raises(rpc.RpcProtocolError):
        rpc.parse_response('junk\nX{"ok":true}', "", 0)
    with pytest.raises(rpc.RpcProtocolError):
        rpc.parse_response('X{"ok":true}\njunk', "", 0)


def test_parse_response_bom_only_line_is_skipped_not_fatal():
    resp = rpc.parse_response('{"ok":true}\n\ufeff', "", 0)
    assert resp["ok"] is True


def test_parse_response_non_object_message_exact():
    with pytest.raises(rpc.RpcProtocolError) as ei:
        rpc.parse_response("[1,2,3]\n", "", 0)
    assert str(ei.value) == "rpc-stdio response is not a JSON object"


# =====================================================================
# client.py — _upgrade_hint boundaries and message content
# =====================================================================

def _unsupported(action: str, version: Any) -> dict:
    return {"ok": False, "errorClass": "unsupported",
            "message": f"unsupported action: {action}", "protocolVersion": version}


def test_upgrade_hint_v6_peer_is_not_rewritten():
    for v in (6, "6", 7, "7"):
        resp = _unsupported("process.run", v)
        rpc._upgrade_hint(resp, "process.run")
        assert resp["message"] == "unsupported action: process.run"


def test_upgrade_hint_message_contains_peer_version():
    resp = _unsupported("process.run", 5)
    rpc._upgrade_hint(resp, "process.run")
    assert "v5." in resp["message"]
    assert "process.run requires a v6+ native" in resp["message"]


def test_upgrade_hint_ok_response_never_rewritten_even_if_unsupported():
    resp = {"ok": True, "errorClass": "unsupported", "protocolVersion": 5,
            "message": "keep me"}
    rpc._upgrade_hint(resp, "process.run")
    assert resp["message"] == "keep me"


# =====================================================================
# client.py — call() argument forwarding
# =====================================================================

def test_call_forwards_everything(monkeypatch, fake_target):
    seen: dict[str, Any] = {}

    def _cr(target, request, *, transport_timeout_seconds, entry_root):
        seen.update(target=target, request=request,
                    transport=transport_timeout_seconds, entry_root=entry_root)
        return rpc.Call(target=target, request=request, response={"ok": True},
                        argv=[], returncode=0)

    monkeypatch.setattr(rpc, "call_request", _cr)
    rpc.call(fake_target, "process.run", {"exe": "x"}, request_id="r-1",
             timeout_ms=2500, entry_root="D:/E")
    assert seen["target"] is fake_target
    req = seen["request"]
    assert req["action"] == "process.run" and req["payload"] == {"exe": "x"}
    assert req["id"] == "r-1" and req["timeoutMs"] == 2500
    assert req["accessToken"] == "test-token"
    assert seen["transport"] == 2500 // 1000 + 30
    assert seen["entry_root"] == "D:/E"


def test_call_forwards_timeout_seconds_path(monkeypatch, fake_target):
    seen: dict[str, Any] = {}

    def _cr(target, request, *, transport_timeout_seconds, entry_root):
        seen.update(request=request, transport=transport_timeout_seconds)
        return rpc.Call(target=target, request=request, response={"ok": True},
                        argv=[], returncode=0)

    monkeypatch.setattr(rpc, "call_request", _cr)
    rpc.call(fake_target, "a", {}, timeout_seconds=7)
    assert seen["request"]["timeoutMs"] == 7000
    assert seen["transport"] == 7 + 30


def test_call_transport_margin_uses_floor_1000(monkeypatch, fake_target):
    seen: dict[str, Any] = {}

    def _cr(target, request, *, transport_timeout_seconds, entry_root):
        seen["transport"] = transport_timeout_seconds
        return rpc.Call(target=target, request=request, response={"ok": True},
                        argv=[], returncode=0)

    monkeypatch.setattr(rpc, "call_request", _cr)
    rpc.call(fake_target, "a", {}, timeout_ms=1000)
    assert seen["transport"] == 31  # 1000 // 1000 + 30 (not // 1001)


# =====================================================================
# client.py — call_request wire format + subprocess.run contract
# =====================================================================

class _RunRecorder:
    def __init__(self, stdout: str, stderr: str = "", returncode: int = 0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode
        self.seen: dict[str, Any] = {}

    def __call__(self, argv, **kw):
        self.seen["argv"] = argv
        self.seen["kw"] = kw
        return self


def _install_run(monkeypatch, rec: _RunRecorder) -> None:
    monkeypatch.setattr(rpc.subprocess, "run", rec)


def test_call_request_wire_format_compact_utf8(monkeypatch, fake_target):
    rec = _RunRecorder(stdout='{"ok":true}\n')
    _install_run(monkeypatch, rec)
    req = rpc.build_request("file.writeText", {"path": "C:/x", "text": "雪"},
                            access_token="test-token")
    rpc.call_request(fake_target, req, transport_timeout_seconds=5)
    line = rec.seen["kw"]["input"]
    assert line == json.dumps(req, ensure_ascii=False,
                              separators=(",", ":")) + "\n"
    assert "雪" in line  # ensure_ascii=False keeps UTF-8 inline


def test_call_request_subprocess_run_kwargs(monkeypatch, fake_target):
    rec = _RunRecorder(stdout='{"ok":true}\n')
    _install_run(monkeypatch, rec)
    rpc.call_request(fake_target, {"id": "x", "action": "host.info"},
                     transport_timeout_seconds=7)
    kw = rec.seen["kw"]
    assert kw["text"] is True
    assert kw["encoding"] == "utf-8"
    assert kw["errors"] == "replace"
    assert kw["stdout"] is subprocess.PIPE
    assert kw["stderr"] is subprocess.PIPE
    assert kw["check"] is False
    assert kw["timeout"] == 7


def test_call_request_argv_carries_entry_root(monkeypatch, fake_target):
    rec = _RunRecorder(stdout='{"ok":true}\n')
    _install_run(monkeypatch, rec)
    call = rpc.call_request(fake_target, {"id": "x", "action": "a"},
                            transport_timeout_seconds=1, entry_root="D:/E")
    assert call.argv[-1] == ('cmd.exe /d /s /c "D:/E/python/python.exe '
                             '-I -X utf8 D:/E/rpc.py rpc-stdio"')


def test_call_request_error_detail_prefers_stderr(monkeypatch, fake_target):
    rec = _RunRecorder(stdout="not json", stderr="detail-xyz", returncode=2)
    _install_run(monkeypatch, rec)
    with pytest.raises(rpc.RpcProtocolError, match="detail-xyz"):
        rpc.call_request(fake_target, {"id": "x"}, transport_timeout_seconds=1)


def test_call_request_error_detail_falls_back_to_exit_code(monkeypatch, fake_target):
    rec = _RunRecorder(stdout="", stderr="", returncode=7)
    _install_run(monkeypatch, rec)
    with pytest.raises(rpc.RpcProtocolError, match="exit code 7"):
        rpc.call_request(fake_target, {"id": "x"}, transport_timeout_seconds=1)


def test_call_request_upgrade_hint_names_action(monkeypatch, fake_target):
    resp = {"ok": False, "errorClass": "unsupported", "protocolVersion": 5,
            "message": "unsupported action: process.run"}
    rec = _RunRecorder(stdout=json.dumps(resp) + "\n")
    _install_run(monkeypatch, rec)
    call = rpc.call_request(fake_target, {"id": "x", "action": "process.run"},
                            transport_timeout_seconds=1)
    assert "process.run requires a v6+ native" in call.message


def test_call_request_call_fields_identity(monkeypatch, fake_target):
    rec = _RunRecorder(stdout='{"ok":true}\n', returncode=0)
    _install_run(monkeypatch, rec)
    req = {"id": "x", "action": "host.info"}
    call = rpc.call_request(fake_target, req, transport_timeout_seconds=1)
    assert call.target is fake_target
    assert call.request is req
    assert call.argv == rec.seen["argv"]
    assert call.returncode == 0


# =====================================================================
# client.py — check_peer_version
# =====================================================================

def _peer_call(fake_target, response: dict) -> rpc.Call:
    return rpc.Call(target=fake_target, request={}, response=response,
                    argv=[], returncode=0)


def test_check_peer_version_call_contract(monkeypatch, fake_target, capsys):
    seen: dict[str, Any] = {}

    def _call(target, action, payload, **kw):
        seen.update(target=target, action=action, payload=payload, kw=kw)
        return _peer_call(fake_target, {"ok": True, "data": {"version": 6}})

    monkeypatch.setattr(rpc, "call", _call)
    assert rpc.check_peer_version(fake_target, entry_root="D:/E") == 6
    assert seen["target"] is fake_target
    assert seen["action"] == "host.capabilities"
    assert seen["payload"] == {}
    assert seen["kw"] == {"timeout_seconds": 20, "entry_root": "D:/E"}
    assert capsys.readouterr().err == ""  # v6 peer: no hint


@pytest.mark.parametrize(("response", "expected", "hint"), [
    ({"ok": True, "data": {"version": "5"}}, 5, True),
    ({"ok": True, "data": {}, "protocolVersion": 4}, 4, True),
    ({"ok": True, "data": {}}, 0, False),
    ({"ok": True, "data": {"version": 1}}, 1, True),   # 0 < version < 6 boundary
    ({"ok": True, "data": {"version": 6}}, 6, False),
])
def test_check_peer_version_extraction_matrix(monkeypatch, fake_target, capsys,
                                              response, expected, hint):
    monkeypatch.setattr(rpc, "call",
                        lambda *a, **kw: _peer_call(fake_target, response))
    assert rpc.check_peer_version(fake_target) == expected
    err = capsys.readouterr().err
    assert ("[wre-v6] note" in err) is hint


# =====================================================================
# client.py — Session init / _argv / _open kwargs / close timeouts
# =====================================================================

def test_session_init_fields(fake_target):
    sess = rpc.Session(fake_target, entry_root="D:/E/", stderr_echo=True)
    assert sess.entry_root == "D:/E"
    assert sess._stderr_echo is True
    assert sess.peer_version is None
    assert rpc.Session(fake_target)._stderr_echo is False


def test_session_open_popen_kwargs_exact(fake_target, popen_patcher):
    factory = popen_patcher([{}])
    sess = rpc.Session(fake_target)
    sess._open()
    proc = factory.spawned[0]
    assert proc.argv == sess._argv()  # the fixed remote command, never None
    kw = proc.popen_kwargs
    assert kw == {"stdin": subprocess.PIPE, "stdout": subprocess.PIPE,
                  "stderr": subprocess.PIPE, "text": True,
                  "encoding": "utf-8", "errors": "replace", "bufsize": 1}
    sess.close()


def test_session_open_stderr_echo_sends_stderr_to_console(fake_target, popen_patcher):
    factory = popen_patcher([{}])
    sess = rpc.Session(fake_target, stderr_echo=True)
    sess._open()
    assert factory.spawned[0].popen_kwargs["stderr"] is None
    sess.close()


def test_session_argv_carries_entry_root(fake_target):
    sess = rpc.Session(fake_target, entry_root="D:/E")
    assert sess._argv()[-1].endswith('D:/E/rpc.py rpc-stdio"')


def test_session_close_wait_timeouts(fake_target, popen_patcher):
    factory = popen_patcher([{}])
    sess = rpc.Session(fake_target)
    sess._open()
    sess.close()
    assert factory.spawned[0].wait_timeouts == [10]


def test_session_close_stubborn_proc_killed_and_reaped(fake_target, popen_patcher):
    factory = popen_patcher([{"wait_raises": 1}])
    sess = rpc.Session(fake_target)
    sess._open()
    sess.close()
    proc = factory.spawned[0]
    assert proc.killed is True
    assert proc.wait_timeouts == [10, 5]  # graceful wait, then reap-after-kill


# =====================================================================
# client.py — Session.call request building + Call fields
# =====================================================================

def _ok_line(data: dict | None = None) -> str:
    return json.dumps({"ok": True, "data": data or {}}) + "\n"


def test_session_call_builds_request_and_call_fields(fake_target, popen_patcher):
    factory = popen_patcher([{"stdout_lines": [_ok_line()]}])
    sess = rpc.Session(fake_target)
    call = sess.call("wsl.list", {"v": 1}, timeout_ms=3000)
    req = json.loads(factory.spawned[0].stdin.writes[0])
    assert req["action"] == "wsl.list" and req["payload"] == {"v": 1}
    assert req["timeoutMs"] == 3000 and req["accessToken"] == "test-token"
    assert call.target is fake_target
    assert call.request["action"] == "wsl.list"
    assert call.argv == sess._argv()
    assert call.returncode == 0
    sess.close()


def test_session_call_frame_is_compact_utf8(fake_target, popen_patcher):
    factory = popen_patcher([{"stdout_lines": [_ok_line()]}])
    sess = rpc.Session(fake_target)
    sess.call("file.writeText", {"text": "雪"})
    line = factory.spawned[0].stdin.writes[0]
    assert "雪" in line and ": " not in line.split('"text"')[0]
    sess.close()


# =====================================================================
# client.py — Session._round_trip error messages + read deadline
# =====================================================================

def _stall_thread(monkeypatch) -> dict:
    """A Thread stand-in whose reader never runs: forces the deadline path."""
    recorded: dict[str, Any] = {}

    class _Stall:
        def __init__(self, target=None, daemon=None):
            recorded["daemon"] = daemon

        def start(self):
            pass

        def join(self, timeout=None):
            recorded["timeout"] = timeout

        def is_alive(self):
            return True

    monkeypatch.setattr(rpc.threading, "Thread", _Stall)
    return recorded


def test_round_trip_deadline_from_timeout_ms(fake_target, popen_patcher, monkeypatch):
    popen_patcher([{}])
    recorded = _stall_thread(monkeypatch)
    sess = rpc.Session(fake_target)
    with pytest.raises(rpc.RpcTransportError, match="read timed out after 31s"):
        sess.call("host.info", timeout_ms=1000)
    assert recorded["timeout"] == 31  # 1000ms // 1000 + 30s margin
    assert recorded["daemon"] is True  # reader must never pin interpreter exit
    sess.close()


def test_round_trip_default_deadline_is_600(fake_target, popen_patcher, monkeypatch):
    popen_patcher([{}])
    recorded = _stall_thread(monkeypatch)
    sess = rpc.Session(fake_target)
    with pytest.raises(rpc.RpcTransportError, match="after 600s"):
        sess.call("host.info")
    assert recorded["timeout"] == 600
    sess.close()


def test_round_trip_write_error_message(fake_target, popen_patcher):
    popen_patcher([{"broken_writes": 1}, {"broken_writes": 1}])
    sess = rpc.Session(fake_target)
    with pytest.raises(rpc.RpcTransportError, match="session write failed"):
        sess.call("host.info")
    sess.close()


def test_round_trip_read_error_surfaces_cause(fake_target, popen_patcher):
    popen_patcher([{"read_exception": OSError("disk gone")}])
    sess = rpc.Session(fake_target)
    with pytest.raises(rpc.RpcTransportError, match="session read failed: disk gone"):
        sess.call("host.info")
    sess.close()


def test_round_trip_unparseable_line_is_protocol_error(fake_target, popen_patcher):
    popen_patcher([{"stdout_lines": ["garbage\n"]}])
    sess = rpc.Session(fake_target)
    with pytest.raises(rpc.RpcProtocolError, match="did not return JSON: garbage"):
        sess.call("host.info")
    sess.close()


def test_round_trip_peer_close_reports_exit_code_and_stderr_tail(
        fake_target, popen_patcher):
    noise = "HEAD" + "E" * 401 + "TAIL-END"
    popen_patcher([{"stdout_lines": [], "stderr_lines": [noise],
                    "returncode": 255}])
    sess = rpc.Session(fake_target)
    with pytest.raises(rpc.RpcTransportError) as ei:
        sess.call("host.info")
    msg = str(ei.value)
    assert "ssh exit 255" in msg
    # tail capped at exactly the last 400 chars (not 401, not the whole read)
    assert msg.endswith("): " + noise[-400:])
    assert "TAIL-END" in msg and "HEAD" not in msg
    sess.close()


def test_round_trip_peer_close_without_stderr_pipe(fake_target, popen_patcher):
    popen_patcher([{"stdout_lines": [], "returncode": 255}])
    sess = rpc.Session(fake_target, stderr_echo=True)  # -> proc.stderr is None
    with pytest.raises(rpc.RpcTransportError) as ei:
        sess.call("host.info")
    assert str(ei.value).endswith("ssh exit 255): ")
    sess.close()


# =====================================================================
# wre_mcp — _session / _call forwarding, messages, ensure_ascii
# =====================================================================

import sys as _sys
import types as _types

# same mcp stub pattern as test_mcp_surface (idempotent)
if "mcp.server.fastmcp" not in _sys.modules:
    _mcp_mod = _types.ModuleType("mcp")
    _server_mod = _types.ModuleType("mcp.server")
    _fastmcp_mod = _types.ModuleType("mcp.server.fastmcp")

    class _StubServer:
        def __init__(self, name, instructions=""):
            pass

        def tool(self):
            def deco(fn):
                return fn
            return deco

    _fastmcp_mod.FastMCP = _StubServer
    _server_mod.fastmcp = _fastmcp_mod
    _mcp_mod.server = _server_mod
    _sys.modules.setdefault("mcp", _mcp_mod)
    _sys.modules["mcp.server"] = _server_mod
    _sys.modules["mcp.server.fastmcp"] = _fastmcp_mod

from v6.mcp import wre_mcp


@pytest.fixture()
def clear_sessions():
    wre_mcp._sessions.clear()
    yield
    wre_mcp._sessions.clear()


def test_session_passes_name_target_and_entry_root(monkeypatch, clear_sessions,
                                                   fake_target):
    seen: dict[str, Any] = {}

    def _load(name):
        seen["name"] = name
        return fake_target

    class _Sess:
        def __init__(self, target, entry_root=None):
            seen["target"] = target
            seen["entry_root"] = entry_root

        def _open(self):
            pass

    monkeypatch.setattr(wre_mcp.tgt, "load_target", _load)
    monkeypatch.setattr(wre_mcp.rpc, "Session", _Sess)
    wre_mcp._session("X570", "D:/E")
    assert seen == {"name": "X570", "target": fake_target, "entry_root": "D:/E"}


def test_session_unknown_target_message_content(clear_sessions):
    out = json.loads(wre_mcp.wre_info("no-such-target-zzz"))
    assert out["error"] == "transport"
    assert "cannot load target 'no-such-target-zzz':" in out["message"]
    assert out["message"].endswith("(valid targets: X570, nuc-8-sumie)")


def test_session_open_failure_message_content(monkeypatch, clear_sessions,
                                              fake_target):
    monkeypatch.setattr(wre_mcp.tgt, "load_target", lambda name: fake_target)

    class _BadSession:
        def __init__(self, *a, **kw):
            pass

        def _open(self):
            raise OSError("ssh binary missing")

    monkeypatch.setattr(wre_mcp.rpc, "Session", _BadSession)
    out = json.loads(wre_mcp.wre_info("X570"))
    assert out["error"] == "transport"
    assert "cannot open session to 'X570': ssh binary missing" in out["message"]


class _FakeCall:
    def __init__(self, *, ok=True, data=None, error_class="", message="",
                 response=None):
        self.ok = ok
        self.data = data or {}
        self.error_class = error_class
        self.message = message
        self.response = response or {}


def _patch_session(monkeypatch, *, recorder: dict | None = None,
                   result: Any = None, raises: BaseException | None = None):
    class _Sess:
        def call(self, action, payload, timeout_ms=None):
            if recorder is not None:
                recorder["call"] = (action, payload, timeout_ms)
            if raises is not None:
                raise raises
            return result

    def _sess(target_name, entry_root):
        if recorder is not None:
            recorder["sess"] = (target_name, entry_root)
        return _Sess()

    monkeypatch.setattr(wre_mcp, "_session", _sess)


def test_call_forwards_session_and_call_args(monkeypatch):
    rec: dict[str, Any] = {}
    _patch_session(monkeypatch, recorder=rec,
                   result=_FakeCall(data={"d": 1}))
    out = json.loads(wre_mcp._call("X570", "host.info", {"a": 1}, "D:/E", 123))
    assert out == {"d": 1}
    assert rec["sess"] == ("X570", "D:/E")
    assert rec["call"] == ("host.info", {"a": 1}, 123)


def test_call_transport_error_keeps_utf8(monkeypatch):
    _patch_session(monkeypatch,
                   raises=wre_mcp.rpc.RpcTransportError("ssh 雪 died"))
    out = wre_mcp._call("X570", "host.info", {})
    assert "雪" in out  # ensure_ascii=False: no \uXXXX escaping
    assert json.loads(out) == {"error": "transport", "message": "ssh 雪 died"}


def test_call_remote_error_passthrough_full(monkeypatch):
    _patch_session(monkeypatch, result=_FakeCall(
        ok=False, error_class="unsupported", message="no 雪",
        response={"didYouMean": "process.run"}))
    out = wre_mcp._call("X570", "process.run", {})
    assert "雪" in out
    assert json.loads(out) == {"error": "unsupported", "message": "no 雪",
                               "didYouMean": "process.run"}


def test_call_remote_error_did_you_mean_defaults_empty(monkeypatch):
    _patch_session(monkeypatch, result=_FakeCall(
        ok=False, error_class="auth", message="denied", response={}))
    out = json.loads(wre_mcp._call("X570", "host.info", {}))
    assert out["didYouMean"] == ""


def test_call_success_data_keeps_utf8(monkeypatch):
    _patch_session(monkeypatch, result=_FakeCall(data={"city": "雪"}))
    out = wre_mcp._call("X570", "host.info", {})
    assert "雪" in out
    assert json.loads(out) == {"city": "雪"}


def test_call_internal_error_keeps_utf8(monkeypatch):
    _patch_session(monkeypatch, raises=ValueError("雪"))
    out = wre_mcp._call("X570", "host.info", {})
    assert "雪" in out  # internal-containment branch also stays unescaped
    assert json.loads(out)["error"] == "internal"
