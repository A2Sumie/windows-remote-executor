"""In-process dispatch tests for the native rpc module (imported via the
conftest native-dir path). Covers action dispatch, the error model, and the
fail-closed auth gate (no access-policy.json exists in the repo tree, so the
default posture is deny-all-but-capabilities)."""

from __future__ import annotations

import pytest
import rpc as native_rpc
from win32 import access_policy


@pytest.fixture()
def deny_all_policy(monkeypatch, tmp_path):
    """Guarantees the policy path points at a nonexistent file (fail-closed)."""
    monkeypatch.setattr(access_policy, "_POLICY_PATH", str(tmp_path / "access-policy.json"))
    return tmp_path / "access-policy.json"


@pytest.fixture()
def good_policy(deny_all_policy):
    import hashlib
    import json
    sha = hashlib.sha256(b"secret-token").hexdigest()
    deny_all_policy.write_text(json.dumps({"accessTokenSha256": sha, "label": "TEST"}))
    return "secret-token"


def _dispatch(request):
    return native_rpc.dispatch(request)


# ---------- dispatch: action resolution ----------

def test_missing_action_is_protocol_error(deny_all_policy):
    resp = _dispatch({"id": "x", "payload": {}})
    assert resp["ok"] is False and resp["errorClass"] == "protocol"
    assert "missing action" in resp["message"]


def test_unknown_action_unsupported_with_did_you_mean(deny_all_policy):
    resp = _dispatch({"id": "x", "action": "process.rn", "payload": {}})
    assert resp["ok"] is False and resp["errorClass"] == "unsupported"
    assert resp.get("didYouMean") == "process.run"
    assert "did you mean process.run" in resp["message"]


def test_unknown_action_far_from_any_has_no_suggestion(deny_all_policy):
    resp = _dispatch({"id": "x", "action": "zzzzzzzz", "payload": {}})
    assert resp["ok"] is False and resp["errorClass"] == "unsupported"
    assert not resp.get("didYouMean")


def test_action_whitespace_and_nonstring_coerced(deny_all_policy):
    resp = _dispatch({"id": "x", "action": "  host.capabilities  ", "payload": {}})
    assert resp["ok"] is True
    resp2 = _dispatch({"id": "y", "action": 42, "payload": {}})
    assert resp2["ok"] is False and resp2["errorClass"] == "unsupported"


def test_request_id_echoed_and_generated(deny_all_policy):
    resp = _dispatch({"id": "my-id-1", "action": "host.capabilities"})
    assert resp["id"] == "my-id-1"
    resp2 = _dispatch({"action": "host.capabilities"})
    assert resp2["id"].startswith("rpc-")


def test_duration_ms_always_present(deny_all_policy):
    resp = _dispatch({"action": "host.capabilities"})
    assert isinstance(resp.get("durationMs"), int)


# ---------- error model ----------

def test_error_message_is_one_line(deny_all_policy):
    resp = _dispatch({"action": "system.help", "payload": {"action": "no.such\nMULTILINE"}})
    assert resp["ok"] is False
    assert "\n" not in resp["message"]


def test_traceback_opt_in(deny_all_policy, good_policy, tmp_jobs_dir):
    base = {"action": "file.readText", "payload": {"path": "/nonexistent/x"}}
    resp = _dispatch({**base, "accessToken": good_policy})
    assert resp["ok"] is False and resp["errorClass"] == "not-found"
    assert "traceback" not in resp
    resp2 = _dispatch({"action": "system.help",
                       "payload": {"action": "no.such", "includeTraceback": True}})
    assert resp2["ok"] is False
    # ValueError maps to "request" — a deterministic non-Exception path; the
    # traceback option only attaches on remote-exception
    resp3 = _dispatch({"action": "process.wait", "accessToken": good_policy,
                       "payload": {"jobId": "j-0000000000000000",
                                   "includeTraceback": True}})
    assert resp3["ok"] is False and resp3["errorClass"] == "not-found"


def test_value_error_maps_to_request_class(deny_all_policy, good_policy):
    resp = _dispatch({"action": "system.help", "accessToken": good_policy,
                      "payload": {"action": "nope.nope"}})
    assert resp["errorClass"] == "request"


