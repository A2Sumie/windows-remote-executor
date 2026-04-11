#!/usr/bin/env python3
"""Minimal MCP server for Windows Remote Executor.

This server exposes structured tools around `windows-remote-executor/bin/win-remote`
so agent clients do not need to compose shell or PowerShell command strings.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SERVER_NAME = "windows-remote-executor"
SERVER_VERSION = "0.1.7"
PROTOCOL_VERSION = "2025-03-26"
WIN_REMOTE = Path(__file__).resolve().parents[1] / "bin" / "win-remote"


@dataclass
class CommandResult:
    argv: list[str]
    exit_code: int
    stdout: str
    stderr: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "argv": self.argv,
            "exitCode": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


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
                },
                "required": ["target", "program"],
                "additionalProperties": False,
            },
        },
        {
            "name": "win_wsl_script",
            "description": "Run a shell script through WSL without composing bash -lc command strings.",
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
                },
                "required": ["target", "script"],
                "additionalProperties": False,
            },
        },
        {
            "name": "win_wsl_script_capture",
            "description": "Run a shell script through WSL and return structured stdout/stderr capture.",
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
            "name": "win_exec_ps_file",
            "description": "Run a PowerShell file through the wrapper's controlled UTF-8/base64 path.",
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
            "description": "Run a PowerShell script body through stdin so the client never needs inline shell quoting.",
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
        if name == "win_probe":
            result = run_win_remote(["probe", require_str(arguments, "target")])
            return format_result(result, parse_stdout_json=True)

        if name == "win_run":
            argv = ["run", require_str(arguments, "target")]
            if cwd := optional_str(arguments, "cwd"):
                argv.extend(["--cwd", cwd])
            if arguments.get("allow_powershell") is True:
                argv.append("--allow-powershell")
            argv.append(require_str(arguments, "program"))
            argv.extend(optional_str_list(arguments, "args"))
            result = run_win_remote(argv)
            return format_result(result)

        if name == "win_capture":
            argv = ["capture", require_str(arguments, "target")]
            if cwd := optional_str(arguments, "cwd"):
                argv.extend(["--cwd", cwd])
            if arguments.get("allow_powershell") is True:
                argv.append("--allow-powershell")
            argv.append(require_str(arguments, "program"))
            argv.extend(optional_str_list(arguments, "args"))
            result = run_win_remote(argv)
            return format_result(result, parse_stdout_json=True)

        if name == "win_py":
            argv = ["py", require_str(arguments, "target"), require_str(arguments, "script_path")]
            if cwd := optional_str(arguments, "cwd"):
                argv.extend(["--cwd", cwd])
            if python_path := optional_str(arguments, "python_path"):
                argv.extend(["--python", python_path])
            if conda_env := optional_str(arguments, "conda_env"):
                argv.extend(["--conda-env", conda_env])
            if conda_prefix := optional_str(arguments, "conda_prefix"):
                argv.extend(["--conda-prefix", conda_prefix])
            script_args = optional_str_list(arguments, "script_args")
            if script_args:
                argv.append("--")
                argv.extend(script_args)
            result = run_win_remote(argv)
            return format_result(result)

        if name == "win_wsl":
            argv = ["wsl", require_str(arguments, "target")]
            if distribution := optional_str(arguments, "distribution"):
                argv.extend(["--distro", distribution])
            if user := optional_str(arguments, "user"):
                argv.extend(["--user", user])
            if cwd := optional_str(arguments, "cwd"):
                argv.extend(["--cwd", cwd])
            argv.append(require_str(arguments, "program"))
            argv.extend(optional_str_list(arguments, "args"))
            result = run_win_remote(argv)
            return format_result(result)

        if name == "win_wsl_capture":
            argv = ["wsl-capture", require_str(arguments, "target")]
            if distribution := optional_str(arguments, "distribution"):
                argv.extend(["--distro", distribution])
            if user := optional_str(arguments, "user"):
                argv.extend(["--user", user])
            if cwd := optional_str(arguments, "cwd"):
                argv.extend(["--cwd", cwd])
            argv.append(require_str(arguments, "program"))
            argv.extend(optional_str_list(arguments, "args"))
            result = run_win_remote(argv)
            return format_result(result, parse_stdout_json=True)

        if name == "win_wsl_script":
            argv = ["wsl-sh", require_str(arguments, "target")]
            if distribution := optional_str(arguments, "distribution"):
                argv.extend(["--distro", distribution])
            if user := optional_str(arguments, "user"):
                argv.extend(["--user", user])
            if cwd := optional_str(arguments, "cwd"):
                argv.extend(["--cwd", cwd])
            if shell := optional_str(arguments, "shell"):
                argv.extend(["--shell", shell])
            argv.extend(["--stdin"])
            script_args = optional_str_list(arguments, "script_args")
            if script_args:
                argv.append("--")
                argv.extend(script_args)
            result = run_win_remote(argv, stdin_text=require_str(arguments, "script"))
            return format_result(result)

        if name == "win_wsl_script_capture":
            argv = ["wsl-sh-capture", require_str(arguments, "target")]
            if distribution := optional_str(arguments, "distribution"):
                argv.extend(["--distro", distribution])
            if user := optional_str(arguments, "user"):
                argv.extend(["--user", user])
            if cwd := optional_str(arguments, "cwd"):
                argv.extend(["--cwd", cwd])
            if shell := optional_str(arguments, "shell"):
                argv.extend(["--shell", shell])
            argv.extend(["--stdin"])
            script_args = optional_str_list(arguments, "script_args")
            if script_args:
                argv.append("--")
                argv.extend(script_args)
            result = run_win_remote(argv, stdin_text=require_str(arguments, "script"))
            return format_result(result, parse_stdout_json=True)

        if name == "win_put":
            result = run_win_remote(
                [
                    "put",
                    require_str(arguments, "target"),
                    require_str(arguments, "local_path"),
                    require_str(arguments, "remote_path"),
                ]
            )
            return format_result(result)

        if name == "win_get":
            result = run_win_remote(
                [
                    "get",
                    require_str(arguments, "target"),
                    require_str(arguments, "remote_path"),
                    require_str(arguments, "local_path"),
                ]
            )
            return format_result(result)

        if name == "win_guard":
            argv = ["guard", require_str(arguments, "target")]
            if arguments.get("no_disable") is True:
                argv.append("--no-disable")
            if expected := optional_str(arguments, "expected_listen_address"):
                argv.extend(["--expected-listen-address", expected])
            result = run_win_remote(argv)
            return format_result(result)

        if name == "win_repair":
            argv = ["repair", require_str(arguments, "target")]
            if arguments.get("force_rewrite") is True:
                argv.append("--force-rewrite")
            if expected := optional_str(arguments, "expected_listen_address"):
                argv.extend(["--expected-listen-address", expected])
            result = run_win_remote(argv)
            return format_result(result)

        if name == "win_tasks":
            argv = ["tasks", require_str(arguments, "target")]
            for task_name in optional_str_list(arguments, "task_names"):
                argv.extend(["--task-name", task_name])
            if task_prefix := optional_str(arguments, "task_prefix"):
                argv.extend(["--prefix", task_prefix])
            result = run_win_remote(argv)
            return format_result(result, parse_stdout_json=True)

        if name == "win_exec_ps_file":
            result = run_win_remote(
                [
                    "exec",
                    require_str(arguments, "target"),
                    "--file",
                    require_str(arguments, "file_path"),
                ]
            )
            return format_result(result)

        if name == "win_exec_ps_script":
            result = run_win_remote(
                ["exec", require_str(arguments, "target"), "--stdin"],
                stdin_text=require_str(arguments, "script"),
            )
            return format_result(result)

        return tool_error(f"Unknown tool: {name}")
    except Exception as exc:  # noqa: BLE001
        return tool_error(str(exc))


def run_win_remote(argv: list[str], stdin_text: str | None = None) -> CommandResult:
    full_argv = [str(WIN_REMOTE), *argv]
    completed = subprocess.run(
        full_argv,
        input=stdin_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return CommandResult(
        argv=full_argv,
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def format_result(result: CommandResult, parse_stdout_json: bool = False) -> dict[str, Any]:
    payload = result.to_payload()
    payload["status"] = "ok" if result.exit_code == 0 else "error"
    if parse_stdout_json:
        stripped = result.stdout.strip()
        if stripped:
            try:
                payload["parsedStdout"] = json.loads(stripped)
            except json.JSONDecodeError:
                pass

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    response: dict[str, Any] = {
        "content": [{"type": "text", "text": text}],
        "isError": result.exit_code != 0,
    }
    if "parsedStdout" in payload:
        response["structuredContent"] = payload["parsedStdout"]
    else:
        response["structuredContent"] = payload
    return response


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
