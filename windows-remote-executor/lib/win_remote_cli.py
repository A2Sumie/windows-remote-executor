#!/usr/bin/env python3
"""Python front-end for Windows Remote Executor V3."""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path


TOOL_ROOT = Path(__file__).resolve().parents[1]
TARGETS_DIR = TOOL_ROOT / "targets"
DRIVE_RELATIVE = re.compile(r"^[A-Za-z]:($|[^/\\])")
RAW_POWERSHELL = {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}


class WinRemoteError(Exception):
    def __init__(self, message: str, exit_code: int = 1):
        super().__init__(message)
        self.exit_code = exit_code


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
        if subcommand == "cmd":
            return cmd_cmd(args)
        if subcommand == "exec":
            return cmd_exec(args, capture=False)
        if subcommand == "exec-capture":
            return cmd_exec(args, capture=True)
        if subcommand == "py":
            return cmd_py(args)
        if subcommand == "wsl":
            return cmd_wsl(args, capture=False)
        if subcommand == "wsl-capture":
            return cmd_wsl(args, capture=True)
        if subcommand == "wsl-py":
            return cmd_wsl_py(args, capture=False)
        if subcommand == "wsl-py-capture":
            return cmd_wsl_py(args, capture=True)
        if subcommand == "wsl-sh":
            return cmd_wsl_script(args, capture=False)
        if subcommand == "wsl-sh-capture":
            return cmd_wsl_script(args, capture=True)
        if subcommand == "wsl-container-sh":
            return cmd_wsl_container_script(args, capture=False)
        if subcommand == "wsl-container-sh-capture":
            return cmd_wsl_container_script(args, capture=True)
        if subcommand == "wsl-resident":
            return cmd_wsl_resident(args)
        if subcommand == "put":
            return cmd_put(args)
        if subcommand == "get":
            return cmd_get(args)
        if subcommand == "deploy":
            return cmd_deploy(args)
        if subcommand == "guard":
            return cmd_guard(args)
        if subcommand == "repair":
            return cmd_repair(args)
        if subcommand == "tasks":
            return cmd_tasks(args)
        if subcommand == "policy":
            return cmd_policy(args)
        if subcommand == "find":
            return cmd_find(args)
        if subcommand == "update-tools":
            return cmd_update_tools(args)
        if subcommand == "selftest":
            return cmd_selftest()
        if subcommand == "ps-encode":
            return cmd_ps_encode(args)
        if subcommand == "ps-decode":
            return cmd_ps_decode(args)
        if subcommand == "ps-check":
            return cmd_ps_check(args)
        raise WinRemoteError(f"Unknown subcommand: {subcommand}", 2)
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
    call = v3().host_probe(target)
    text = call.response.get("stdoutText", "")
    write_or_stdout(text, out_path)
    write_stderr(call)
    return call.exit_code


def cmd_run(args: list[str]) -> int:
    target, cwd, allow_powershell, program, program_args = parse_process_command(args, "run")
    call = v3().process_run(target, program, program_args, cwd=cwd, allow_powershell=allow_powershell)
    sys.stdout.write(str(call.response.get("stdoutText", "")))
    sys.stderr.write(str(call.response.get("stderrText", "")) or call.ssh_stderr)
    return call.exit_code


def cmd_capture(args: list[str]) -> int:
    target, cwd, out_path, allow_powershell, program, program_args = parse_capture_command(args)
    call = v3().process_capture(target, program, program_args, cwd=cwd, allow_powershell=allow_powershell)
    write_rpc_json(call, out_path)
    return call.exit_code


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
    call = v3().process_spawn(target, rest[0], rest[1:], cwd=cwd, stdout=stdout, stderr=stderr, allow_powershell=allow_powershell)
    write_rpc_json(call, out_path)
    return call.exit_code


def cmd_cmd(args: list[str]) -> int:
    if len(args) < 2:
        raise WinRemoteError("cmd requires <target> <cmd-code>", 2)
    target = load_target(args[0])
    call = v3().script_run(target, " ".join(args[1:]), kind="cmd")
    sys.stdout.write(str(call.response.get("stdoutText", "")))
    sys.stderr.write(str(call.response.get("stderrText", "")) or call.ssh_stderr)
    return call.exit_code


