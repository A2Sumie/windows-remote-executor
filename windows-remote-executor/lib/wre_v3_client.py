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


def host_guard(
    target: cli.Target,
    *,
    expected_listen_address: str | None = None,
    log_path: str | None = None,
    no_disable: bool = False,
) -> RpcCall:
    return call_rpc(target, "host.guard", {
        "expectedListenAddress": expected_listen_address,
        "logPath": cli.normalize_remote_path(log_path) if log_path else None,
        "noDisable": no_disable,
    })


def host_repair(
    target: cli.Target,
    *,
    expected_listen_address: str | None = None,
    codex_root: str | None = None,
    log_path: str | None = None,
    force_rewrite: bool = False,
) -> RpcCall:
    return call_rpc(target, "host.repair", {
        "expectedListenAddress": expected_listen_address,
        "codexRoot": cli.normalize_remote_path(codex_root) if codex_root else None,
        "logPath": cli.normalize_remote_path(log_path) if log_path else None,
        "forceRewrite": force_rewrite,
    })


def host_tasks(target: cli.Target, *, task_names: list[str] | None = None, prefix: str | None = None) -> RpcCall:
    return call_rpc(target, "host.tasks", {"taskNames": task_names or [], "prefix": prefix})


def host_policy(
    target: cli.Target,
    *,
    exposure_mode: str = "private-only",
    command_mode: str = "standard",
    expected_listen_address: str | None = None,
    label: str | None = None,
    token: str | None = None,
) -> RpcCall:
    return call_rpc(target, "host.policy", {
        "exposureMode": exposure_mode,
        "commandMode": command_mode,
        "expectedListenAddress": expected_listen_address,
        "label": label,
        "token": token,
    })


def process_run(
    target: cli.Target,
    file: str,
    args: list[str] | None = None,
    *,
    cwd: str | None = None,
    timeout_seconds: int | None = None,
    capture_limit_bytes: int | None = None,
    allow_powershell: bool = False,
) -> RpcCall:
    cli.guard_raw_powershell("run", allow_powershell, file)
    return call_rpc(
        target,
        "process.run",
        process_payload(file, args, cwd=cwd),
        timeout_seconds=timeout_seconds,
        capture_limit_bytes=capture_limit_bytes,
    )


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
    return call_rpc(
        target,
        "process.capture",
        process_payload(file, args, cwd=cwd),
        timeout_seconds=timeout_seconds,
        capture_limit_bytes=capture_limit_bytes,
    )


def process_spawn(
    target: cli.Target,
    file: str,
    args: list[str] | None = None,
    *,
    cwd: str | None = None,
    stdout: str | None = None,
    stderr: str | None = None,
    allow_powershell: bool = False,
) -> RpcCall:
    cli.guard_raw_powershell("spawn", allow_powershell, file)
    return call_rpc(target, "process.spawn", process_payload(file, args, cwd=cwd, stdout=stdout, stderr=stderr))


