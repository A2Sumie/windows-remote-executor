#!/usr/bin/env python3
"""Python front-end for Windows Remote Executor.

The v2 route sends user command data as one base64url JSON envelope to the
Windows native executor. The legacy bash implementation is still reachable with
WIN_REMOTE_LEGACY=1 for targets that have not been updated yet.
"""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, NoReturn


TOOL_ROOT = Path(__file__).resolve().parents[1]
BIN = TOOL_ROOT / "bin" / "win-remote"
TARGETS_DIR = TOOL_ROOT / "targets"
SCRIPT_ACTIVE_CHARS = re.compile(r"[\s'\"`&();|<>^!]")
DRIVE_RELATIVE = re.compile(r"^[A-Za-z]:($|[^/\\])")
RAW_POWERSHELL = {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}


class WinRemoteError(Exception):
    def __init__(self, message: str, exit_code: int = 1):
        super().__init__(message)
        self.exit_code = exit_code


class UnsupportedInvoke(WinRemoteError):
    pass


@dataclass
class Target:
    name: str
    env_file: Path
    host: str
    user: str
    port: str = "22"
    key: str | None = None
    access_token: str | None = None
    ps_exe: str = "powershell.exe"
    stage_root: str = "C:/CodexRemote/staging"
    native_exe: str = "C:/CodexRemote/tools/WindowsRemoteExecutor.Native.exe"
    native_launcher: str = "C:/CodexRemote/tools/WindowsRemoteExecutor.cmd"
    native_current_file: str = "C:/CodexRemote/tools/current-release.txt"
    tools_dir: str = "C:/CodexRemote/tools"
    native_releases_dir: str = "C:/CodexRemote/tools/releases"
    policy_path: str = "C:/CodexRemote/tools/access-policy.json"
    guard_log_path: str = "C:/CodexRemote/logs/sshd-guard.log"
    repair_log_path: str = "C:/CodexRemote/logs/sshd-repair.log"
    wsl_distro: str | None = None
    wsl_user: str | None = None
    wsl_shell: str = "/bin/bash"
    expected_listen_address: str | None = None

    @property
    def ssh_destination(self) -> str:
        return f"{self.user}@{self.host}"

    @property
    def ssh_args(self) -> list[str]:
        args = [
            "ssh",
            "-p",
            self.port,
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "KexAlgorithms=^mlkem768x25519-sha256,sntrup761x25519-sha512,sntrup761x25519-sha512@openssh.com",
            "-o",
            "WarnWeakCrypto=no",
        ]
        if self.key:
            args.extend(["-i", self.key])
        return args

    @property
    def scp_args(self) -> list[str]:
        args = [
            "scp",
            "-P",
            self.port,
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "KexAlgorithms=^mlkem768x25519-sha256,sntrup761x25519-sha512,sntrup761x25519-sha512@openssh.com",
            "-o",
            "WarnWeakCrypto=no",
        ]
        if self.key:
            args.extend(["-i", self.key])
        return args


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"help", "-h", "--help"}:
        print_usage()
        return 0

    subcommand = argv[0]
    args = argv[1:]

    try:
        if subcommand in {"probe", "ping"}:
            return cmd_probe(args)
        if subcommand == "run":
            return cmd_run(args)
        if subcommand == "capture":
            return cmd_capture(args)
        if subcommand == "spawn":
            return cmd_spawn(args)
        if subcommand == "exec":
            return cmd_exec(args, capture=False)
        if subcommand == "exec-capture":
            return cmd_exec(args, capture=True)
        if subcommand == "wsl":
            return cmd_wsl(args, capture=False)
        if subcommand == "wsl-capture":
            return cmd_wsl(args, capture=True)
        if subcommand == "py":
            return cmd_py(args)
        if subcommand == "put":
            return cmd_put(args)
        if subcommand == "get":
            return cmd_get(args)
        if subcommand == "guard":
            return cmd_guard(args)
        if subcommand == "repair":
            return cmd_repair(args)
        if subcommand == "update-tools":
            return cmd_update_tools(args)
        if subcommand == "selftest":
            return cmd_selftest()
        if subcommand in {"wsl-sh", "wsl-sh-capture", "wsl-resident", "tasks", "policy", "deploy", "find", "cmd", "ps-encode", "ps-decode", "ps-check", "wsl-py", "wsl-py-capture", "wsl-container-sh", "wsl-container-sh-capture"}:
            return run_legacy([subcommand, *args])
        raise WinRemoteError(f"Unknown subcommand: {subcommand}", 2)
    except UnsupportedInvoke:
        return run_legacy([subcommand, *args])
    except WinRemoteError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code