def cmd_exec(args: list[str], *, capture: bool) -> int:
    if not args:
        raise WinRemoteError("exec requires <target>", 2)
    target = load_target(args[0])
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
    script = read_script_argument("exec", rest, suffix=exec_suffix(shell))
    action = v3().script_capture if capture else v3().script_run
    call = action(target, script, kind=shell, cwd=cwd, exe=target.ps_exe if shell == "powershell" else None)
    if capture:
        write_rpc_json(call, out_path)
    else:
        sys.stdout.write(str(call.response.get("stdoutText", "")))
        sys.stderr.write(str(call.response.get("stderrText", "")) or call.ssh_stderr)
    return call.exit_code


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
    call = v3().python_run(target, script_path, script_args, cwd=cwd, python=python, conda_env=conda_env, conda_prefix=conda_prefix)
    sys.stdout.write(str(call.response.get("stdoutText", "")))
    sys.stderr.write(str(call.response.get("stderrText", "")) or call.ssh_stderr)
    return call.exit_code


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
            _ = pop_value(rest, option)
        else:
            raise WinRemoteError(f"Unknown {'wsl-capture' if capture else 'wsl'} option: {option}", 2)
    if not rest:
        raise WinRemoteError("wsl requires <program> [args...]", 2)
    action = v3().wsl_capture if capture else v3().wsl_run
    call = action(target, rest[0], rest[1:], cwd=cwd, distribution=distro, user=user)
    if capture:
        write_rpc_json(call, out_path)
    else:
        sys.stdout.write(str(call.response.get("stdoutText", "")))
        sys.stderr.write(str(call.response.get("stderrText", "")) or call.ssh_stderr)
    return call.exit_code


def cmd_wsl_py(args: list[str], *, capture: bool) -> int:
    if not args:
        raise WinRemoteError("wsl-py requires <target>", 2)
    target = load_target(args[0])
    rest = args[1:]
    distro = target.wsl_distro
    user = target.wsl_user
    cwd = None
    python = "python3"
    module = None
    out_path = None
    while rest and rest[0].startswith("--"):
        option = rest.pop(0)
        if option == "--distro":
            distro = pop_value(rest, option)
        elif option == "--user":
            user = pop_value(rest, option)
        elif option == "--cwd":
            cwd = pop_value(rest, option)
        elif option == "--python":
            python = pop_value(rest, option)
        elif option == "--module":
            module = pop_value(rest, option)
        elif capture and option == "--out":
            out_path = pop_value(rest, option)
        elif option == "--heartbeat-seconds":
            _ = pop_value(rest, option)
        else:
            raise WinRemoteError(f"Unknown {'wsl-py-capture' if capture else 'wsl-py'} option: {option}", 2)
    script_args: list[str] = []
    if "--" in rest:
        idx = rest.index("--")
        script_part = rest[:idx]
        script_args = rest[idx + 1:]
    else:
        script_part = rest
    if module:
        py_args = ["-m", module, *script_args]
    else:
        if len(script_part) != 1:
            raise WinRemoteError("wsl-py requires --module <module> or <script-path>", 2)
        py_args = [script_part[0], *script_args]
    action = v3().wsl_capture if capture else v3().wsl_run
    call = action(target, python, py_args, cwd=cwd, distribution=distro, user=user)
    if capture:
        write_rpc_json(call, out_path)
    else:
        sys.stdout.write(str(call.response.get("stdoutText", "")))
        sys.stderr.write(str(call.response.get("stderrText", "")) or call.ssh_stderr)
    return call.exit_code


def cmd_wsl_script(args: list[str], *, capture: bool) -> int:
    target, script, script_args, cwd, distro, user, shell, out_path = parse_wsl_script(args, capture=capture)
    action = v3().wsl_script_capture if capture else v3().wsl_script
    call = action(target, script, script_args, cwd=cwd, distribution=distro, user=user, shell=shell)
    if capture:
        write_rpc_json(call, out_path)
    else:
        sys.stdout.write(str(call.response.get("stdoutText", "")))
        sys.stderr.write(str(call.response.get("stderrText", "")) or call.ssh_stderr)
    return call.exit_code


