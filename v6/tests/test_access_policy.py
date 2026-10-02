"""Access-policy unit tests: fail-closed token enforcement and policy writes.
Policy path is always redirected to tmp — the repo tree must stay untouched."""

from __future__ import annotations

import hashlib
import json

import pytest
from win32 import access_policy as ap


@pytest.fixture()
def policy_path(monkeypatch, tmp_path):
    p = tmp_path / "access-policy.json"
    monkeypatch.setattr(ap, "_POLICY_PATH", str(p))
    return p


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def test_missing_policy_status(policy_path):
    policy, status = ap.read_policy()
    assert status == "missing" and policy == {}
    with pytest.raises(PermissionError, match="fail-closed"):
        ap.enforce_policy_for_action("host.info", "tok")


def test_corrupt_policy_status(policy_path):
    policy_path.write_text("[1,2,3]")
    _, status = ap.read_policy()
    assert status == "corrupt"
    policy_path.write_text("{broken")
    _, status = ap.read_policy()
    assert status == "corrupt"


def test_token_exempt_only_capabilities(policy_path):
    ap.enforce_policy_for_action("host.capabilities", None)  # never raises
    with pytest.raises(PermissionError):
        ap.enforce_policy_for_action("host.capabilities.evil", None)


def test_enforce_happy_and_bad_token(policy_path):
    policy_path.write_text(json.dumps({"accessTokenSha256": _sha("s3cret")}))
    ap.enforce_policy_for_action("process.run", "s3cret")
    with pytest.raises(PermissionError, match="invalid access token"):
        ap.enforce_policy_for_action("process.run", "s3cretX")
    with pytest.raises(PermissionError, match="access token required"):
        ap.enforce_policy_for_action("process.run", None)


def test_null_token_hash_denies_all(policy_path):
    policy_path.write_text(json.dumps({"accessTokenSha256": None}))
    with pytest.raises(PermissionError, match="null token"):
        ap.enforce_policy_for_action("host.info", "anything")


def test_write_policy_installs_hashed_token(policy_path):
    policy = ap.write_policy({"token": "abc", "label": "L"})
    assert policy["accessTokenSha256"] == _sha("abc")
    on_disk = json.loads(policy_path.read_text())
    assert on_disk["accessTokenSha256"] == _sha("abc")
    assert "abc" not in policy_path.read_text()  # never stored in the clear


def test_write_policy_preserves_existing_token(policy_path):
    ap.write_policy({"token": "first"})
    policy = ap.write_policy({"label": "NEW"})
    assert policy["accessTokenSha256"] == _sha("first")


def test_write_policy_refuses_tokenless_cold_start(policy_path):
    with pytest.raises(ValueError, match="no token provided"):
        ap.write_policy({"label": "X"})
    assert not policy_path.exists()


def test_write_policy_disable_token_is_explicit(policy_path):
    ap.write_policy({"token": "t"})
    policy = ap.write_policy({"disableToken": True})  # explicit null-token flip
    assert policy["accessTokenSha256"] is None
    with pytest.raises(PermissionError):
        ap.enforce_policy_for_action("host.info", "t")


def test_write_policy_public_requires_token(policy_path):
    with pytest.raises(ValueError, match="public-with-token requires"):
        ap.write_policy({"disableToken": True, "exposureMode": "public-with-token"})


def test_write_policy_atomic_no_tmp_left(policy_path, tmp_path):
    ap.write_policy({"token": "t"})
    leftovers = [p.name for p in tmp_path.iterdir() if "wre-tmp" in p.name]
    assert leftovers == []