def cmd_probe(args: list[str]) -> int:
    if not args:
        raise WinRemoteError("probe requires <target>", 2)
    target = load_target(args[0])
    out_path: Path | None = None
    rest = args[1:]
    while rest:
        option = rest.pop(0)
        if option == "--out":
            out_path = Path(pop_value(rest, option))
        else:
            raise WinRemoteError(f"Unknown probe option: {option}", 2)
    result = invoke(target, {"action": "probe"}, capture_output=True)
    if out_path:
        out_path.write_text(result.stdout, encoding="utf-8")
    else:
        sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


def cmd_run(args: list[str]) -> int:
    target, cwd, allow_powershell, program, program_args = parse_process_command(args, "run")
    guard_raw_powershell("run", allow_powershell, program)
    return invoke_passthrough(target, {"action": "process.run", "file": program, "cwd": cwd, "args": program_args})


def cmd_capture(args: list[str]) -> int:
    target, cwd, out_path, allow_powershell, program, program_args = parse_capture_command(args)
    guard_raw_powershell("capture", allow_powershell, program)
    result = invoke(target, {"action": "process.capture", "file": program, "cwd": cwd, "args": program_args}, capture_output=True)
    if out_path:
        Path(out_path).write_text(result.stdout, encoding="utf-8")
    else:
        sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


def cmd_spawn(args: list[str]) -> int:
    if not args:
        raise WinRemoteError("spawn requires <target>", 2)
    target = load_target(args[0])
    rest = args[1:]
    cwd = stdout = stderr = out_path = None
    allow_powershell = False
    while rest and rest[0].startswith("--"):
        option = rest.pop(0)
        if option == "--cwd":
            cwd = pop_value(rest, option)
        elif option == "--stdout":
            stdout = normalize_remote_path(pop_value(rest, option))
        elif option == "--stderr":
            stderr = normalize_remote_path(pop_value(rest, option))
        elif option == "--out":
            out_path = pop_value(rest, option)
        elif option == "--allow-powershell":
            allow_powershell = True
        else:
            raise WinRemoteError(f"Unknown spawn option: {option}", 2)
    if not rest:
        raise WinRemoteError("spawn requires <program> [args...]", 2)
    program, program_args = rest[0], rest[1:]
    guard_raw_powershell("spawn", allow_powershell, program)
    request = {"action": "process.spawn", "file": program, "cwd": cwd, "stdout": stdout, "stderr": stderr, "args": program_args}
    result = invoke(target, request, capture_output=True)
    if out_path:
        Path(out_path).write_text(result.stdout, encoding="utf-8")
    else:
        sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