def cmd_wsl_container_script(args: list[str], *, capture: bool) -> int:
    target, script, script_args, cwd, distro, user, shell, out_path, container = parse_wsl_container_script(args, capture=capture)
    wrapper = build_container_wrapper(script, container)
    action = v3().wsl_script_capture if capture else v3().wsl_script
    call = action(target, wrapper, script_args, cwd=cwd, distribution=distro, user=user, shell=shell)
    if capture:
        write_rpc_json(call, out_path)
    else:
        sys.stdout.write(str(call.response.get("stdoutText", "")))
        sys.stderr.write(str(call.response.get("stderrText", "")) or call.ssh_stderr)
    return call.exit_code


def cmd_wsl_resident(args: list[str]) -> int:
    target, script, script_args, cwd, distro, user, shell, out_path = parse_wsl_script(args, capture=True, command_name="wsl-resident")
    rest = parse_wsl_script.extra_options
    pid_file = log_file = health_url = None
    port = ready_timeout = settle_delay = poll_interval = diag_lines = None
    launch_path = None
    while rest:
        option = rest.pop(0)
        if option == "--pid-file":
            pid_file = pop_value(rest, option)
        elif option == "--log-file":
            log_file = pop_value(rest, option)
        elif option == "--port":
            port = int(pop_value(rest, option))
        elif option == "--health-url":
            health_url = pop_value(rest, option)
        elif option == "--ready-timeout":
            ready_timeout = int(pop_value(rest, option))
        elif option == "--settle-delay":
            settle_delay = int(pop_value(rest, option))
        elif option == "--poll-interval-ms":
            poll_interval = int(pop_value(rest, option))
        elif option == "--diag-lines":
            diag_lines = int(pop_value(rest, option))
        elif option == "--launch-path":
            launch_path = pop_value(rest, option)
        else:
            raise WinRemoteError(f"Unknown wsl-resident option: {option}", 2)
    call = v3().wsl_resident(
        target,
        script,
        script_args,
        cwd=cwd,
        distribution=distro,
        user=user,
        shell=shell,
        launch_path=launch_path,
        pid_file=pid_file,
        log_file=log_file,
        port=port,
        health_url=health_url,
        ready_timeout_seconds=ready_timeout,
        settle_delay_seconds=settle_delay,
        poll_interval_ms=poll_interval,
        diagnostic_lines=diag_lines,
    )
    write_rpc_json(call, out_path)
    return call.exit_code


def cmd_put(args: list[str]) -> int:
    if len(args) != 3:
        raise WinRemoteError("put requires <target> <local-path> <remote-path>", 2)
    target = load_target(args[0])
    local_path = Path(args[1])
    remote_path = normalize_remote_path(args[2])
    if not local_path.is_file():
        raise WinRemoteError(f"Local file not found: {local_path}", 2)
    v3().file_mkdir(target, remote_parent(remote_path))
    scp_to_remote(target, local_path, remote_path)
    print("OK")
    return 0


def cmd_get(args: list[str]) -> int:
    if len(args) != 3:
        raise WinRemoteError("get requires <target> <remote-path> <local-path>", 2)
    target = load_target(args[0])
    remote_path = normalize_remote_path(args[1])
    local_path = Path(args[2])
    local_path.parent.mkdir(parents=True, exist_ok=True)
    scp_from_remote(target, remote_path, local_path)
    print("OK")
    return 0


def cmd_deploy(args: list[str]) -> int:
    if len(args) < 3:
        raise WinRemoteError("deploy requires <target> <local-dir> <remote-dir>", 2)
    target = load_target(args[0])
    local_dir = Path(args[1])
    remote_dir = normalize_remote_path(args[2])
    rest = args[3:]
    post_script = None
    while rest:
        option = rest.pop(0)
        if option == "--post":
            post_script = pop_value(rest, option)
        elif option == "--post-file":
            post_script = Path(pop_value(rest, option)).read_text(encoding="utf-8")
        elif option == "--post-stdin":
            post_script = sys.stdin.read()
        else:
            raise WinRemoteError(f"Unknown deploy option: {option}", 2)
    if not local_dir.is_dir():
        raise WinRemoteError(f"Local directory not found: {local_dir}", 2)
    v3().file_mkdir(target, remote_dir)
    scp_dir_to_remote(target, local_dir, remote_dir)
    if post_script:
        call = v3().script_run(target, post_script)
        sys.stdout.write(str(call.response.get("stdoutText", "")))
        sys.stderr.write(str(call.response.get("stderrText", "")) or call.ssh_stderr)
        return call.exit_code
    print("OK")
    return 0