def script_run(
    target: cli.Target,
    script: str,
    *,
    kind: str = "powershell",
    cwd: str | None = None,
    exe: str | None = None,
    timeout_seconds: int | None = None,
    capture_limit_bytes: int | None = None,
) -> RpcCall:
    return call_rpc(
        target,
        "script.run",
        script_payload(script, kind=kind, cwd=cwd, exe=exe),
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
    return call_rpc(
        target,
        "script.capture",
        script_payload(script, kind=kind, cwd=cwd, exe=exe),
        timeout_seconds=timeout_seconds,
        capture_limit_bytes=capture_limit_bytes,
    )


def python_run(
    target: cli.Target,
    script_path: str,
    args: list[str] | None = None,
    *,
    cwd: str | None = None,
    python: str | None = None,
    conda_env: str | None = None,
    conda_prefix: str | None = None,
) -> RpcCall:
    return call_rpc(target, "python.run", {
        "scriptPath": cli.normalize_remote_path(script_path),
        "cwd": cli.normalize_remote_path(cwd) if cwd else None,
        "python": cli.normalize_remote_path(python) if python else None,
        "condaEnv": conda_env,
        "condaPrefix": cli.normalize_remote_path(conda_prefix) if conda_prefix else None,
        "args": args or [],
    })


def wsl_run(
    target: cli.Target,
    file: str,
    args: list[str] | None = None,
    *,
    cwd: str | None = None,
    distribution: str | None = None,
    user: str | None = None,
) -> RpcCall:
    return call_rpc(target, "wsl.run", wsl_process_payload(file, args, cwd=cwd, distribution=distribution, user=user))


def wsl_capture(
    target: cli.Target,
    file: str,
    args: list[str] | None = None,
    *,
    cwd: str | None = None,
    distribution: str | None = None,
    user: str | None = None,
) -> RpcCall:
    return call_rpc(target, "wsl.capture", wsl_process_payload(file, args, cwd=cwd, distribution=distribution, user=user))


def wsl_script(
    target: cli.Target,
    script: str,
    args: list[str] | None = None,
    *,
    cwd: str | None = None,
    distribution: str | None = None,
    user: str | None = None,
    shell: str | None = None,
) -> RpcCall:
    return call_rpc(target, "wsl.script", wsl_script_payload(script, args, cwd=cwd, distribution=distribution, user=user, shell=shell))


def wsl_script_capture(
    target: cli.Target,
    script: str,
    args: list[str] | None = None,
    *,
    cwd: str | None = None,
    distribution: str | None = None,
    user: str | None = None,
    shell: str | None = None,
) -> RpcCall:
    return call_rpc(target, "wsl.script.capture", wsl_script_payload(script, args, cwd=cwd, distribution=distribution, user=user, shell=shell))


def wsl_resident(
    target: cli.Target,
    script: str,
    args: list[str] | None = None,
    *,
    cwd: str | None = None,
    distribution: str | None = None,
    user: str | None = None,
    shell: str | None = None,
    launch_path: str | None = None,
    pid_file: str | None = None,
    log_file: str | None = None,
    port: int | None = None,
    health_url: str | None = None,
    ready_timeout_seconds: int | None = None,
    settle_delay_seconds: int | None = None,
    poll_interval_ms: int | None = None,
    diagnostic_lines: int | None = None,
) -> RpcCall:
    stage_path = f"/tmp/windows-remote-executor-resident-src-{uuid.uuid4().hex}.sh"
    return call_rpc(target, "wsl.resident", {
        "stagePath": stage_path,
        "launchPath": launch_path,
        "cwd": cwd,
        "distribution": distribution,
        "user": user,
        "shell": shell,
        "pidFile": pid_file,
        "logFile": log_file,
        "port": port,
        "healthUrl": health_url,
        "readyTimeoutSeconds": ready_timeout_seconds,
        "settleDelaySeconds": settle_delay_seconds,
        "pollIntervalMilliseconds": poll_interval_ms,
        "diagnosticLines": diagnostic_lines,
        "args": args or [],
        "script": script,
    })


def file_write_text(target: cli.Target, path: str, text: str) -> RpcCall:
    return call_rpc(target, "file.writeText", {"path": cli.normalize_remote_path(path), "text": text})


def file_read_text(target: cli.Target, path: str, *, max_bytes: int | None = None) -> RpcCall:
    return call_rpc(target, "file.readText", {"path": cli.normalize_remote_path(path), "maxBytes": max_bytes})


def file_mkdir(target: cli.Target, path: str) -> RpcCall:
    return call_rpc(target, "file.mkdir", {"path": cli.normalize_remote_path(path)})


def file_delete_tree(target: cli.Target, path: str) -> RpcCall:
    return call_rpc(target, "file.deleteTree", {"path": cli.normalize_remote_path(path)})


def file_copy(target: cli.Target, source: str, destination: str) -> RpcCall:
    return call_rpc(target, "file.copy", {
        "source": cli.normalize_remote_path(source),
        "destination": cli.normalize_remote_path(destination),
    })


def everything_search(target: cli.Target, query: str, *, max_results: int | None = None) -> RpcCall:
    return call_rpc(target, "everything.search", {"query": query, "max": max_results})


def process_payload(
    file: str,
    args: list[str] | None,
    *,
    cwd: str | None = None,
    stdout: str | None = None,
    stderr: str | None = None,
) -> dict[str, Any]:
    return {
        "file": cli.normalize_remote_path(file),
        "cwd": cli.normalize_remote_path(cwd) if cwd else None,
        "stdout": cli.normalize_remote_path(stdout) if stdout else None,
        "stderr": cli.normalize_remote_path(stderr) if stderr else None,
        "args": args or [],
    }


def script_payload(script: str, *, kind: str, cwd: str | None = None, exe: str | None = None) -> dict[str, Any]:
    return {
        "kind": kind,
        "script": script,
        "cwd": cli.normalize_remote_path(cwd) if cwd else None,
        "exe": cli.normalize_remote_path(exe) if exe else None,
    }


def wsl_process_payload(
    file: str,
    args: list[str] | None,
    *,
    cwd: str | None,
    distribution: str | None,
    user: str | None,
) -> dict[str, Any]:
    return {
        "file": file,
        "cwd": cwd,
        "distribution": distribution,
        "user": user,
        "args": args or [],
    }


def wsl_script_payload(
    script: str,
    args: list[str] | None,
    *,
    cwd: str | None,
    distribution: str | None,
    user: str | None,
    shell: str | None,
) -> dict[str, Any]:
    return {
        "script": script,
        "cwd": cwd,
        "distribution": distribution,
        "user": user,
        "shell": shell,
        "args": args or [],
    }


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