def cmd_exec(args: list[str], *, capture: bool) -> int:
    if not args:
        raise WinRemoteError("exec requires <target>", 2)
    target_name = args[0]
    target = load_target(target_name)
    if not target_supports_invoke(target):
        raise UnsupportedInvoke("target native executor does not support invoke-b64")

    rest = args[1:]
    cwd = None
    shell = "powershell"
    out_path = None
    while rest and rest[0].startswith("--"):
        option = rest.pop(0)
        if option == "--cwd":
            cwd = pop_value(rest, option)
        elif option == "--shell":
            shell = normalize_exec_shell(pop_value(rest, option))
        elif capture and option == "--out":
            out_path = pop_value(rest, option)
        elif option in {"--file", "--stdin"}:
            rest.insert(0, option)
            break
        else:
            raise WinRemoteError(f"Unknown {'exec-capture' if capture else 'exec'} option: {option}", 2)

    cleanup_dir: tempfile.TemporaryDirectory[str] | None = None
    local_script: Path
    if rest and rest[0] == "--file":
        rest.pop(0)
        local_script = Path(pop_value(rest, "--file"))
        if rest:
            raise WinRemoteError("exec --file does not accept extra arguments", 2)
        if not local_script.is_file():
            raise WinRemoteError(f"Local file not found: {local_script}", 2)
    elif rest and rest[0] == "--stdin":
        rest.pop(0)
        if rest:
            raise WinRemoteError("exec --stdin does not accept extra arguments", 2)
        cleanup_dir = tempfile.TemporaryDirectory(prefix="win-remote-exec-")
        local_script = Path(cleanup_dir.name) / f"payload.{exec_suffix(shell)}"
        local_script.write_text(sys.stdin.read(), encoding="utf-8")
    elif rest:
        cleanup_dir = tempfile.TemporaryDirectory(prefix="win-remote-exec-")
        local_script = Path(cleanup_dir.name) / f"payload.{exec_suffix(shell)}"
        local_script.write_text(" ".join(rest), encoding="utf-8")
    else:
        raise WinRemoteError("exec requires inline script, --file <local-script>, or --stdin", 2)

    stage_dir = f"{target.stage_root}/exec-{stage_id()}"
    remote_script = f"{stage_dir}/payload.{exec_suffix(shell)}"
    try:
        invoke(target, {"action": "file.mkdir", "path": stage_dir}, capture_output=True)
        staged_upload_file(target, local_script, remote_script)
        request = {
            "action": "script.capture" if capture else "script.run",
            "kind": shell,
            "scriptPath": remote_script,
            "cwd": cwd,
            "exe": target.ps_exe if shell == "powershell" else None,
        }
        result = invoke(target, request, capture_output=capture)
        if capture:
            assert isinstance(result, subprocess.CompletedProcess)
            if out_path:
                Path(out_path).write_text(result.stdout, encoding="utf-8")
            else:
                sys.stdout.write(result.stdout)
            sys.stderr.write(result.stderr)
            return result.returncode
        assert isinstance(result, int)
        return result
    finally:
        try:
            invoke(target, {"action": "file.delete-tree", "path": stage_dir}, capture_output=True)
        except Exception:
            pass
        if cleanup_dir is not None:
            cleanup_dir.cleanup()


def cmd_wsl(args: list[str], *, capture: bool) -> int:
    if not args:
        raise WinRemoteError("wsl requires <target>", 2)
    target = load_target(args[0])
    rest = args[1:]
    distro = target.wsl_distro
    user = target.wsl_user
    cwd = None
    out_path = None
    while rest and rest[0].startswith("--"):
        option = rest.pop(0)
        if option == "--distro":
            distro = pop_value(rest, option)
        elif option == "--user":
            user = pop_value(rest, option)
        elif option == "--cwd":
            cwd = pop_value(rest, option)
        elif capture and option == "--out":
            out_path = pop_value(rest, option)
        elif option == "--heartbeat-seconds":
            raise UnsupportedInvoke("heartbeat wrapper is still served by legacy wsl route")
        else:
            raise WinRemoteError(f"Unknown {'wsl-capture' if capture else 'wsl'} option: {option}", 2)
    if not rest:
        raise WinRemoteError("wsl requires <program> [args...]", 2)
    program, program_args = rest[0], rest[1:]
    request = {"action": "wsl.capture" if capture else "wsl.run", "file": program, "cwd": cwd, "distribution": distro, "user": user, "args": program_args}
    if capture:
        result = invoke(target, request, capture_output=True)
        if out_path:
            Path(out_path).write_text(result.stdout, encoding="utf-8")
        else:
            sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        return result.returncode
    return invoke_passthrough(target, request)