def cmd_guard(args: list[str]) -> int:
    if not args:
        raise WinRemoteError("guard requires <target>", 2)
    target = load_target(args[0])
    rest = args[1:]
    out_path = None
    no_disable = False
    expected = None
    while rest:
        option = rest.pop(0)
        if option == "--out":
            out_path = pop_value(rest, option)
        elif option == "--no-disable":
            no_disable = True
        elif option == "--expected-listen-address":
            expected = pop_value(rest, option)
        else:
            raise WinRemoteError(f"Unknown guard option: {option}", 2)
    call = v3().host_guard(target, expected_listen_address=expected, no_disable=no_disable)
    write_rpc_json(call, out_path)
    return call.exit_code


def cmd_repair(args: list[str]) -> int:
    if not args:
        raise WinRemoteError("repair requires <target>", 2)
    target = load_target(args[0])
    rest = args[1:]
    out_path = None
    expected = None
    force_rewrite = False
    while rest:
        option = rest.pop(0)
        if option == "--out":
            out_path = pop_value(rest, option)
        elif option == "--expected-listen-address":
            expected = pop_value(rest, option)
        elif option == "--force-rewrite":
            force_rewrite = True
        else:
            raise WinRemoteError(f"Unknown repair option: {option}", 2)
    call = v3().host_repair(target, expected_listen_address=expected, force_rewrite=force_rewrite)
    write_rpc_json(call, out_path)
    return call.exit_code


def cmd_tasks(args: list[str]) -> int:
    if not args:
        raise WinRemoteError("tasks requires <target>", 2)
    target = load_target(args[0])
    rest = args[1:]
    out_path = None
    prefix = None
    task_names: list[str] = []
    while rest:
        option = rest.pop(0)
        if option == "--task-name":
            task_names.append(pop_value(rest, option))
        elif option == "--prefix":
            prefix = pop_value(rest, option)
        elif option == "--out":
            out_path = pop_value(rest, option)
        else:
            raise WinRemoteError(f"Unknown tasks option: {option}", 2)
    call = v3().host_tasks(target, task_names=task_names, prefix=prefix)
    text = call.response.get("stdoutText", "")
    write_or_stdout(str(text), Path(out_path) if out_path else None)
    return call.exit_code


def cmd_policy(args: list[str]) -> int:
    if not args:
        raise WinRemoteError("policy requires <target>", 2)
    target = load_target(args[0])
    rest = args[1:]
    mode = "private-only"
    command_mode = "standard"
    expected = target.expected_listen_address
    label = None
    token = target.access_token
    rotate_token = False
    while rest:
        option = rest.pop(0)
        if option == "--mode":
            mode = pop_value(rest, option)
        elif option == "--command-mode":
            command_mode = pop_value(rest, option)
        elif option == "--expected-listen-address":
            expected = pop_value(rest, option)
        elif option == "--label":
            label = pop_value(rest, option)
        elif option == "--token":
            token = pop_value(rest, option)
        elif option == "--rotate-token":
            token = secrets.token_urlsafe(32)
            rotate_token = True
        elif option in {"--skip-guard-install", "--no-run-guard"}:
            pass
        else:
            raise WinRemoteError(f"Unknown policy option: {option}", 2)
    call = v3().host_policy(target, exposure_mode=mode, command_mode=command_mode, expected_listen_address=expected, label=label, token=token)
    print(json.dumps(call.response, ensure_ascii=False, indent=2))
    if rotate_token and token:
        print(f"new-access-token: {token}", file=sys.stderr)
    return call.exit_code


def cmd_find(args: list[str]) -> int:
    if len(args) < 2:
        raise WinRemoteError("find requires <target> <query>", 2)
    target = load_target(args[0])
    query = args[1]
    max_results = None
    rest = args[2:]
    while rest:
        option = rest.pop(0)
        if option == "--max":
            max_results = int(pop_value(rest, option))
        else:
            raise WinRemoteError(f"Unknown find option: {option}", 2)
    call = v3().everything_search(target, query, max_results=max_results)
    sys.stdout.write(str(call.response.get("stdoutText", "")))
    sys.stderr.write(str(call.response.get("stderrText", "")) or call.ssh_stderr)
    return call.exit_code


