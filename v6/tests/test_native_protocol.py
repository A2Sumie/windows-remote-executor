"""Protocol-layer tests: the native rpc-stdio frame loop, driven as a real
local subprocess over plain pipes (zero network).

Contract under test (V6.md "Wire protocol" + "Responses and errors"):
  - stdin = one UTF-8 JSON request line, stdout = one UTF-8 JSON response line
  - malformed input gets a structured {"ok": false, "errorClass": "protocol"}
    answer and the loop STAYS ALIVE (fail-closed, never a bare traceback,
    never a silent session suicide)
  - blank lines are keepalives (no response), EOF is a clean shutdown
"""

from __future__ import annotations

import json

import pytest

ERROR_CLASSES = {"protocol", "auth", "unsupported", "request", "not-found",
                 "remote-exception"}


def _assert_error_frame(resp: dict, error_class: str | None = None) -> None:
    assert isinstance(resp, dict), f"response is not a JSON object: {resp!r}"
    assert resp.get("ok") is False, f"expected ok:false, got {resp!r}"
    assert resp.get("errorClass") in ERROR_CLASSES
    if error_class is not None:
        assert resp.get("errorClass") == error_class
    assert isinstance(resp.get("message"), str) and resp["message"]
    # fail-closed: no raw traceback may leak without includeTraceback opt-in
    assert "traceback" not in resp
    assert "Traceback (most recent call last)" not in json.dumps(resp)


def _assert_ok_frame(resp: dict) -> None:
    assert resp.get("ok") is True, f"expected ok:true, got {resp!r}"
    assert resp.get("protocolVersion") == 6
    assert isinstance(resp.get("data"), dict)


def _ping(rpc) -> None:
    """The loop must still be alive and answering after a malformed probe."""
    resp = rpc.exchange({"id": "ping", "action": "host.capabilities", "payload": {}})
    _assert_ok_frame(resp)
    assert resp.get("id") == "ping"


# ---------- well-formed frames ----------

def test_capabilities_roundtrip(native_rpc):
    resp = native_rpc.exchange({"id": "r1", "action": "host.capabilities", "payload": {}})
    _assert_ok_frame(resp)
    assert resp["data"]["version"] == 6
    assert "actions" in resp["data"] and len(resp["data"]["actions"]) == 32
    assert "schemas" in resp["data"]


def test_batch_requests_answered_in_order(native_rpc):
    for i in range(3):
        native_rpc.send_json({"id": f"b{i}", "action": "host.capabilities", "payload": {}})
    ids = [native_rpc.read_response().get("id") for _ in range(3)]
    assert ids == ["b0", "b1", "b2"]


def test_blank_line_is_keepalive_no_response(native_rpc):
    native_rpc.send_line("")
    native_rpc.send_line("   ")
    # next real request must get THE next response (blanks produced none)
    resp = native_rpc.exchange({"id": "after-blanks", "action": "host.capabilities"})
    assert resp.get("id") == "after-blanks"


def test_eof_is_clean_shutdown(native_rpc):
    assert native_rpc.proc.stdin is not None
    native_rpc.proc.stdin.close()
    assert native_rpc.proc.wait(timeout=10) == 0


def test_bom_prefixed_line(native_rpc):
    native_rpc.send_line("﻿" + json.dumps({"id": "bom", "action": "host.capabilities"}))
    resp = native_rpc.read_response()
    _assert_ok_frame(resp)


def test_crlf_line_ending(native_rpc):
    assert native_rpc.proc.stdin is not None
    native_rpc.proc.stdin.write(json.dumps({"id": "crlf", "action": "host.capabilities"}) + "\r\n")
    native_rpc.proc.stdin.flush()
    resp = native_rpc.read_response()
    _assert_ok_frame(resp)
    assert resp.get("id") == "crlf"


def test_unicode_payload_echo(native_rpc):
    resp = native_rpc.exchange({"id": "uni-雪", "action": "host.capabilities",
                                "payload": {"note": "雪"}})
    _assert_ok_frame(resp)
    assert resp.get("id") == "uni-雪"