def cmd_py(args: list[str]) -> int:
    if len(args) < 2:
        raise WinRemoteError("py requires <target> <remote-script-path>", 2)
    target = load_target(args[0])
    script_path = args[1]
    rest = args[2:]
    cwd = python = conda_env = conda_prefix = None
    script_args: list[str] = []
    while rest:
        option = rest.pop(0)
        if option == "--cwd":
            cwd = pop_value(rest, option)
        elif option == "--python":
            python = pop_value(rest, option)
        elif option == "--conda-env":
            conda_env = pop_value(rest, option)
        elif option == "--conda-prefix":
            conda_prefix = pop_value(rest, option)
        elif option == "--":
            script_args = rest
            rest = []
        else:
            raise WinRemoteError(f"Unknown py option: {option}", 2)
    return invoke_passthrough(target, {
        "action": "python.run",
        "scriptPath": script_path,
        "cwd": cwd,
        "python": python,
        "condaEnv": conda_env,
        "condaPrefix": conda_prefix,
        "args": script_args,
    })


def cmd_put(args: list[str]) -> int:
    if len(args) != 3:
        raise WinRemoteError("put requires <target> <local-path> <remote-path>", 2)
    target = load_target(args[0])
    if not target_supports_invoke(target):
        raise UnsupportedInvoke("target native executor does not support invoke-b64")
    local_path = Path(args[1])
    remote_path = normalize_remote_path(args[2])
    if not local_path.is_file():
        raise UnsupportedInvoke("v2 staged put currently handles files; using legacy for this path")
    staged_upload_file(target, local_path, remote_path)
    print("OK")
    return 0


def staged_upload_file(target: Target, local_path: Path, remote_path: str) -> None:
    stage_dir = f"{target.stage_root}/file-transfer-{stage_id()}"
    remote_stage_path = f"{stage_dir}/payload"
    try:
        invoke(target, {"action": "file.mkdir", "path": stage_dir}, capture_output=True)
        scp_to_remote(target, local_path, remote_stage_path)
        invoke(target, {"action": "file.copy", "source": remote_stage_path, "destination": remote_path}, capture_output=True)
    finally:
        try:
            invoke(target, {"action": "file.delete-tree", "path": stage_dir}, capture_output=True)
        except Exception:
            pass


def cmd_get(args: list[str]) -> int:
    if len(args) != 3:
        raise WinRemoteError("get requires <target> <remote-path> <local-path>", 2)
    target = load_target(args[0])
    if not target_supports_invoke(target):
        raise UnsupportedInvoke("target native executor does not support invoke-b64")
    remote_path = normalize_remote_path(args[1])
    local_path = Path(args[2])
    stage_dir = f"{target.stage_root}/file-transfer-{stage_id()}"
    remote_stage_path = f"{stage_dir}/payload"
    try:
        invoke(target, {"action": "file.mkdir", "path": stage_dir}, capture_output=True)
        invoke(target, {"action": "file.copy", "source": remote_path, "destination": remote_stage_path}, capture_output=True)
        scp_from_remote(target, remote_stage_path, local_path)
        print("OK")
        return 0
    finally:
        try:
            invoke(target, {"action": "file.delete-tree", "path": stage_dir}, capture_output=True)
        except Exception:
            pass


def cmd_guard(args: list[str]) -> int:
    if not args:
        raise WinRemoteError("guard requires <target>", 2)
    target = load_target(args[0])
    rest = args[1:]
    request: dict[str, object] = {"action": "guard.run"}
    out_path = None
    while rest:
        option = rest.pop(0)
        if option == "--out":
            out_path = pop_value(rest, option)
        elif option == "--no-disable":
            request["noDisable"] = True
        elif option == "--expected-listen-address":
            request["expectedListenAddress"] = pop_value(rest, option)
        else:
            raise WinRemoteError(f"Unknown guard option: {option}", 2)
    result = invoke(target, request, capture_output=True)
    if out_path:
        Path(out_path).write_text(result.stdout, encoding="utf-8")
    else:
        sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


def cmd_repair(args: list[str]) -> int:
    if not args:
        raise WinRemoteError("repair requires <target>", 2)
    target = load_target(args[0])
    rest = args[1:]
    request: dict[str, object] = {"action": "repair.run"}
    out_path = None
    while rest:
        option = rest.pop(0)
        if option == "--out":
            out_path = pop_value(rest, option)
        elif option == "--expected-listen-address":
            request["expectedListenAddress"] = pop_value(rest, option)
        elif option == "--force-rewrite":
            request["forceRewrite"] = True
        else:
            raise WinRemoteError(f"Unknown repair option: {option}", 2)
    result = invoke(target, request, capture_output=True)
    if out_path:
        Path(out_path).write_text(result.stdout, encoding="utf-8")
    else:
        sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


