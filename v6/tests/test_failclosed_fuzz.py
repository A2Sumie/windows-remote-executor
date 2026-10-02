"""Fail-closed fuzz: ANY internal anomaly must exit as a structured error —
never a raw traceback, never a loop death, never a hang.

Two lanes:
  1. wire-level: random malformed lines through the real native loop
     subprocess; every answer must be a structured protocol/request error and
     the loop must still be alive at the end
  2. dispatch-level: random request dicts through native dispatch() in-process;
     it must never raise and always return a structured response
"""

from __future__ import annotations

import json
import random
import string

import pytest
import rpc as native_rpc

ERROR_CLASSES = {"protocol", "auth", "unsupported", "request", "not-found",
                 "remote-exception"}


def _random_line(rng: random.Random) -> str:
    kind = rng.randrange(8)
    if kind == 0:  # printable garbage
        return "".join(rng.choice(string.printable) for _ in range(rng.randrange(200)))
    if kind == 1:  # braces salad
        return "".join(rng.choice("{}[]\",:0123truefalsn ") for _ in range(rng.randrange(300)))
    if kind == 2:  # valid JSON, non-dict
        return json.dumps(rng.choice([None, True, 3.14, "str", [1, 2], {"a": 1}]))[1:-1] or "x"
    if kind == 3:  # dict missing action
        return json.dumps({"id": "f", "payload": {"x": rng.randrange(10)}})
    if kind == 4:  # weird action types
        return json.dumps({"action": rng.choice([None, 42, ["x"], {"a": 1}, "", "  "])})
    if kind == 5:  # weird payload types
        return json.dumps({"action": "host.capabilities",
                           "payload": rng.choice([None, 42, "s", [1], True])})
    if kind == 6:  # truncated json
        return '{"action": "host.capabilities", "payload": {' + "1" * rng.randrange(50)
    # control chars / unicode
    return "".join(chr(rng.randrange(0x20, 0x2FFF)) for _ in range(rng.randrange(100)))


@pytest.mark.timeout(120)
def test_fuzz_wire_level_loop_survives(native_rpc):
    rng = random.Random(20260825)
    # a "line" must not embed its own newline (that would split the frame)
    lines = [_random_line(rng).replace("\n", " ").replace("\r", " ")
             for _ in range(120)]
    # blank/whitespace lines are keepalives (no response); count them
    expected_silence = sum(1 for ln in lines if not ln.strip())
    for ln in lines:
        native_rpc.send_line(ln)
    for _ in range(len(lines) - expected_silence):
        resp = native_rpc.read_response(timeout_s=30)
        assert isinstance(resp, dict)
        assert isinstance(resp.get("ok"), bool)
        if resp["ok"] is False:
            assert resp.get("errorClass") in ERROR_CLASSES
        assert "Traceback (most recent call last)" not in json.dumps(resp)
    assert native_rpc.alive()
    resp = native_rpc.exchange({"id": "post-fuzz", "action": "host.capabilities"})
    assert resp.get("ok") is True and resp.get("id") == "post-fuzz"


def _random_value(rng: random.Random, depth: int = 0):
    if depth > 4:
        return rng.choice([1, "x", None])
    kind = rng.randrange(7)
    if kind == 0:
        return None
    if kind == 1:
        return rng.choice([True, False])
    if kind == 2:
        return rng.choice([0, -1, 2**40, 3.14, float("inf") if rng.random() < 0.05 else 1])
    if kind == 3:
        return "".join(rng.choice("abc雪\x00%{}") for _ in range(rng.randrange(30)))
    if kind == 4:
        return [_random_value(rng, depth + 1) for _ in range(rng.randrange(5))]
    if kind == 5:
        return {f"k{i}": _random_value(rng, depth + 1) for i in range(rng.randrange(5))}
    return rng.choice(["process.run", "file.readText", "host.info", "nope", 42])


@pytest.mark.timeout(120)
def test_fuzz_dispatch_never_raises(deny_all=None):
    rng = random.Random(424242)
    for _ in range(400):
        request = {
            "id": _random_value(rng),
            "action": _random_value(rng),
            "payload": _random_value(rng),
            "accessToken": _random_value(rng),
        }
        if rng.random() < 0.3:
            request.pop(rng.choice(list(request)))
        resp = native_rpc.dispatch(request)  # must not raise
        assert isinstance(resp, dict)
        assert isinstance(resp.get("ok"), bool)
        if not resp["ok"]:
            assert resp.get("errorClass") in ERROR_CLASSES
            assert isinstance(resp.get("message"), str)
            assert "\n" not in resp["message"]
        # the response itself must be JSON-serializable as one line
        json.dumps(resp, ensure_ascii=False)


@pytest.mark.timeout(60)
def test_fuzz_client_parse_response_never_crashes_oddly():
    from v6.controller import client as rpc
    rng = random.Random(7)
    for _ in range(300):
        stdout = _random_line(rng) + "\n" + _random_line(rng)
        stderr = _random_line(rng)
        try:
            resp = rpc.parse_response(stdout, stderr, rng.randrange(256))
            assert isinstance(resp, dict)
        except rpc.RpcProtocolError:
            pass  # the ONLY acceptable failure mode
