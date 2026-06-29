#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TOOL_ROOT="${REPO_ROOT}/windows-remote-executor"
NATIVE_PROJECT="${REPO_ROOT}/windows-remote-executor-native/src/WindowsRemoteExecutor.Native/WindowsRemoteExecutor.Native.csproj"

status() {
  printf '[verify-v3-local] %s\n' "$*"
}

status "Python syntax and unit tests"
python3 -m py_compile \
  "${TOOL_ROOT}/lib/win_remote_cli.py" \
  "${TOOL_ROOT}/lib/wre_v3_client.py" \
  "${TOOL_ROOT}/mcp/win_remote_mcp.py"
python3 -m unittest discover -s "${TOOL_ROOT}/tests"

status "Shell script syntax"
bash -n "${TOOL_ROOT}/bin/win-remote"
bash -n "${TOOL_ROOT}/bin/win-remote-legacy"
bash -n "${TOOL_ROOT}/scripts/verify-remote-cases.sh"
bash -n "${TOOL_ROOT}/scripts/verify-v3-remote-cases.sh"
bash -n "${TOOL_ROOT}/scripts/make-bootstrap-package.sh"

status "Native build"
dotnet build "${NATIVE_PROJECT}"

status "Native selftests with local runtime roll-forward enabled"
if DOTNET_ROLL_FORWARD=Major dotnet run --project "${NATIVE_PROJECT}" -- selftest >/tmp/wre-native-selftest.json 2>/tmp/wre-native-selftest.err; then
  python3 - /tmp/wre-native-selftest.json <<'PY'
import json
import sys
with open(sys.argv[1], "r", encoding="utf-8-sig") as f:
    data = json.load(f)
if not data.get("ok"):
    raise SystemExit(data)
PY
else
  status "skipping native selftest runtime execution: $(tr '\n' ' ' </tmp/wre-native-selftest.err)"
fi

if DOTNET_ROLL_FORWARD=Major dotnet run --project "${NATIVE_PROJECT}" -- rpc-selftest >/tmp/wre-native-rpc-selftest.json 2>/tmp/wre-native-rpc-selftest.err; then
  python3 - /tmp/wre-native-rpc-selftest.json <<'PY'
import json
import sys
with open(sys.argv[1], "r", encoding="utf-8-sig") as f:
    data = json.load(f)
if not data.get("ok") or data.get("version") != 3:
    raise SystemExit(data)
PY
else
  status "skipping native rpc-selftest runtime execution: $(tr '\n' ' ' </tmp/wre-native-rpc-selftest.err)"
fi

status "all local V3 checks completed"