def cmd_update_tools(args: list[str]) -> int:
    if not args:
        raise WinRemoteError("update-tools requires <target>", 2)
    if "--native-dir" in args or "--native-exe" in args:
        raise WinRemoteError("production update-tools no longer accepts --native-dir or --native-exe; use --native-zip <GitHub-release-asset.zip> or WIN_REMOTE_LEGACY=1 for explicit dev smoke work", 2)
    if "--native-zip" not in args:
        raise WinRemoteError("production update-tools requires --native-zip <GitHub-release-asset.zip>", 2)
    zip_path = Path(args[args.index("--native-zip") + 1])
    validate_release_native_zip(zip_path)
    return run_legacy(["update-tools", *args])


def validate_release_native_zip(path: Path) -> None:
    if not path.is_file():
        raise WinRemoteError(f"Native release zip not found: {path}", 2)
    name = path.name
    if not re.match(r"windows-remote-executor-native-v[^/]+-fdd-win-x64\.zip$", name):
        raise WinRemoteError(
            "production update-tools requires the framework-dependent GitHub release asset named "
            "windows-remote-executor-native-v<version>-fdd-win-x64.zip",
            2,
        )
    try:
        with zipfile.ZipFile(path) as zf:
            names = {Path(info.filename).name for info in zf.infolist() if not info.is_dir()}
    except zipfile.BadZipFile as exc:
        raise WinRemoteError(f"Invalid native release zip: {path}", 2) from exc
    required = {
        "WindowsRemoteExecutor.Native.exe",
        "WindowsRemoteExecutor.Native.dll",
        "WindowsRemoteExecutor.Native.runtimeconfig.json",
        "WindowsRemoteExecutor.Native.deps.json",
    }
    missing = sorted(required - names)
    if missing:
        raise WinRemoteError(f"Native release zip is missing required files: {', '.join(missing)}", 2)


def cmd_selftest() -> int:
    hostile = [
        "plain",
        "space arg",
        "quote \" arg",
        "tick ` dollar $ paren $(x)",
        "percent %PATH% bang !VAR! amp & pipe | lt < gt >",
        "json {\"a\":[1,2]}",
        "line1\nline2",
    ]
    request = {"action": "process.capture", "file": "C:/Tools/echo.exe", "cwd": "D:/Work Dir", "args": hostile}
    envelope = encode_envelope(request)
    decoded = json.loads(base64_url_decode(envelope).decode("utf-8"))
    ok = decoded == request and not SCRIPT_ACTIVE_CHARS.search(envelope)
    print(json.dumps({"ok": ok, "envelopeLength": len(envelope), "decoded": decoded}, ensure_ascii=False))
    return 0 if ok else 1


def parse_process_command(args: list[str], command_name: str) -> tuple[Target, str | None, bool, str, list[str]]:
    if not args:
        raise WinRemoteError(f"{command_name} requires <target>", 2)
    target = load_target(args[0])
    rest = args[1:]
    cwd = None
    allow_powershell = False
    while rest and rest[0].startswith("--"):
        option = rest.pop(0)
        if option == "--cwd":
            cwd = pop_value(rest, option)
        elif option == "--allow-powershell":
            allow_powershell = True
        else:
            raise WinRemoteError(f"Unknown {command_name} option: {option}", 2)
    if not rest:
        raise WinRemoteError(f"{command_name} requires <program> [args...]", 2)
    return target, cwd, allow_powershell, rest[0], rest[1:]


