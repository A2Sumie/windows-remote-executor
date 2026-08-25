"""WRE v4 access policy enforcement — FAIL-CLOSED (v4-hardening backport).

Reads `access-policy.json` next to the rpc.py file and enforces:
- mandatory access token (sha256) on every action except `host.capabilities`
- exposure mode label (informational)
- v4 has NO command-mode argv-only branch — argv is gone entirely.

Fail-closed rules (backported 2026-08-25 from v5, fixing the 2026-08-18 v4
audit finding A1 — v4 previously failed OPEN on all of these):
- policy file missing or JSON corrupt  -> deny every action except
  `host.capabilities` (which stays open so a locked-out controller can still
  identify the bridge).
- `accessTokenSha256` null/absent      -> same total denial.
- Default-deny for new actions: the exemption list below is the ONLY way an
  action skips the token. Registering a new action in rpc.py automatically
  puts it behind the token — no opt-in list to forget.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
# access-policy.json lives at the v4 wre root (next to rpc.py), one level up.
_POLICY_PATH = os.path.join(os.path.dirname(_THIS_DIR), "access-policy.json")

# The ONLY actions callable without a valid token. Everything else — including
# actions added in the future — requires the token by default.
_TOKEN_EXEMPT_ACTIONS = frozenset({"host.capabilities"})

# Policy read statuses.
_STATUS_OK = "ok"
_STATUS_MISSING = "missing"
_STATUS_CORRUPT = "corrupt"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def read_policy() -> tuple[dict[str, Any], str]:
    """Return (policy_dict, status) where status is ok|missing|corrupt."""
    if not os.path.isfile(_POLICY_PATH):
        return {}, _STATUS_MISSING
    try:
        with open(_POLICY_PATH, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}, _STATUS_CORRUPT
    if not isinstance(data, dict):
        return {}, _STATUS_CORRUPT
    return data, _STATUS_OK


def load_policy() -> dict[str, Any]:
    """Compatibility helper for probe/guard/repair paths (status discarded)."""
    policy, _status = read_policy()
    return policy


def policy_status() -> str:
    """ok | missing | corrupt — surfaced by host.probe (policy category)."""
    _policy, status = read_policy()
    return status


def write_policy(payload: dict[str, Any]) -> dict[str, Any]:
    """Write access-policy.json from a host.policy payload.

    Token semantics:
      payload.token given        -> hash and install it
      payload.disableToken=true  -> explicitly write a token-less policy
                                    (fail-closed: everything but
                                    host.capabilities will be denied)
      otherwise                  -> PRESERVE the existing token hash; a bare
                                    host.policy call can never erase an
                                    installed token. If there is no existing
                                    token either, refuse to write a token-less
                                    policy (it would lock the controller out).
    """
    expected_listen = payload.get("expectedListenAddress") or ""
    exposure_mode = (payload.get("exposureMode") or "private-only").lower()
    command_mode = "v4"  # lock to v4; old "argv-only"/"standard" are gone
    label = payload.get("label") or "PRIVATE-ONLY"
    token = payload.get("token")
    disable_token = payload.get("disableToken") is True

    existing, _status = read_policy()
    existing_sha = existing.get("accessTokenSha256")

    if token:
        token_sha: str | None = _hash_token(str(token))
    elif disable_token:
        token_sha = None
    elif existing_sha:
        token_sha = existing_sha
    else:
        raise ValueError(
            "no token provided and no existing accessTokenSha256 to preserve; "
            "pass payload.token, or set disableToken=true to explicitly write "
            "a token-less policy (fail-closed: all actions will be denied)"
        )
    if exposure_mode == "public-with-token" and not token_sha:
        raise ValueError("public-with-token requires an access token")

    policy = {
        "expectedListenAddress": expected_listen,
        "exposureMode": exposure_mode,
        "commandMode": command_mode,
        "label": label,
        "accessTokenSha256": token_sha,
        "updatedAt": _now_iso(),
    }
    # Atomic write: tmp file in the same directory + os.replace, so a crash
    # mid-write cannot leave a truncated (fail-closed "corrupt") policy.
    tmp = f"{_POLICY_PATH}.wre-tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(policy, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, _POLICY_PATH)
    return policy


def enforce_policy_for_action(action: str, access_token: str | None) -> None:
    if action in _TOKEN_EXEMPT_ACTIONS:
        return
    policy, status = read_policy()
    if status != _STATUS_OK:
        raise PermissionError(
            f"access policy {status} ({_POLICY_PATH}); refusing all actions "
            "except host.capabilities (fail-closed)"
        )
    token_sha = policy.get("accessTokenSha256")
    if not token_sha:
        raise PermissionError(
            "access policy has no accessTokenSha256 (null token); refusing all "
            "actions except host.capabilities (fail-closed)"
        )
    if not access_token:
        raise PermissionError(f"access token required for {action}")
    provided = _hash_token(str(access_token))
    if not hmac.compare_digest(provided, str(token_sha)):
        raise PermissionError(f"invalid access token for {action}")


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