def cmd_update_tools(args: list[str]) -> int:
    if not args:
        raise WinRemoteError("update-tools requires <target>", 2)
    target = load_target(args[0])
    rest = args[1:]
    native_zip = None
    while rest:
        option = rest.pop(0)
        if option == "--native-zip":
            native_zip = Path(pop_value(rest, option))
        elif option in {"--native-dir", "--native-exe"}:
            raise WinRemoteError("V3 update-tools accepts only --native-zip <release-asset.zip>", 2)
        elif option in {"--everything-dll", "--es-exe", "--policy-file"}:
            _ = pop_value(rest, option)
        elif option == "--install-guard":
            pass
        else:
            raise WinRemoteError(f"Unknown update-tools option: {option}", 2)
    if native_zip is None:
        raise WinRemoteError("update-tools requires --native-zip <release-asset.zip>", 2)
    validate_release_native_zip(native_zip)
    release_dir = f"{target.native_releases_dir}/v3-{stage_id()}"
    with tempfile.TemporaryDirectory(prefix="win-remote-update-") as tmp:
        extract_dir = Path(tmp) / "native"
        extract_dir.mkdir()
        with zipfile.ZipFile(native_zip) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = Path(info.filename).name
                if not name:
                    continue
                with zf.open(info) as src, (extract_dir / name).open("wb") as dst:
                    shutil.copyfileobj(src, dst)
        ensure_remote_dir_for_update(target, release_dir)
        for item in extract_dir.iterdir():
            if item.is_file():
                scp_to_remote(target, item, f"{release_dir}/{item.name}")
        v3().file_write_text(target, target.native_current_file, release_dir)
    print("OK")
    return 0


def ensure_remote_dir_for_update(target: Target, remote_dir: str) -> None:
    call = v3().file_mkdir(target, remote_dir)
    if call.ok:
        return
    if call.response.get("errorClass") != "unsupported":
        raise WinRemoteError(str(call.response.get("stderrText") or call.ssh_stderr or "file.mkdir failed"), call.exit_code)
    script = "$ErrorActionPreference = 'Stop'\n[System.IO.Directory]::CreateDirectory({0}) | Out-Null\n".format(ps_single_quote(remote_dir))
    fallback = v3().script_capture(target, script)
    if not fallback.ok:
        raise WinRemoteError(str(fallback.response.get("stderrText") or fallback.ssh_stderr or "script.capture mkdir fallback failed"), fallback.exit_code)


def ps_single_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def cmd_selftest() -> int:
    request = v3().build_rpc_request(
        "process.capture",
        {"file": "C:/Tools/echo.exe", "cwd": "D:/Work Dir", "args": ["space arg", "quote \" arg", "tick ` dollar $"]},
        request_id="selftest",
    )
    line = v3().request_json_line(request)
    decoded = json.loads(line)
    ok = decoded == compact_none(request) and "process.capture" in line
    print(json.dumps({"ok": ok, "requestLength": len(line), "decoded": decoded}, ensure_ascii=False))
    return 0 if ok else 1


def cmd_ps_encode(args: list[str]) -> int:
    script = read_script_argument("ps-encode", args, suffix="ps1")
    print(base64.b64encode(script.encode("utf-16le")).decode("ascii"))
    return 0


def cmd_ps_decode(args: list[str]) -> int:
    if len(args) != 1:
        raise WinRemoteError("ps-decode requires <utf16le-base64>", 2)
    sys.stdout.write(base64.b64decode(args[0]).decode("utf-16le"))
    return 0