def parse_capture_command(args: list[str]) -> tuple[Target, str | None, str | None, bool, str, list[str]]:
    if not args:
        raise WinRemoteError("capture requires <target>", 2)
    target = load_target(args[0])
    rest = args[1:]
    cwd = out_path = None
    allow_powershell = False
    if rest and rest[0] == "--cmd":
        raise WinRemoteError("capture --cmd is still served by legacy because it is intentionally shell-shaped", 2)
    while rest and rest[0].startswith("--"):
        option = rest.pop(0)
        if option == "--cwd":
            cwd = pop_value(rest, option)
        elif option == "--out":
            out_path = pop_value(rest, option)
        elif option == "--allow-powershell":
            allow_powershell = True
        elif option == "--cmd":
            raise UnsupportedInvoke("capture --cmd is still served by legacy")
        else:
            raise WinRemoteError(f"Unknown capture option: {option}", 2)
    if not rest:
        raise WinRemoteError("capture requires <program> [args...]", 2)
    return target, cwd, out_path, allow_powershell, rest[0], rest[1:]


def invoke_passthrough(target: Target, request: dict[str, object]) -> int:
    return invoke(target, request, capture_output=False)


def invoke(target: Target, request: dict[str, object], *, capture_output: bool) -> subprocess.CompletedProcess[str] | int:
    if not target_supports_invoke(target):
        raise UnsupportedInvoke("target native executor does not support invoke-b64")
    envelope = encode_envelope(compact_none(request))
    native_args = ["invoke-b64", envelope]
    if target.access_token:
        native_args.extend(["--access-token", b64_utf8(target.access_token)])
    result = run_remote_native(target, native_args, capture_output=capture_output)
    if isinstance(result, subprocess.CompletedProcess):
        if result.returncode == 1 and "Unknown command: invoke-b64" in result.stderr:
            raise UnsupportedInvoke("target native executor rejected invoke-b64")
        return result
    return result


def target_supports_invoke(target: Target) -> bool:
    cache_name = f"_WIN_REMOTE_SUPPORTS_INVOKE_{target.name}"
    cached = os.environ.get(cache_name)
    if cached == "1":
        return True
    if cached == "0":
        return False
    result = run_remote_native(target, ["help"], capture_output=True)
    supported = isinstance(result, subprocess.CompletedProcess) and result.returncode == 0 and "invoke-b64" in result.stdout
    os.environ[cache_name] = "1" if supported else "0"
    return supported