def test_half_frame_then_eof_yields_error_then_clean_exit(native_rpc):
    # a truncated line at EOF: readline returns the partial payload -> one
    # structured protocol error, then EOF shutdown
    assert native_rpc.proc.stdin is not None
    native_rpc.proc.stdin.write('{"action": "host.capabilities"')
    native_rpc.proc.stdin.close()
    assert native_rpc.proc.stdout is not None
    line = native_rpc.proc.stdout.readline()
    resp = json.loads(line)
    _assert_error_frame(resp, "protocol")
    assert native_rpc.proc.wait(timeout=10) == 0


def test_large_but_legal_frame(native_rpc):
    big = "雪" * (512 * 1024)  # ~1.5 MB UTF-8, well under the 56 MB cap
    resp = native_rpc.exchange({"id": "big", "action": "host.capabilities",
                                "payload": {"pad": big}})
    _assert_ok_frame(resp)  # parsed fine; unknown payload keys are ignored
    _ping(native_rpc)


# ---------- malformed frames: loop must stay alive ----------

def test_garbage_non_json_line(native_rpc):
    native_rpc.send_line("this is not json at all {{{")
    _assert_error_frame(native_rpc.read_response(), "protocol")
    _ping(native_rpc)


def test_json_null_line_is_protocol_error_not_eof(native_rpc):
    # a literal `null` request line is malformed input, NOT an end-of-stream
    # signal — the loop must answer and stay alive
    native_rpc.send_line("null")
    _assert_error_frame(native_rpc.read_response(), "protocol")
    _ping(native_rpc)


def test_json_scalar_lines_are_protocol_errors(native_rpc):
    for line in ("42", '"a string"', "true", "3.14"):
        native_rpc.send_line(line)
        _assert_error_frame(native_rpc.read_response(), "protocol")
    _ping(native_rpc)


def test_json_array_line_is_protocol_error(native_rpc):
    # [] used to be swallowed as a keepalive (no response -> client hang);
    # non-empty arrays used to crash the loop with AttributeError
    native_rpc.send_line("[]")
    _assert_error_frame(native_rpc.read_response(), "protocol")
    native_rpc.send_line('[{"action": "host.capabilities"}]')
    _assert_error_frame(native_rpc.read_response(), "protocol")
    _ping(native_rpc)


def test_deeply_nested_json_does_not_kill_loop(native_rpc):
    depth = 5000
    native_rpc.send_line("[" * depth + "]" * depth)
    _assert_error_frame(native_rpc.read_response(), "protocol")
    _ping(native_rpc)


def test_deeply_nested_object_payload_contained(native_rpc):
    depth = 2000
    nested = {}
    cur = nested
    for _ in range(depth):
        cur["k"] = {}
        cur = cur["k"]
    # parser may accept it; dispatch must then contain any recursion failure
    resp = native_rpc.exchange({"id": "nest", "action": "host.capabilities",
                                "payload": nested})
    assert resp.get("id") == "nest"
    assert isinstance(resp.get("ok"), bool)
    _ping(native_rpc)


@pytest.mark.timeout(120)
def test_oversized_frame_rejected_once_and_loop_resyncs(native_rpc):
    # one line over the 56 MB cap: exactly ONE structured protocol error,
    # the remainder of the line is drained, and the NEXT request is answered
    import rpc as native_rpc_mod  # native module (conftest put it on sys.path)
    cap = native_rpc_mod.MAX_REQUEST_LINE_BYTES
    payload = "A" * (cap + 4096)
    native_rpc.send_line('{"action":"host.capabilities","pad":"' + payload + '"}')
    resp = native_rpc.read_response(timeout_s=60)
    _assert_error_frame(resp, "protocol")
    assert "exceeds" in resp["message"]
    _ping(native_rpc)


def test_many_malformed_lines_keep_loop_alive(native_rpc):
    for i in range(25):
        native_rpc.send_line(f"garbage-{i}-{{{{")
    for _ in range(25):
        _assert_error_frame(native_rpc.read_response(), "protocol")
    _ping(native_rpc)


def test_response_frame_is_single_line(native_rpc):
    native_rpc.send_json({"id": "sl", "action": "host.capabilities",
                          "payload": {"includeStdoutText": True}})
    assert native_rpc.proc.stdout is not None
    raw = native_rpc.proc.stdout.readline()
    assert raw.endswith("\n") and raw.count("\n") == 1
    json.loads(raw)  # must parse as one document