def cmd_ps_check(args: list[str]) -> int:
    script = read_script_argument("ps-check", args, suffix="ps1")
    print(json.dumps({"ok": True, "chars": len(script), "lines": len(script.splitlines())}, ensure_ascii=False))
    return 0


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
        rest.pop(0)
        if not rest:
            raise WinRemoteError("capture --cmd requires <cmd-code>", 2)
        return target, cwd, out_path, True, "cmd.exe", ["/d", "/s", "/c", " ".join(rest)]
    while rest and rest[0].startswith("--"):
        option = rest.pop(0)
        if option == "--cwd":
            cwd = pop_value(rest, option)
        elif option == "--out":
            out_path = pop_value(rest, option)
        elif option == "--allow-powershell":
            allow_powershell = True
        elif option == "--cmd":
            if not rest:
                raise WinRemoteError("capture --cmd requires <cmd-code>", 2)
            return target, cwd, out_path, True, "cmd.exe", ["/d", "/s", "/c", " ".join(rest)]
        else:
            raise WinRemoteError(f"Unknown capture option: {option}", 2)
    if not rest:
        raise WinRemoteError("capture requires <program> [args...]", 2)
    return target, cwd, out_path, allow_powershell, rest[0], rest[1:]


def parse_wsl_script(args: list[str], *, capture: bool, command_name: str | None = None):
    command_name = command_name or ("wsl-sh-capture" if capture else "wsl-sh")
    if not args:
        raise WinRemoteError(f"{command_name} requires <target>", 2)
    target = load_target(args[0])
    rest = args[1:]
    distro = target.wsl_distro
    user = target.wsl_user
    cwd = None
    shell = target.wsl_shell
    out_path = None
    passthrough_options = {
        "--pid-file", "--log-file", "--port", "--health-url", "--ready-timeout", "--settle-delay", "--poll-interval-ms", "--diag-lines", "--launch-path"
    }
    extra: list[str] = []
    while rest and rest[0].startswith("--") and rest[0] not in {"--file", "--stdin"}:
        option = rest.pop(0)
        if option == "--distro":
            distro = pop_value(rest, option)
        elif option == "--user":
            user = pop_value(rest, option)
        elif option == "--cwd":
            cwd = pop_value(rest, option)
        elif option == "--shell":
            shell = pop_value(rest, option)
        elif capture and option == "--out":
            out_path = pop_value(rest, option)
        elif option == "--heartbeat-seconds":
            _ = pop_value(rest, option)
        elif option in passthrough_options:
            extra.extend([option, pop_value(rest, option)])
        else:
            raise WinRemoteError(f"Unknown {command_name} option: {option}", 2)
    script_args: list[str] = []
    if "--" in rest:
        idx = rest.index("--")
        script_part = rest[:idx]
        script_args = rest[idx + 1:]
    else:
        script_part = rest
    script = read_script_argument(command_name, script_part, suffix="sh")
    parse_wsl_script.extra_options = extra  # type: ignore[attr-defined]
    return target, script, script_args, cwd, distro, user, shell, out_path


parse_wsl_script.extra_options = []  # type: ignore[attr-defined]


def parse_wsl_container_script(args: list[str], *, capture: bool):
    if not args:
        raise WinRemoteError("wsl-container-sh requires <target>", 2)
    container = {
        "name": None,
        "runtime": "docker",
        "cwd": None,
        "user": None,
        "shell": "/bin/sh",
    }
    container_options = {
        "--container": "name",
        "--container-runtime": "runtime",
        "--container-cwd": "cwd",
        "--container-user": "user",
        "--container-shell": "shell",
    }
    wsl_options_with_value = {"--distro", "--user", "--cwd", "--shell", "--out", "--heartbeat-seconds"}
    filtered: list[str] = [args[0]]
    rest = args[1:]
    while rest:
        option = rest.pop(0)
        if option in container_options:
            container[container_options[option]] = pop_value(rest, option)
        elif option in {"--file", "--stdin", "--"}:
            filtered.append(option)
            filtered.extend(rest)
            break
        elif option in wsl_options_with_value:
            filtered.append(option)
            filtered.append(pop_value(rest, option))
        else:
            filtered.append(option)
            filtered.extend(rest)
            break
    if not container["name"]:
        raise WinRemoteError("wsl-container-sh requires --container <name>", 2)
    return (*parse_wsl_script(filtered, capture=capture, command_name="wsl-container-sh"), container)


