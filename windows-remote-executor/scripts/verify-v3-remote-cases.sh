#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TARGET="${1:-}"

if [[ -z "${TARGET}" ]]; then
  cat >&2 <<'EOF'
Usage:
  verify-v3-remote-cases.sh <target-name-or-env-file>

Runs the V3-only rpc-stdio remote matrix against a target that already has a
native executor with rpc-stdio support. Safe default cases avoid mutating host
policy or repairing sshd; use win-remote policy/repair explicitly when those
host-state changes are intended.
EOF
  exit 2
fi

python3 - "${TOOL_ROOT}" "${TARGET}" <<'PY'
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

TOOL_ROOT = Path(sys.argv[1])
TARGET_NAME = sys.argv[2]
sys.path.insert(0, str(TOOL_ROOT / "lib"))

import win_remote_cli as cli  # noqa: E402
import wre_v3_client as v3  # noqa: E402


REQUIRED_ACTIONS = {
    "host.capabilities",
    "host.probe",
    "host.guard",
    "host.tasks",
    "host.policy",
    "process.run",
    "process.capture",
    "process.spawn",
    "script.run",
    "script.capture",
    "python.run",
    "wsl.run",
    "wsl.capture",
    "wsl.script",
    "wsl.script.capture",
    "wsl.resident",
    "file.writeText",
    "file.readText",
    "file.mkdir",
    "file.deleteTree",
    "file.copy",
    "everything.search",
}


def status(message: str) -> None:
    print(f"[verify-v3-remote-cases] {message}", flush=True)


def require_ok(label: str, call: v3.RpcCall) -> dict[str, object]:
    response = call.response
    if not call.ok:
        raise SystemExit(
            f"{label} failed: ssh={call.ssh_returncode} exit={response.get('exitCode')} "
            f"class={response.get('errorClass')} stderr={response.get('stderrText') or call.ssh_stderr}"
        )
    return response


def ps_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def cleanup_root(target: cli.Target, root: str) -> None:
    script = "$ErrorActionPreference = 'SilentlyContinue'\nRemove-Item -LiteralPath {0} -Recurse -Force\n".format(ps_literal(root))
    try:
        v3.script_capture(target, script)
    except Exception as exc:  # noqa: BLE001
        status(f"cleanup warning: {exc}")


def stdout_text(response: dict[str, object]) -> str:
    return str(response.get("stdoutText") or "")


target = cli.load_target(TARGET_NAME)
remote_root = f"C:/CodexRemote/inbox/wre-v3-case-{time.strftime('%Y%m%d-%H%M%S')}-{id(target) % 100000}"

