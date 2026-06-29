#!/usr/bin/env python3
"""V3 rpc-stdio client for Windows Remote Executor."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from dataclasses import dataclass
from typing import Any

import win_remote_cli as cli


class UnsupportedRpc(cli.WinRemoteError):
    pass


class RpcTransportError(cli.WinRemoteError):
    pass


class RpcProtocolError(cli.WinRemoteError):
    pass


@dataclass
class RpcCall:
    target: cli.Target
    request: dict[str, Any]
    response: dict[str, Any]
    argv: list[str]
    ssh_returncode: int
    ssh_stderr: str

    @property
    def ok(self) -> bool:
        return bool(self.response.get("ok"))

    @property
    def exit_code(self) -> int:
        value = self.response.get("exitCode")
        return value if isinstance(value, int) else self.ssh_returncode


def call_rpc(
    target: cli.Target,
    action: str,
    payload: dict[str, Any] | None = None,
    *,
    request_id: str | None = None,
    timeout_seconds: int | None = None,
    capture_limit_bytes: int | None = None,
    check_support: bool = True,
) -> RpcCall:
    request = build_rpc_request(
        action,
        payload,
        request_id=request_id,
        timeout_seconds=timeout_seconds,
        capture_limit_bytes=capture_limit_bytes,
        access_token=target.access_token,
    )
    transport_timeout = timeout_seconds + 30 if timeout_seconds and timeout_seconds > 0 else None
    return call_rpc_request(target, request, check_support=check_support, transport_timeout_seconds=transport_timeout)


def host_capabilities(target: cli.Target) -> RpcCall:
    return call_rpc(target, "host.capabilities")


def host_probe(target: cli.Target) -> RpcCall:
    return call_rpc(target, "host.probe")


def process_capture(
    target: cli.Target,
    file: str,
    args: list[str] | None = None,
    *,
    cwd: str | None = None,
    timeout_seconds: int | None = None,
    capture_limit_bytes: int | None = None,
    allow_powershell: bool = False,
) -> RpcCall:
    cli.guard_raw_powershell("capture", allow_powershell, file)
    payload = {
        "file": cli.normalize_remote_path(file),
        "cwd": cli.normalize_remote_path(cwd) if cwd else None,
        "args": args or [],
    }
    return call_rpc(
        target,
        "process.capture",
        payload,
        timeout_seconds=timeout_seconds,
        capture_limit_bytes=capture_limit_bytes,
    )


def script_capture(
    target: cli.Target,
    script: str,
    *,
    kind: str = "powershell",
    cwd: str | None = None,
    exe: str | None = None,
    timeout_seconds: int | None = None,
    capture_limit_bytes: int | None = None,
) -> RpcCall:
    payload = {
        "kind": kind,
        "script": script,
        "cwd": cli.normalize_remote_path(cwd) if cwd else None,
        "exe": cli.normalize_remote_path(exe) if exe else None,
    }
    return call_rpc(
        target,
        "script.capture",
        payload,
        timeout_seconds=timeout_seconds,
        capture_limit_bytes=capture_limit_bytes,
    )


def file_write_text(target: cli.Target, path: str, text: str) -> RpcCall:
    return call_rpc(target, "file.writeText", {"path": cli.normalize_remote_path(path), "text": text})


def file_read_text(target: cli.Target, path: str, *, max_bytes: int | None = None) -> RpcCall:
    return call_rpc(target, "file.readText", {"path": cli.normalize_remote_path(path), "maxBytes": max_bytes})


def build_rpc_request(
    action: str,
    payload: dict[str, Any] | None = None,
    *,
    request_id: str | None = None,
    timeout_seconds: int | None = None,
    capture_limit_bytes: int | None = None,
    access_token: str | None = None,
) -> dict[str, Any]:
    if not action or not action.strip():
        raise cli.WinRemoteError("rpc action is required", 2)
    if timeout_seconds is not None and timeout_seconds <= 0:
        raise cli.WinRemoteError("timeout_seconds must be positive", 2)
    if capture_limit_bytes is not None and capture_limit_bytes <= 0:
        raise cli.WinRemoteError("capture_limit_bytes must be positive", 2)

    request = {
        "id": request_id or f"rpc-{uuid.uuid4().hex}",
        "action": action,
        "timeoutSeconds": timeout_seconds,
        "captureLimitBytes": capture_limit_bytes,
        "accessToken": access_token,
        "payload": payload or {},
    }
    return cli.compact_none(request)  # type: ignore[return-value]


def request_json_line(request: dict[str, Any]) -> str:
    return json.dumps(cli.compact_none(request), ensure_ascii=False, separators=(",", ":")) + "\n"


def target_supports_rpc(target: cli.Target) -> bool:
    cache_name = f"_WIN_REMOTE_SUPPORTS_RPC_{target.name}"
    cached = os.environ.get(cache_name)
    if cached == "1":
        return True
    if cached == "0":
        return False
    result = cli.run_remote_native(target, ["help"], capture_output=True)
    supported = isinstance(result, subprocess.CompletedProcess) and result.returncode == 0 and "rpc-stdio" in result.stdout
    os.environ[cache_name] = "1" if supported else "0"
    return supported


def call_rpc_request(
    target: cli.Target,
    request: dict[str, Any],
    *,
    check_support: bool = True,
    transport_timeout_seconds: int | None = None,
) -> RpcCall:
    if check_support and not target_supports_rpc(target):
        raise UnsupportedRpc("target native executor does not support rpc-stdio", 2)

    stdin_text = request_json_line(request)
    remote = build_rpc_remote_command(target)
    argv = [*target.ssh_args, target.ssh_destination, remote]
    try:
        completed = subprocess.run(
            argv,
            input=stdin_text,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=transport_timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RpcTransportError(f"rpc-stdio transport timed out after {transport_timeout_seconds}s", 124) from exc

    response = parse_rpc_response(completed.stdout, completed.stderr, completed.returncode)
    return RpcCall(
        target=target,
        request=request,
        response=response,
        argv=argv,
        ssh_returncode=completed.returncode,
        ssh_stderr=completed.stderr,
    )


def build_rpc_remote_command(target: cli.Target) -> str:
    native_path = cli.resolve_native_path(target)
    return cli.build_remote_command(native_path, ["rpc-stdio"])


def parse_rpc_response(stdout: str, stderr: str, returncode: int) -> dict[str, Any]:
    candidates = [stdout.lstrip("\ufeff").strip()]
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if len(lines) > 1:
        candidates.extend([lines[-1].lstrip("\ufeff"), lines[0].lstrip("\ufeff")])

    for candidate in candidates:
        if not candidate:
            continue
        try:
            response = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(response, dict):
            return response
        raise RpcProtocolError("rpc-stdio response is not a JSON object", 1)

    detail = stderr.strip() or stdout.strip() or f"ssh exit code {returncode}"
    raise RpcProtocolError(f"rpc-stdio did not return a JSON response: {detail}", returncode or 1)