def build_container_wrapper(script: str, container: dict[str, str | None]) -> str:
    delimiter = "WRE_SCRIPT_" + secrets.token_hex(12)
    runtime = shlex.quote(container["runtime"] or "docker")
    name = shlex.quote(container["name"] or "")
    shell = shlex.quote(container["shell"] or "/bin/sh")
    opts: list[str] = []
    if container.get("cwd"):
        opts.extend(["--workdir", shlex.quote(container["cwd"] or "")])
    if container.get("user"):
        opts.extend(["--user", shlex.quote(container["user"] or "")])
    return f"""set -euo pipefail
cat <<'{delimiter}' | {runtime} exec -i {' '.join(opts)} {name} {shell} -s -- "$@"
{script}
{delimiter}
"""


def read_script_argument(command_name: str, args: list[str], *, suffix: str) -> str:
    rest = list(args)
    if rest and rest[0] == "--file":
        rest.pop(0)
        path = Path(pop_value(rest, "--file"))
        if rest:
            raise WinRemoteError(f"{command_name} --file does not accept extra arguments before --", 2)
        if not path.is_file():
            raise WinRemoteError(f"Local file not found: {path}", 2)
        return path.read_text(encoding="utf-8")
    if rest and rest[0] == "--stdin":
        rest.pop(0)
        if rest:
            raise WinRemoteError(f"{command_name} --stdin does not accept extra arguments before --", 2)
        return sys.stdin.read()
    if rest:
        return " ".join(rest)
    raise WinRemoteError(f"{command_name} requires inline script, --file <local-script>, or --stdin", 2)


def validate_release_native_zip(path: Path) -> str:
    if not path.is_file():
        raise WinRemoteError(f"Native release zip not found: {path}", 2)
    name = path.name
    scd = re.match(r"windows-remote-executor-native-v[^/]+-scd-win-x64\.zip$", name)
    fdd = re.match(r"windows-remote-executor-native-v[^/]+-fdd-win-x64\.zip$", name)
    if not scd and not fdd:
        raise WinRemoteError(
            "V3 update-tools requires a GitHub release asset named "
            "windows-remote-executor-native-v<version>-scd-win-x64.zip or "
            "windows-remote-executor-native-v<version>-fdd-win-x64.zip",
            2,
        )
    try:
        with zipfile.ZipFile(path) as zf:
            names = {Path(info.filename).name for info in zf.infolist() if not info.is_dir()}
    except zipfile.BadZipFile as exc:
        raise WinRemoteError(f"Invalid native release zip: {path}", 2) from exc
    required = {"WindowsRemoteExecutor.Native.exe"} if scd else {
        "WindowsRemoteExecutor.Native.exe",
        "WindowsRemoteExecutor.Native.dll",
        "WindowsRemoteExecutor.Native.runtimeconfig.json",
        "WindowsRemoteExecutor.Native.deps.json",
    }
    missing = sorted(required - names)
    if missing:
        raise WinRemoteError(f"Native release zip is missing required files: {', '.join(missing)}", 2)
    return "scd" if scd else "fdd"


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


def scp_dir_to_remote(target: Target, local_dir: Path, remote_dir: str) -> None:
    result = subprocess.run([*target.scp_args, "-r", str(local_dir) + "/.", f"{target.user}@{target.host}:{normalize_remote_path(remote_dir)}"], stdin=subprocess.DEVNULL, check=False)
    if result.returncode != 0:
        raise WinRemoteError(f"scp directory upload failed with exit code {result.returncode}", result.returncode)


def scp_from_remote(target: Target, remote_path: str, local_path: Path) -> None:
    remote_path = normalize_remote_path(remote_path)
    result = subprocess.run([*target.scp_args, f"{target.user}@{target.host}:{remote_path}", str(local_path)], stdin=subprocess.DEVNULL, check=False)
    if result.returncode != 0:
        raise WinRemoteError(f"scp download failed with exit code {result.returncode}", result.returncode)


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
    env_allow = os.environ.get("WIN_REMOTE_ALLOW_RAW_POWERSHELL", "0") == "1"
    if not allow and not env_allow and name in RAW_POWERSHELL:
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


def write_rpc_json(call, out_path: str | Path | None = None) -> None:
    text = json.dumps(call.response, ensure_ascii=False, indent=2) + "\n"
    if out_path:
        Path(out_path).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    if call.ssh_stderr and not call.response.get("stderrText"):
        sys.stderr.write(call.ssh_stderr)


