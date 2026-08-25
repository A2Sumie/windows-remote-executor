"""WRE v4 controller-side client.

Sends one UTF-8 JSON request line over SSH stdin; reads one UTF-8 JSON
response line from stdout. The remote command is fixed to:
    pythonw.exe -I -X utf8 C:/CodexRemote/wre/rpc.py rpc-stdio
The payload never travels in argv.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
import uuid
from dataclasses import dataclass
from typing import Any

from .targets import Target


REMOTE_COMMAND = 'cmd.exe /d /s /c "C:/CodexRemote/wre/python/python.exe -I -X utf8 C:/CodexRemote/wre/rpc.py rpc-stdio"'

# A caller that passes no timeout used to get NO transport deadline at all —
# a hung ssh or native process suspended call() forever (v4 audit A6;
# backported 2026-08-25 from v6/controller/client.py H9).
DEFAULT_TRANSPORT_TIMEOUT_S = 600


class RpcError(Exception):
    pass


class RpcTransportError(RpcError):
    pass


class RpcProtocolError(RpcError):
    pass


@dataclass
class Call:
    target: Target
    request: dict[str, Any]
    response: dict[str, Any]
    argv: list[str]
    returncode: int

    @property
    def ok(self) -> bool:
        return bool(self.response.get("ok"))

    @property
    def error_class(self) -> str:
        return str(self.response.get("errorClass") or "")

    @property
    def data(self) -> dict[str, Any]:
        return self.response.get("data") or {}

    @property
    def stdout_text(self) -> str:
        return str(self.response.get("stdoutText") or "")

    @property
    def stderr_text(self) -> str:
        return str(self.response.get("stderrText") or "")

    def require_ok(self) -> "Call":
        if not self.ok:
            raise RpcError(f"{self.error_class}: {self.stderr_text}")
        return self


def call(
    target: Target,
    action: str,
    payload: dict[str, Any] | None = None,
    *,
    request_id: str | None = None,
    timeout_seconds: int | None = None,
    capture_limit_bytes: int | None = None,
) -> Call:
    request = build_request(
        action, payload,
        request_id=request_id,
        timeout_seconds=timeout_seconds,
        capture_limit_bytes=capture_limit_bytes,
        access_token=target.access_token,
    )
    return call_request(target, request, transport_timeout_seconds=_transport_timeout(timeout_seconds))


def build_request(
    action: str,
    payload: dict[str, Any] | None,
    *,
    request_id: str | None,
    timeout_seconds: int | None,
    capture_limit_bytes: int | None,
    access_token: str | None,
) -> dict[str, Any]:
    if not action:
        raise RpcError("action is required")
    request: dict[str, Any] = {
        "id": request_id or f"rpc-{uuid.uuid4().hex}",
        "action": action,
        "payload": payload or {},
    }
    if timeout_seconds:
        request["timeoutSeconds"] = timeout_seconds
    if capture_limit_bytes:
        request["captureLimitBytes"] = capture_limit_bytes
    if access_token:
        request["accessToken"] = access_token
    return request


def call_request(target: Target, request: dict[str, Any], *, transport_timeout_seconds: int | None) -> Call:
    stdin_text = json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n"
    argv = [*target.ssh_args, target.ssh_destination, REMOTE_COMMAND]
    try:
        completed = subprocess.run(
            argv,
            input=stdin_text,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=transport_timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RpcTransportError(
            f"rpc transport timed out after {transport_timeout_seconds}s"
        ) from exc

    response = parse_response(completed.stdout, completed.stderr, completed.returncode)
    return Call(target=target, request=request, response=response, argv=argv, returncode=completed.returncode)


def parse_response(stdout: str, stderr: str, returncode: int) -> dict[str, Any]:
    candidates = [stdout.lstrip("\ufeff").strip()]
    lines = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
    if len(lines) > 1:
        candidates.extend([lines[-1].lstrip("\ufeff"), lines[0].lstrip("\ufeff")])

    for cand in candidates:
        if not cand:
            continue
        try:
            response = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(response, dict):
            return response
        raise RpcProtocolError("rpc-stdio response is not a JSON object")

    detail = stderr.strip() or stdout.strip() or f"ssh exit code {returncode}"
    raise RpcProtocolError(f"rpc-stdio did not return JSON: {detail}")


def _transport_timeout(timeout_seconds: int | None) -> int:
    """Transport deadline for the ssh subprocess. An explicit action timeout
    gets +30 s of ssh/native overhead headroom; no timeout at all still gets
    the DEFAULT_TRANSPORT_TIMEOUT_S backstop so call() can never hang
    indefinitely."""
    if not timeout_seconds:
        return DEFAULT_TRANSPORT_TIMEOUT_S
    return timeout_seconds + 30