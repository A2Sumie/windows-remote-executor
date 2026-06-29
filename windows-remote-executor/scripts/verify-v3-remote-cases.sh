#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TARGET="${1:-}"

if [[ -z "${TARGET}" ]]; then
  cat >&2 <<'EOF'
Usage:
  verify-v3-remote-cases.sh <target-name-or-env-file>

Runs the V3 rpc-stdio remote matrix against a target that already has a native
executor with rpc-stdio support. This does not replace the V2 matrix; run
verify-remote-cases.sh afterwards before promoting V3.
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


target = cli.load_target(TARGET_NAME)
remote_root = f"C:/CodexRemote/inbox/wre-v3-case-{time.strftime('%Y%m%d-%H%M%S')}-{id(target) % 100000}"

try:
    status("host.capabilities")
    capabilities = require_ok("host.capabilities", v3.host_capabilities(target))
    data = capabilities.get("data") or {}
    actions = data.get("actions") if isinstance(data, dict) else None
    if data.get("version") != 3 or not isinstance(actions, list) or "process.capture" not in actions:
        raise SystemExit(f"unexpected capabilities payload: {json.dumps(data, ensure_ascii=False)}")

    status("host.probe")
    probe = require_ok("host.probe", v3.host_probe(target))
    if not isinstance(probe.get("data"), dict):
        raise SystemExit("host.probe did not return a data object")

    status("process.capture whoami.exe")
    whoami = require_ok("process.capture whoami.exe", v3.process_capture(target, "whoami.exe"))
    if int(whoami.get("stdoutBytes") or 0) < 1:
        raise SystemExit("whoami.exe returned empty stdout")

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

    status("script.capture powershell")
    ps_script = "$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)\nWrite-Output 'v3-powershell-ok 値'\n"
    ps_response = require_ok("script.capture powershell", v3.script_capture(target, ps_script, cwd=remote_root))
    if "v3-powershell-ok" not in str(ps_response.get("stdoutText")):
        raise SystemExit(f"powershell stdout mismatch: {ps_response.get('stdoutText')!r}")

    status("script.capture cmd")
    cmd_response = require_ok("script.capture cmd", v3.script_capture(target, "@echo off\r\necho v3-cmd-ok\r\n", kind="cmd", cwd=remote_root))
    if "v3-cmd-ok" not in str(cmd_response.get("stdoutText")):
        raise SystemExit(f"cmd stdout mismatch: {cmd_response.get('stdoutText')!r}")

    status("drive-relative path is rejected locally")
    try:
        v3.file_read_text(target, "D:CodexRemoteinboxbad.txt")
    except cli.WinRemoteError as exc:
        if "drive-relative" not in str(exc):
            raise
    else:
        raise SystemExit("drive-relative path unexpectedly reached transport")

    status("optional py.exe argv/stdin/capture case")
    py_probe = v3.process_capture(target, "py.exe", ["-3", "-c", "print('py-ok')"])
    if py_probe.ok:
        argv_args = ["-3", "-X", "utf8", "-c", "import json, sys; print(json.dumps(sys.argv[1:], ensure_ascii=False))", "alpha beta", 'quote"ok', "unicode-値"]
        argv_response = require_ok("process.capture py.exe argv", v3.process_capture(target, "py.exe", argv_args, cwd=remote_root))
        captured_args = json.loads(str(argv_response.get("stdoutText")).strip())
        if captured_args != ["alpha beta", 'quote"ok', "unicode-値"]:
            raise SystemExit(f"py argv mismatch: {captured_args!r}")

        stdin_response = require_ok(
            "process.capture py.exe stdin EOF",
            v3.process_capture(target, "py.exe", ["-3", "-c", "import sys; print('stdin-len=%d' % len(sys.stdin.read()))"]),
        )
        if "stdin-len=0" not in str(stdin_response.get("stdoutText")):
            raise SystemExit(f"stdin EOF mismatch: {stdin_response.get('stdoutText')!r}")
    else:
        status("py.exe unavailable or blocked; skipping optional py.exe cases")

    status("all V3 cases passed")
finally:
    cleanup_root(target, remote_root)
PY
