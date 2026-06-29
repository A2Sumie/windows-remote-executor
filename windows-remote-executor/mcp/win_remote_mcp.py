#!/usr/bin/env python3
"""Minimal MCP server for Windows Remote Executor V3.

This server exposes structured tools around the rpc-stdio transport so agent
clients do not need to compose shell or PowerShell command strings.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


TOOL_ROOT = Path(__file__).resolve().parents[1]
LIB_DIR = TOOL_ROOT / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import win_remote_cli as cli  # noqa: E402
import wre_v3_client as v3  # noqa: E402


SERVER_NAME = "windows-remote-executor"
SERVER_VERSION = "0.3.0"
PROTOCOL_VERSION = "2025-03-26"


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in {"-h", "--help", "help"}:
        print(
            "Run as an MCP stdio server. Example:\n"
            "  python3 windows-remote-executor/mcp/win_remote_mcp.py",
            file=sys.stderr,
        )
        return 0

    while True:
        message = read_message()
        if message is None:
            return 0

        response = dispatch(message)
        if response is not None:
            write_message(response)


def dispatch(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")

    if method == "initialize":
        return ok(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )

    if method == "notifications/initialized":
        return None

    if method == "ping":
        return ok(request_id, {})

    if method == "tools/list":
        return ok(request_id, {"tools": tool_specs()})

    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        return ok(request_id, handle_tool_call(name, arguments))

    if method == "shutdown":
        return ok(request_id, {})

    if method == "exit":
        raise SystemExit(0)

    if request_id is None:
        return None

    return err(request_id, -32601, f"Method not found: {method}")


def tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": "win_probe",
            "description": "Collect a structured probe from a configured Windows target.",
            "inputSchema": {
                "type": "object",
                "properties": {"target": {"type": "string"}},
                "required": ["target"],
                "additionalProperties": False,
            },
        },
        {
            "name": "win_run",
            "description": "Run a native process on the Windows target without composing a shell string.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "program": {"type": "string"},
                    "args": {"type": "array", "items": {"type": "string"}},
                    "cwd": {"type": "string"},
                    "allow_powershell": {"type": "boolean"},
                },
                "required": ["target", "program"],
                "additionalProperties": False,
            },
        },
        {
            "name": "win_capture",
            "description": "Run a native process and return structured JSON plus raw byte metadata.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "program": {"type": "string"},
                    "args": {"type": "array", "items": {"type": "string"}},
                    "cwd": {"type": "string"},
                    "allow_powershell": {"type": "boolean"},
                },
                "required": ["target", "program"],
                "additionalProperties": False,
            },
        },
        {
            "name": "win_py",
            "description": "Run a Python script on the Windows target.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "script_path": {"type": "string"},
                    "script_args": {"type": "array", "items": {"type": "string"}},
                    "cwd": {"type": "string"},
                    "python_path": {"type": "string"},
                    "conda_env": {"type": "string"},
                    "conda_prefix": {"type": "string"},
                },
                "required": ["target", "script_path"],
                "additionalProperties": False,
            },
        },
        {
            "name": "win_wsl",
            "description": "Run a Linux program through WSL with structured distro/user/cwd arguments.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "program": {"type": "string"},
                    "args": {"type": "array", "items": {"type": "string"}},
                    "cwd": {"type": "string"},
                    "distribution": {"type": "string"},
                    "user": {"type": "string"},
                    "heartbeat_seconds": {"type": "integer", "minimum": 1},
                },
                "required": ["target", "program"],
                "additionalProperties": False,
            },
        },
        {
            "name": "win_wsl_capture",
            "description": "Run a Linux program through WSL and return structured stdout/stderr capture.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "program": {"type": "string"},
                    "args": {"type": "array", "items": {"type": "string"}},
                    "cwd": {"type": "string"},
                    "distribution": {"type": "string"},
                    "user": {"type": "string"},
                    "heartbeat_seconds": {"type": "integer", "minimum": 1},
                },
                "required": ["target", "program"],
                "additionalProperties": False,
            },
        },
        {
            "name": "win_wsl_py",
            "description": "Run a Python script or module inside WSL with explicit Python executable, cwd, and argv.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "python": {"type": "string"},
                    "script_path": {"type": "string"},
                    "module": {"type": "string"},
                    "script_args": {"type": "array", "items": {"type": "string"}},
                    "cwd": {"type": "string"},
                    "distribution": {"type": "string"},
                    "user": {"type": "string"},
                    "heartbeat_seconds": {"type": "integer", "minimum": 1},
                },
                "required": ["target"],
                "additionalProperties": False,
            },
        },
        {
            "name": "win_wsl_py_capture",
            "description": "Run a Python script or module inside WSL and return structured stdout/stderr capture.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "python": {"type": "string"},
                    "script_path": {"type": "string"},
                    "module": {"type": "string"},
                    "script_args": {"type": "array", "items": {"type": "string"}},
                    "cwd": {"type": "string"},
                    "distribution": {"type": "string"},
                    "user": {"type": "string"},
                    "heartbeat_seconds": {"type": "integer", "minimum": 1},
                },
                "required": ["target"],
                "additionalProperties": False,
            },
        },
        {
            "name": "win_wsl_script",
            "description": "Run a shell script through WSL through staged file transfer instead of composing bash -lc command strings.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "script": {"type": "string"},
                    "script_args": {"type": "array", "items": {"type": "string"}},
                    "cwd": {"type": "string"},
                    "distribution": {"type": "string"},
                    "user": {"type": "string"},
                    "shell": {"type": "string"},
                    "heartbeat_seconds": {"type": "integer", "minimum": 1},
                },
                "required": ["target", "script"],
                "additionalProperties": False,
            },
        },
        {
            "name": "win_wsl_script_capture",
            "description": "Run a shell script through WSL through staged file transfer and return structured stdout/stderr capture.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "script": {"type": "string"},
                    "script_args": {"type": "array", "items": {"type": "string"}},
                    "cwd": {"type": "string"},
                    "distribution": {"type": "string"},
                    "user": {"type": "string"},
                    "shell": {"type": "string"},
                    "heartbeat_seconds": {"type": "integer", "minimum": 1},
                },
                "required": ["target", "script"],
                "additionalProperties": False,
            },
        },
        {
            "name": "win_wsl_resident",
            "description": "Launch a WSL shell script as a verified resident process and return structured readiness diagnostics.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "script": {"type": "string"},
                    "script_args": {"type": "array", "items": {"type": "string"}},
                    "cwd": {"type": "string"},
                    "distribution": {"type": "string"},
                    "user": {"type": "string"},
                    "shell": {"type": "string"},
                    "pid_file": {"type": "string"},
                    "log_file": {"type": "string"},
                    "port": {"type": "integer", "minimum": 1},
                    "health_url": {"type": "string"},
                    "ready_timeout": {"type": "integer", "minimum": 1},
                    "settle_delay": {"type": "integer", "minimum": 0},
                    "poll_interval_ms": {"type": "integer", "minimum": 1},
                    "diag_lines": {"type": "integer", "minimum": 1},
                },
                "required": ["target", "script"],
                "additionalProperties": False,
            },
        },
        {
            "name": "win_put",
            "description": "Upload a local file to the Windows target.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "local_path": {"type": "string"},
                    "remote_path": {"type": "string"},
                },
                "required": ["target", "local_path", "remote_path"],
                "additionalProperties": False,
            },
        },
        {
            "name": "win_get",
            "description": "Download a file from the Windows target.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "remote_path": {"type": "string"},
                    "local_path": {"type": "string"},
                },
                "required": ["target", "remote_path", "local_path"],
                "additionalProperties": False,
            },
        },
        {
            "name": "win_guard",
            "description": "Validate sshd exposure policy on the Windows target.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "expected_listen_address": {"type": "string"},
                    "no_disable": {"type": "boolean"},
                },
                "required": ["target"],
                "additionalProperties": False,
            },
        },
        {
            "name": "win_repair",
            "description": "Repair sshd configuration and startup state on the Windows target.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "expected_listen_address": {"type": "string"},
                    "force_rewrite": {"type": "boolean"},
                },
                "required": ["target"],
                "additionalProperties": False,
            },
        },
        {
            "name": "win_tasks",
            "description": "Read scheduled-task state through the wrapper so task names with spaces stay structured.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "task_names": {"type": "array", "items": {"type": "string"}},
                    "task_prefix": {"type": "string"},
                },
                "required": ["target"],
                "additionalProperties": False,
            },
        },
        {
            "name": "win_exec",
            "description": "Run a staged non-argv script body through the native exec bridge without inline shell quoting.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "script": {"type": "string"},
                    "shell": {"type": "string", "enum": ["powershell", "cmd"]},
                    "cwd": {"type": "string"},
                },
                "required": ["target", "script"],
                "additionalProperties": False,
            },
        },
        {
            "name": "win_exec_capture",
            "description": "Run a staged non-argv script body and return structured stdout/stderr capture.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "script": {"type": "string"},
                    "shell": {"type": "string", "enum": ["powershell", "cmd"]},
                    "cwd": {"type": "string"},
                },
                "required": ["target", "script"],
                "additionalProperties": False,
            },
        },
        {
            "name": "win_exec_ps_file",
            "description": "Run a PowerShell file through the staged native exec bridge.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "file_path": {"type": "string"},
                },
                "required": ["target", "file_path"],
                "additionalProperties": False,
            },
        },
        {
            "name": "win_exec_ps_script",
            "description": "Run a PowerShell script body through the staged native exec bridge.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "script": {"type": "string"},
                },
                "required": ["target", "script"],
                "additionalProperties": False,
            },
        },
    ]


def handle_tool_call(name: str | None, arguments: dict[str, Any]) -> dict[str, Any]:
    if not name:
        return tool_error("Missing tool name.")

    try:
        target = load_target(arguments) if name.startswith("win_") and "target" in arguments else None

        if name == "win_probe":
            return format_rpc_call(v3.host_probe(require_target(target)))

        if name == "win_run":
            return format_rpc_call(v3.process_run(
                require_target(target),
                require_str(arguments, "program"),
                optional_str_list(arguments, "args"),
                cwd=optional_str(arguments, "cwd"),
                allow_powershell=arguments.get("allow_powershell") is True,
            ))

        if name == "win_capture":
            return format_rpc_call(v3.process_capture(
                require_target(target),
                require_str(arguments, "program"),
                optional_str_list(arguments, "args"),
                cwd=optional_str(arguments, "cwd"),
                allow_powershell=arguments.get("allow_powershell") is True,
            ))

        if name == "win_py":
            return format_rpc_call(v3.python_run(
                require_target(target),
                require_str(arguments, "script_path"),
                optional_str_list(arguments, "script_args"),
                cwd=optional_str(arguments, "cwd"),
                python=optional_str(arguments, "python_path"),
                conda_env=optional_str(arguments, "conda_env"),
                conda_prefix=optional_str(arguments, "conda_prefix"),
            ))

        if name == "win_wsl":
            return format_rpc_call(v3.wsl_run(
                require_target(target),
                require_str(arguments, "program"),
                optional_str_list(arguments, "args"),
                cwd=optional_str(arguments, "cwd"),
                distribution=optional_str(arguments, "distribution"),
                user=optional_str(arguments, "user"),
            ))

        if name == "win_wsl_capture":
            return format_rpc_call(v3.wsl_capture(
                require_target(target),
                require_str(arguments, "program"),
                optional_str_list(arguments, "args"),
                cwd=optional_str(arguments, "cwd"),
                distribution=optional_str(arguments, "distribution"),
                user=optional_str(arguments, "user"),
            ))

        if name == "win_wsl_py":
            program, args = build_wsl_python_call(arguments)
            return format_rpc_call(v3.wsl_run(
                require_target(target),
                program,
                args,
                cwd=optional_str(arguments, "cwd"),
                distribution=optional_str(arguments, "distribution"),
                user=optional_str(arguments, "user"),
            ))

        if name == "win_wsl_py_capture":
            program, args = build_wsl_python_call(arguments)
            return format_rpc_call(v3.wsl_capture(
                require_target(target),
                program,
                args,
                cwd=optional_str(arguments, "cwd"),
                distribution=optional_str(arguments, "distribution"),
                user=optional_str(arguments, "user"),
            ))

        if name == "win_wsl_script":
            return format_rpc_call(v3.wsl_script(
                require_target(target),
                require_str(arguments, "script"),
                optional_str_list(arguments, "script_args"),
                cwd=optional_str(arguments, "cwd"),
                distribution=optional_str(arguments, "distribution"),
                user=optional_str(arguments, "user"),
                shell=optional_str(arguments, "shell"),
            ))

        if name == "win_wsl_script_capture":
            return format_rpc_call(v3.wsl_script_capture(
                require_target(target),
                require_str(arguments, "script"),
                optional_str_list(arguments, "script_args"),
                cwd=optional_str(arguments, "cwd"),
                distribution=optional_str(arguments, "distribution"),
                user=optional_str(arguments, "user"),
                shell=optional_str(arguments, "shell"),
            ))

        if name == "win_wsl_resident":
            return format_rpc_call(v3.wsl_resident(
                require_target(target),
                require_str(arguments, "script"),
                optional_str_list(arguments, "script_args"),
                cwd=optional_str(arguments, "cwd"),
                distribution=optional_str(arguments, "distribution"),
                user=optional_str(arguments, "user"),
                shell=optional_str(arguments, "shell"),
                pid_file=optional_str(arguments, "pid_file"),
                log_file=optional_str(arguments, "log_file"),
                port=optional_int(arguments, "port"),
                health_url=optional_str(arguments, "health_url"),
                ready_timeout_seconds=optional_int(arguments, "ready_timeout"),
                settle_delay_seconds=optional_int(arguments, "settle_delay", allow_zero=True),
                poll_interval_ms=optional_int(arguments, "poll_interval_ms"),
                diagnostic_lines=optional_int(arguments, "diag_lines"),
            ))

        if name == "win_put":
            target_obj = require_target(target)
            local_path = Path(require_str(arguments, "local_path"))
            remote_path = cli.normalize_remote_path(require_str(arguments, "remote_path"))
            if not local_path.is_file():
                raise ValueError(f"Local file not found: {local_path}")
            mkdir_call = v3.file_mkdir(target_obj, cli.remote_parent(remote_path))
            if not mkdir_call.ok:
                return format_rpc_call(mkdir_call)
            cli.scp_to_remote(target_obj, local_path, remote_path)
            return tool_payload({"status": "ok", "transport": "v3", "localPath": str(local_path), "remotePath": remote_path})

        if name == "win_get":
            target_obj = require_target(target)
            remote_path = cli.normalize_remote_path(require_str(arguments, "remote_path"))
            local_path = Path(require_str(arguments, "local_path"))
            local_path.parent.mkdir(parents=True, exist_ok=True)
            cli.scp_from_remote(target_obj, remote_path, local_path)
            return tool_payload({"status": "ok", "transport": "v3", "remotePath": remote_path, "localPath": str(local_path)})

        if name == "win_guard":
            return format_rpc_call(v3.host_guard(
                require_target(target),
                expected_listen_address=optional_str(arguments, "expected_listen_address"),
                no_disable=arguments.get("no_disable") is True,
            ))

        if name == "win_repair":
            return format_rpc_call(v3.host_repair(
                require_target(target),
                expected_listen_address=optional_str(arguments, "expected_listen_address"),
                force_rewrite=arguments.get("force_rewrite") is True,
            ))

        if name == "win_tasks":
            return format_rpc_call(v3.host_tasks(
                require_target(target),
                task_names=optional_str_list(arguments, "task_names"),
                prefix=optional_str(arguments, "task_prefix"),
            ))

        if name == "win_exec":
            return format_rpc_call(v3.script_run(
                require_target(target),
                require_str(arguments, "script"),
                kind=optional_str(arguments, "shell") or "powershell",
                cwd=optional_str(arguments, "cwd"),
            ))

        if name == "win_exec_capture":
            return format_rpc_call(v3.script_capture(
                require_target(target),
                require_str(arguments, "script"),
                kind=optional_str(arguments, "shell") or "powershell",
                cwd=optional_str(arguments, "cwd"),
            ))

        if name == "win_exec_ps_file":
            script = Path(require_str(arguments, "file_path")).read_text(encoding="utf-8")
            return format_rpc_call(v3.script_run(require_target(target), script, kind="powershell"))

        if name == "win_exec_ps_script":
            return format_rpc_call(v3.script_run(require_target(target), require_str(arguments, "script"), kind="powershell"))

        return tool_error(f"Unknown tool: {name}")
    except Exception as exc:  # noqa: BLE001
        return tool_error(str(exc))


def require_target(target: cli.Target | None) -> cli.Target:
    if target is None:
        raise ValueError("'target' is required and must be a non-empty string.")
    return target


def build_wsl_python_call(arguments: dict[str, Any]) -> tuple[str, list[str]]:
    python = optional_str(arguments, "python") or "python3"
    module = optional_str(arguments, "module")
    script_path = optional_str(arguments, "script_path")
    if bool(module) == bool(script_path):
        raise ValueError("Provide exactly one of 'module' or 'script_path'.")
    args: list[str] = []
    if module:
        args.extend(["-m", module])
    else:
        args.append(script_path or "")
    args.extend(optional_str_list(arguments, "script_args"))
    return python, args


def format_rpc_call(call: v3.RpcCall) -> dict[str, Any]:
    payload = {
        "argv": call.argv,
        "exitCode": call.exit_code,
        "stdout": call.response.get("stdoutText", ""),
        "stderr": call.response.get("stderrText", "") or call.ssh_stderr,
        "transport": "v3",
        "status": "ok" if call.ok else "error",
        "rpc": call.response,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    return {
        "content": [{"type": "text", "text": text}],
        "isError": not call.ok,
        "structuredContent": call.response,
    }


def tool_payload(payload: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    return {
        "content": [{"type": "text", "text": text}],
        "isError": False,
        "structuredContent": payload,
    }

def load_target(arguments: dict[str, Any]) -> cli.Target:
    return cli.load_target(require_str(arguments, "target"))


def tool_error(message: str) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": message}],
        "isError": True,
    }


def require_str(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"'{key}' is required and must be a non-empty string.")
    return value


def optional_str(arguments: dict[str, Any], key: str) -> str | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"'{key}' must be a string when provided.")
    return value


def optional_str_list(arguments: dict[str, Any], key: str) -> list[str]:
    value = arguments.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"'{key}' must be an array of strings when provided.")
    return value


def optional_int(arguments: dict[str, Any], key: str, allow_zero: bool = False) -> int | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, int):
        raise ValueError(f"'{key}' must be an integer when provided.")
    if allow_zero:
        if value < 0:
            raise ValueError(f"'{key}' must be zero or greater.")
    elif value <= 0:
        raise ValueError(f"'{key}' must be greater than zero.")
    return value


def ok(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def err(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def read_message() -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in {b"\r\n", b"\n"}:
            break
        name, _, value = line.decode("utf-8").partition(":")
        headers[name.strip().lower()] = value.strip()

    try:
        content_length = int(headers["content-length"])
    except (KeyError, ValueError) as exc:
        raise RuntimeError("Missing or invalid Content-Length header.") from exc

    body = sys.stdin.buffer.read(content_length)
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def write_message(payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(encoded)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    raise SystemExit(main())