def run_remote_native(target: Target, native_args: list[str], *, capture_output: bool) -> subprocess.CompletedProcess[str] | int:
    native_path = resolve_native_path(target)
    remote = build_remote_command(native_path, native_args)
    argv = [*target.ssh_args, target.ssh_destination, remote]
    if capture_output:
        return subprocess.run(argv, text=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    return subprocess.run(argv, stdin=subprocess.DEVNULL, check=False).returncode


def resolve_native_path(target: Target) -> str:
    current = read_current_release(target)
    if current:
        candidate = normalize_remote_path(f"{current.rstrip('/')}/WindowsRemoteExecutor.Native.exe")
        if remote_file_exists(target, candidate):
            return candidate
    if remote_file_exists(target, target.native_exe):
        return target.native_exe
    return target.native_launcher


def read_current_release(target: Target) -> str | None:
    path = remote_cmd_path(target.native_current_file)
    code = f'if exist "{path}" type "{path}"'
    result = ssh_cmd(target, code, capture_output=True)
    if result.returncode != 0:
        return None
    value = result.stdout.strip().replace("\\", "/")
    return normalize_remote_path(value) if value else None


def remote_file_exists(target: Target, remote_path: str) -> bool:
    path = remote_cmd_path(remote_path)
    result = ssh_cmd(target, f'if exist "{path}" (exit /b 0) else (exit /b 1)', capture_output=True)
    return result.returncode == 0


def ssh_cmd(target: Target, code: str, *, capture_output: bool) -> subprocess.CompletedProcess[str]:
    remote = f'cmd.exe /v:off /d /s /c "{code}"'
    return subprocess.run(
        [*target.ssh_args, target.ssh_destination, remote],
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
        check=False,
    )


def build_remote_command(native_path: str, native_args: list[str]) -> str:
    command = " ".join([quote_cmd_executable(native_path), *[quote_safe_native_arg(arg) for arg in native_args]])
    return f'cmd.exe /v:off /d /s /c "{command}"'


def quote_cmd_executable(path: str) -> str:
    if '"' in path or "\n" in path or "\r" in path:
        raise WinRemoteError(f"Unsafe native executable path: {path!r}", 2)
    return f'"{remote_cmd_path(path)}"'


def quote_safe_native_arg(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise WinRemoteError("Native argv token unexpectedly contains a newline", 2)
    if re.search(r"[\s\"&|<>^%!()]", value):
        if '"' in value:
            raise WinRemoteError("Native argv token unexpectedly contains a quote", 2)
        return f'"{value}"'
    return value


def scp_to_remote(target: Target, local_path: Path, remote_path: str) -> None:
    remote_path = normalize_remote_path(remote_path)
    result = subprocess.run([*target.scp_args, str(local_path), f"{target.user}@{target.host}:{remote_path}"], stdin=subprocess.DEVNULL, check=False)
    if result.returncode != 0:
        raise WinRemoteError(f"scp upload failed with exit code {result.returncode}", result.returncode)


def scp_from_remote(target: Target, remote_path: str, local_path: Path) -> None:
    remote_path = normalize_remote_path(remote_path)
    result = subprocess.run([*target.scp_args, f"{target.user}@{target.host}:{remote_path}", str(local_path)], stdin=subprocess.DEVNULL, check=False)
    if result.returncode != 0:
        raise WinRemoteError(f"scp download failed with exit code {result.returncode}", result.returncode)


def run_legacy(args: list[str]) -> int:
    env = os.environ.copy()
    env["WIN_REMOTE_LEGACY"] = "1"
    return subprocess.run([str(BIN), *args], env=env, check=False).returncode


def load_target(raw_target: str) -> Target:
    env_file = Path(raw_target)
    if not env_file.is_file():
        env_file = TARGETS_DIR / f"{raw_target}.env"
    if not env_file.is_file():
        raise WinRemoteError(f"Target env not found: {env_file}", 2)
    values = parse_env_file(env_file)
    host = require_env(values, "TARGET_HOST", env_file)
    user = require_env(values, "TARGET_USER", env_file)
    native_exe = normalize_remote_path(values.get("TARGET_NATIVE_EXE", "C:/CodexRemote/tools/WindowsRemoteExecutor.Native.exe"))
    tools_dir = normalize_remote_path(values.get("TARGET_TOOLS_DIR", remote_parent(native_exe)))
    return Target(
        name=values.get("TARGET_NAME", raw_target),
        env_file=env_file,
        host=host,
        user=user,
        port=values.get("TARGET_PORT", "22"),
        key=empty_to_none(values.get("TARGET_KEY")),
        access_token=empty_to_none(values.get("TARGET_ACCESS_TOKEN")),
        ps_exe=values.get("TARGET_PS_EXE", "powershell.exe"),
        stage_root=normalize_remote_path(values.get("TARGET_STAGE_ROOT", "C:/CodexRemote/staging")),
        native_exe=native_exe,
        tools_dir=tools_dir,
        native_launcher=normalize_remote_path(values.get("TARGET_NATIVE_LAUNCHER", f"{tools_dir}/WindowsRemoteExecutor.cmd")),
        native_current_file=normalize_remote_path(values.get("TARGET_NATIVE_CURRENT_FILE", f"{tools_dir}/current-release.txt")),
        native_releases_dir=normalize_remote_path(values.get("TARGET_NATIVE_RELEASES_DIR", f"{tools_dir}/releases")),
        policy_path=normalize_remote_path(values.get("TARGET_POLICY_PATH", f"{tools_dir}/access-policy.json")),
        guard_log_path=normalize_remote_path(values.get("TARGET_GUARD_LOG_PATH", "C:/CodexRemote/logs/sshd-guard.log")),
        repair_log_path=normalize_remote_path(values.get("TARGET_REPAIR_LOG_PATH", "C:/CodexRemote/logs/sshd-repair.log")),
        wsl_distro=empty_to_none(values.get("TARGET_WSL_DISTRO")),
        wsl_user=empty_to_none(values.get("TARGET_WSL_USER")),
        wsl_shell=values.get("TARGET_WSL_SHELL", "/bin/bash"),
        expected_listen_address=empty_to_none(values.get("TARGET_EXPECTED_LISTEN_ADDRESS")),
    )


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        try:
            parts = shlex.split(line, posix=True)
        except ValueError as exc:
            raise WinRemoteError(f"Unable to parse {path}: {exc}", 2) from exc
        if len(parts) != 1 or "=" not in parts[0]:
            continue
        key, value = parts[0].split("=", 1)
        values[key] = value
    return values


def require_env(values: dict[str, str], key: str, path: Path) -> str:
    value = values.get(key)
    if not value:
        raise WinRemoteError(f"{key} is required in {path}", 2)
    return value


def normalize_remote_path(path: str) -> str:
    if DRIVE_RELATIVE.match(path):
        raise WinRemoteError(f"Suspicious Windows drive-relative path: {path}. Quote the argument or use forward slashes, for example D:/path/file.", 2)
    return path.replace("\\", "/")


def remote_cmd_path(path: str) -> str:
    return normalize_remote_path(path).replace("/", "\\")


def remote_parent(path: str) -> str:
    normalized = normalize_remote_path(path).rstrip("/")
    if "/" not in normalized:
        return "."
    return normalized.rsplit("/", 1)[0]


def encode_envelope(request: dict[str, object]) -> str:
    raw = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def base64_url_decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def b64_utf8(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def compact_none(value: object) -> object:
    if isinstance(value, dict):
        return {k: compact_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [compact_none(v) for v in value]
    return value


def pop_value(args: list[str], option: str) -> str:
    if not args:
        raise WinRemoteError(f"{option} requires a value", 2)
    return args.pop(0)


def guard_raw_powershell(command: str, allow: bool, program: str) -> None:
    name = Path(program.replace("\\", "/")).name.lower()
    if not allow and name in RAW_POWERSHELL:
        raise WinRemoteError(f"{command} blocks raw PowerShell transport by default: {program}. Use exec --file/--stdin, or pass --allow-powershell for an intentional escape hatch.", 2)


def normalize_exec_shell(value: str) -> str:
    lowered = value.lower()
    if lowered in {"ps", "powershell"}:
        return "powershell"
    if lowered in {"cmd", "batch"}:
        return "cmd"
    raise WinRemoteError(f"Unsupported exec shell: {value}. Use powershell or cmd.", 2)


def exec_suffix(shell: str) -> str:
    return "ps1" if shell == "powershell" else "cmd"


def stage_id() -> str:
    return secrets.token_hex(12)


def empty_to_none(value: str | None) -> str | None:
    return value if value else None


def print_usage() -> None:
    print(
        """Usage:
  win-remote probe <target> [--out <local-json-file>]
  win-remote run <target> [--cwd <remote-dir>] [--allow-powershell] <program> [args...]
  win-remote capture <target> [--cwd <remote-dir>] [--out <local-json-file>] [--allow-powershell] <program> [args...]
  win-remote exec <target> [--cwd <remote-dir>] [--shell <powershell|cmd>] [--file <local-script> | --stdin | <script-code>]
  win-remote exec-capture <target> [--cwd <remote-dir>] [--shell <powershell|cmd>] [--out <local-json-file>] [--file <local-script> | --stdin | <script-code>]
  win-remote wsl <target> [--distro <name>] [--user <linux-user>] [--cwd <linux-dir>] <program> [args...]
  win-remote wsl-capture <target> [--distro <name>] [--user <linux-user>] [--cwd <linux-dir>] [--out <local-json-file>] <program> [args...]
  win-remote put <target> <local-path> <remote-path>
  win-remote get <target> <remote-path> <local-path>
  win-remote update-tools <target> --native-zip <GitHub-release-asset.zip>

Most common commands use the v2 invoke-b64 envelope when the target native
executor supports it. Older targets automatically fall back to the legacy bash
implementation. Set WIN_REMOTE_LEGACY=1 to force the legacy implementation.
"""
    )


def unreachable(message: str) -> NoReturn:
    raise RuntimeError(message)


if __name__ == "__main__":
    raise SystemExit(main())