def test_remote_exception_contains_no_traceback_by_default(deny_all_policy, good_policy, monkeypatch, tmp_jobs_dir):
    def _boom(payload):
        raise RuntimeError("kapow")
    monkeypatch.setitem(native_rpc.ACTIONS, "test.boom", _boom)
    try:
        resp = _dispatch({"action": "test.boom", "accessToken": good_policy, "payload": {}})
        assert resp["ok"] is False and resp["errorClass"] == "remote-exception"
        assert resp["message"] == "RuntimeError: kapow"
        assert "traceback" not in resp
        resp_tb = _dispatch({"action": "test.boom", "accessToken": good_policy,
                             "payload": {"includeTraceback": True}})
        assert "RuntimeError" in resp_tb.get("traceback", "")
    finally:
        native_rpc.ACTIONS.pop("test.boom", None)


# ---------- fail-closed auth ----------

def test_capabilities_is_token_exempt(deny_all_policy):
    resp = _dispatch({"action": "host.capabilities"})
    assert resp["ok"] is True


@pytest.mark.parametrize("action", sorted(native_rpc.ACTIONS.keys() - {"host.capabilities"}))
def test_every_other_action_denied_without_policy(deny_all_policy, action):
    resp = _dispatch({"action": action, "payload": {}})
    assert resp["ok"] is False and resp["errorClass"] == "auth"
    assert "fail-closed" in resp["message"]


def test_wrong_token_denied(deny_all_policy, good_policy):
    resp = _dispatch({"action": "host.info", "accessToken": "wrong", "payload": {}})
    assert resp["ok"] is False and resp["errorClass"] == "auth"


def test_right_token_passes(deny_all_policy, good_policy):
    resp = _dispatch({"action": "host.info", "accessToken": good_policy, "payload": {}})
    assert resp["ok"] is True
    assert resp["data"]["protocolVersion"] == 6


def test_corrupt_policy_denies(deny_all_policy):
    deny_all_policy.write_text("{not json")
    resp = _dispatch({"action": "host.info", "accessToken": "x", "payload": {}})
    assert resp["ok"] is False and resp["errorClass"] == "auth"
    assert "corrupt" in resp["message"]


def test_null_token_policy_denies_even_with_token(deny_all_policy):
    import json
    deny_all_policy.write_text(json.dumps({"accessTokenSha256": None}))
    resp = _dispatch({"action": "host.info", "accessToken": "x", "payload": {}})
    assert resp["ok"] is False and resp["errorClass"] == "auth"


# ---------- payload shape ----------

def test_payload_string_rejected(deny_all_policy, good_policy):
    resp = _dispatch({"action": "process.run", "accessToken": good_policy,
                      "payload": "exe=cmd"})
    assert resp["ok"] is False and resp["errorClass"] == "request"
    assert "payload must be a JSON object" in resp["message"]


def test_payload_list_rejected(deny_all_policy, good_policy):
    resp = _dispatch({"action": "process.run", "accessToken": good_policy,
                      "payload": ["exe"]})
    assert resp["errorClass"] == "request"


def test_missing_payload_defaults_empty(deny_all_policy):
    resp = _dispatch({"action": "host.capabilities"})
    assert resp["ok"] is True


def test_stdout_text_opt_in(deny_all_policy, good_policy, tmp_path, monkeypatch):
    f = tmp_path / "a.txt"
    f.write_text("hello", encoding="utf-8")
    req = {"action": "file.readText", "accessToken": good_policy,
           "payload": {"path": str(f)}}
    resp = _dispatch(req)
    assert resp["ok"] is True
    assert "stdoutText" not in resp
    assert resp["data"]["text"] == "hello"
    resp2 = _dispatch({**req, "payload": {"path": str(f), "includeStdoutText": True}})
    assert resp2.get("stdoutText") == "hello"
    assert resp2.get("stdoutEncoding") == "utf-8"


def test_system_help_surface(deny_all_policy, good_policy):
    resp = _dispatch({"action": "system.help", "accessToken": good_policy, "payload": {}})
    assert resp["ok"] is True
    actions = resp["data"]["actions"]
    assert len(actions) == 32
    for name, entry in actions.items():
        assert "summary" in entry and "schema" in entry and "identity" in entry, name