try:
    status("host.capabilities")
    capabilities = require_ok("host.capabilities", v3.host_capabilities(target))
    data = capabilities.get("data") or {}
    actions = set(data.get("actions") if isinstance(data, dict) else [])
    missing_actions = sorted(REQUIRED_ACTIONS - actions)
    if data.get("version") != 3 or missing_actions:
        raise SystemExit(f"unexpected capabilities payload: missing={missing_actions} data={json.dumps(data, ensure_ascii=False)}")

    status("host.probe")
    probe = require_ok("host.probe", v3.host_probe(target))
    if not isinstance(probe.get("data"), dict):
        raise SystemExit("host.probe did not return a data object")

    status("file.mkdir")
    require_ok("file.mkdir", v3.file_mkdir(target, f"{remote_root}/copy dir"))

    status("file.writeText / file.readText with spaces and unicode")
    remote_text_path = f"{remote_root}/payload with spaces.txt"
    text = 'space path payload\nquote="ok"\nunicode=値\n'
    write_response = require_ok("file.writeText", v3.file_write_text(target, remote_text_path, text))
    read_response = require_ok("file.readText", v3.file_read_text(target, remote_text_path, max_bytes=4096))
    if read_response.get("stdoutText") != text:
        raise SystemExit(f"file.readText mismatch: {read_response.get('stdoutText')!r}")
    expected_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    proof = write_response.get("data") or {}
    if not isinstance(proof, dict) or proof.get("sha256") != expected_hash:
        raise SystemExit(f"file proof mismatch: {json.dumps(proof, ensure_ascii=False)}")

    status("file.copy")
    copied_text_path = f"{remote_root}/copy dir/copied payload.txt"
    copy_response = require_ok("file.copy", v3.file_copy(target, remote_text_path, copied_text_path))
    copy_data = copy_response.get("data") or {}
    copy_proof = copy_data.get("proof") if isinstance(copy_data, dict) else None
    if not isinstance(copy_proof, dict) or copy_proof.get("sha256") != expected_hash:
        raise SystemExit(f"file.copy proof mismatch: {json.dumps(copy_data, ensure_ascii=False)}")

    status("process.capture whoami.exe")
    whoami = require_ok("process.capture whoami.exe", v3.process_capture(target, "whoami.exe"))
    if int(whoami.get("stdoutBytes") or 0) < 1:
        raise SystemExit("whoami.exe returned empty stdout")

    status("process.run cmd.exe")
    cmd_run = require_ok("process.run cmd.exe", v3.process_run(target, "cmd.exe", ["/d", "/s", "/c", "echo process-run-ok"], cwd=remote_root))
    if "process-run-ok" not in stdout_text(cmd_run):
        raise SystemExit(f"process.run stdout mismatch: {cmd_run.get('stdoutText')!r}")

    status("script.capture powershell")
    ps_script = "$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)\nWrite-Output 'v3-powershell-ok 値'\n"
    ps_response = require_ok("script.capture powershell", v3.script_capture(target, ps_script, cwd=remote_root))
    if "v3-powershell-ok" not in stdout_text(ps_response):
        raise SystemExit(f"powershell stdout mismatch: {ps_response.get('stdoutText')!r}")

    status("script.run powershell")
    ps_run = require_ok("script.run powershell", v3.script_run(target, "Write-Output 'script-run-ok'\n", cwd=remote_root))
    if "script-run-ok" not in stdout_text(ps_run):
        raise SystemExit(f"script.run stdout mismatch: {ps_run.get('stdoutText')!r}")

    status("script.capture cmd")
    cmd_response = require_ok("script.capture cmd", v3.script_capture(target, "@echo off\r\necho v3-cmd-ok\r\n", kind="cmd", cwd=remote_root))
    if "v3-cmd-ok" not in stdout_text(cmd_response):
        raise SystemExit(f"cmd stdout mismatch: {cmd_response.get('stdoutText')!r}")

    status("process.spawn with stdout readback")
    spawn_stdout = f"{remote_root}/spawn stdout.txt"
    spawn_stderr = f"{remote_root}/spawn stderr.txt"
    spawn_response = require_ok(
        "process.spawn",
        v3.process_spawn(
            target,
            "cmd.exe",
            ["/d", "/s", "/c", "echo spawn-ok"],
            cwd=remote_root,
            stdout=spawn_stdout,
            stderr=spawn_stderr,
        ),
    )
    time.sleep(2)
    spawn_read = require_ok("file.readText spawn stdout", v3.file_read_text(target, spawn_stdout, max_bytes=4096))
    if "spawn-ok" not in stdout_text(spawn_read):
        raise SystemExit(f"spawn stdout mismatch: spawn={json.dumps(spawn_response, ensure_ascii=False)} read={spawn_read.get('stdoutText')!r}")

    status("host.guard no-disable")
    guard = require_ok("host.guard", v3.host_guard(target, expected_listen_address=target.expected_listen_address, no_disable=True))
    if not isinstance(guard.get("data"), dict):
        raise SystemExit("host.guard did not return a data object")

    status("host.tasks")
    tasks = require_ok("host.tasks", v3.host_tasks(target, prefix="CodexRemote"))
    if not isinstance(tasks.get("data"), (dict, list)):
        raise SystemExit("host.tasks did not return structured data")

    status("drive-relative path is rejected locally")
    try:
        v3.file_read_text(target, "D:CodexRemoteinboxbad.txt")
    except cli.WinRemoteError as exc:
        if "drive-relative" not in str(exc):
            raise
    else:
        raise SystemExit("drive-relative path unexpectedly reached transport")

    status("optional python.run case")
    py_probe = v3.process_capture(target, "py.exe", ["-3", "-c", "print('py-ok')"])
    if py_probe.ok:
        remote_py_path = f"{remote_root}/echo args.py"
        py_script = "import json, os, sys\nprint(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd()}, ensure_ascii=False))\n"
        require_ok("file.writeText python script", v3.file_write_text(target, remote_py_path, py_script))
        py_response = require_ok("python.run", v3.python_run(target, remote_py_path, ["alpha beta", 'quote"ok', "unicode-値"], cwd=remote_root))
        py_payload = json.loads(stdout_text(py_response).strip())
        if py_payload["argv"] != ["alpha beta", 'quote"ok', "unicode-値"]:
            raise SystemExit(f"python argv mismatch: {py_payload!r}")
    else:
        status("py.exe unavailable or blocked; skipping optional python.run case")

    status("optional WSL actions")
    wsl_probe = v3.wsl_capture(target, "/usr/bin/uname", ["-a"])
    if wsl_probe.ok:
        wsl_run = require_ok("wsl.run", v3.wsl_run(target, "/bin/echo", ["wsl-run-ok"], cwd="/tmp"))
        if "wsl-run-ok" not in stdout_text(wsl_run):
            raise SystemExit(f"wsl.run stdout mismatch: {wsl_run.get('stdoutText')!r}")
        wsl_script = "set -eu\nprintf 'argc=%s\\n' \"$#\"\nfor arg in \"$@\"; do printf '<%s>\\n' \"$arg\"; done\n"
        wsl_script_response = require_ok("wsl.script.capture", v3.wsl_script_capture(target, wsl_script, ["alpha beta", 'quote"ok', "unicode-値"], cwd="/tmp"))
        wsl_script_out = stdout_text(wsl_script_response)
        for expected in ["argc=3", "<alpha beta>", '<quote"ok>', "<unicode-値>"]:
            if expected not in wsl_script_out:
                raise SystemExit(f"wsl script output missing {expected!r}: {wsl_script_out!r}")
        wsl_script_run = require_ok("wsl.script", v3.wsl_script(target, "printf 'wsl-script-run-ok\\n'\n", cwd="/tmp"))
        if "wsl-script-run-ok" not in stdout_text(wsl_script_run):
            raise SystemExit(f"wsl.script stdout mismatch: {wsl_script_run.get('stdoutText')!r}")
    else:
        status("WSL unavailable or blocked; skipping WSL-specific cases")

    status("optional everything.search")
    everything = v3.everything_search(target, "WindowsRemoteExecutor.Native.exe", max_results=3)
    if everything.ok:
        if not isinstance((everything.response.get("data") or {}).get("results"), list):
            raise SystemExit("everything.search did not return a results list")
    else:
        status("Everything unavailable or blocked; skipping everything.search result validation")

    status("file.deleteTree")
    require_ok("file.deleteTree", v3.file_delete_tree(target, remote_root))

    status("all V3 cases passed")
finally:
    cleanup_root(target, remote_root)
PY
