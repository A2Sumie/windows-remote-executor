"""WRE v4 access policy enforcement.

Reads `access-policy.json` next to the rpc.py file and enforces:
- optional access token (sha256)
- exposure mode label (informational)
- v4 has NO command-mode argv-only branch — argv is gone entirely.
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

_ACTIONS_REQUIRING_TOKEN = {
    "host.probe", "host.guard", "host.repair", "host.policy",
    "host.tasks.list", "host.tasks.detail",
    "host.task.create", "host.task.update", "host.task.run", "host.task.delete",
    "host.tasks.apply",
    "file.writeText", "file.readText", "file.mkdir",
    "file.deleteTree", "file.copy", "file.putBinary",
    "file.list", "file.search",
}


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def load_policy() -> dict[str, Any]:
    if not os.path.isfile(_POLICY_PATH):
        return {}
    try:
        with open(_POLICY_PATH, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def write_policy(payload: dict[str, Any]) -> str:
    expected_listen = payload.get("expectedListenAddress") or ""
    exposure_mode = (payload.get("exposureMode") or "private-only").lower()
    command_mode = "v4"  # lock to v4; old "argv-only"/"standard" are gone
    label = payload.get("label") or "PRIVATE-ONLY"
    token = payload.get("token")
    if exposure_mode == "public-with-token" and not token:
        raise ValueError("public-with-token requires an access token")
    token_sha = _hash_token(token) if token else None
    policy = {
        "expectedListenAddress": expected_listen,
        "exposureMode": exposure_mode,
        "commandMode": command_mode,
        "label": label,
        "accessTokenSha256": token_sha,
        "updatedAt": _now_iso(),
    }
    with open(_POLICY_PATH, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(policy, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return policy


def enforce_policy_for_action(action: str, access_token: str | None) -> None:
    if action in ("host.capabilities",):
        return
    policy = load_policy()
    token_sha = policy.get("accessTokenSha256")
    if not token_sha:
        return
    if action not in _ACTIONS_REQUIRING_TOKEN and action != "host.capabilities":
        return
    if not access_token:
        raise PermissionError(f"access token required for {action}")
    provided = _hash_token(str(access_token))
    if not hmac.compare_digest(provided, str(token_sha)):
        raise PermissionError(f"invalid access token for {action}")


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()