def write_or_stdout(text: str, out_path: Path | None) -> None:
    if out_path:
        out_path.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


def write_stderr(call) -> None:
    stderr = str(call.response.get("stderrText", "")) or call.ssh_stderr
    if stderr:
        sys.stderr.write(stderr)


def v3():
    import wre_v3_client
    return wre_v3_client


def print_usage() -> None:
    print(
        """Usage:
  win-remote probe <target> [--out <local-json-file>]
  win-remote run <target> [--cwd <remote-dir>] [--allow-powershell] <program> [args...]
  win-remote capture <target> [--cwd <remote-dir>] [--out <local-json-file>] [--allow-powershell] [--cmd <cmd-code> | <program> [args...]]
  win-remote spawn <target> [--cwd <remote-dir>] [--stdout <remote-log>] [--stderr <remote-log>] [--out <local-json-file>] [--allow-powershell] <program> [args...]
  win-remote exec <target> [--cwd <remote-dir>] [--shell <powershell|cmd>] [--file <local-script> | --stdin | <script-code>]
  win-remote exec-capture <target> [--cwd <remote-dir>] [--shell <powershell|cmd>] [--out <local-json-file>] [--file <local-script> | --stdin | <script-code>]
  win-remote py <target> <remote-script-path> [--cwd <remote-dir>] [--python <remote-python>] [--conda-env <name> | --conda-prefix <prefix>] [-- <script-args...>]
  win-remote wsl <target> [--distro <name>] [--user <linux-user>] [--cwd <linux-dir>] <program> [args...]
  win-remote wsl-capture <target> [--distro <name>] [--user <linux-user>] [--cwd <linux-dir>] [--out <local-json-file>] <program> [args...]
  win-remote wsl-py <target> [--distro <name>] [--user <linux-user>] [--cwd <linux-dir>] [--python <linux-python>] [--module <module> | <script-path>] [-- <script-args...>]
  win-remote wsl-py-capture <target> [--distro <name>] [--user <linux-user>] [--cwd <linux-dir>] [--python <linux-python>] [--out <local-json-file>] [--module <module> | <script-path>] [-- <script-args...>]
  win-remote wsl-sh <target> [--distro <name>] [--user <linux-user>] [--cwd <linux-dir>] [--shell <linux-shell>] [--file <local-sh> | --stdin | <shell-code>] [-- <script-args...>]
  win-remote wsl-sh-capture <target> [--distro <name>] [--user <linux-user>] [--cwd <linux-dir>] [--shell <linux-shell>] [--out <local-json-file>] [--file <local-sh> | --stdin | <shell-code>] [-- <script-args...>]
  win-remote wsl-resident <target> [--distro <name>] [--user <linux-user>] [--cwd <linux-dir>] [--shell <linux-shell>] [--pid-file <linux-path>] [--log-file <linux-path>] [--port <n>] [--health-url <url>] [--ready-timeout <seconds>] [--settle-delay <seconds>] [--poll-interval-ms <n>] [--diag-lines <n>] [--out <local-json-file>] [--file <local-sh> | --stdin | <shell-code>] [-- <script-args...>]
  win-remote put <target> <local-path> <remote-path>
  win-remote get <target> <remote-path> <local-path>
  win-remote deploy <target> <local-dir> <remote-dir> [--post <powershell-code> | --post-file <local-ps1> | --post-stdin]
  win-remote guard <target> [--out <local-json-file>] [--no-disable] [--expected-listen-address <ip>]
  win-remote repair <target> [--out <local-json-file>] [--expected-listen-address <ip>] [--force-rewrite]
  win-remote tasks <target> [--task-name <name>]... [--prefix <text>] [--out <local-json-file>]
  win-remote policy <target> [--mode <private-only|public-with-token>] [--command-mode <standard|argv-only>] [--expected-listen-address <ip>] [--label <text>] [--token <plain-token> | --rotate-token]
  win-remote find <target> <query> [--max <count>]
  win-remote update-tools <target> --native-zip <GitHub-release-asset.zip>

All remote execution uses V3 rpc-stdio.
"""
    )


if __name__ == "__main__":
    raise SystemExit(main())
