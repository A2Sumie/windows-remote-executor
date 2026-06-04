#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WIN_REMOTE="${TOOL_ROOT}/bin/win-remote"

TARGET="${1:-}"
ARGV_ONLY=0

if [[ "${TARGET}" == "--argv-only" ]]; then
  ARGV_ONLY=1
  TARGET="${2:-}"
fi

if [[ -z "${TARGET}" ]]; then
  cat >&2 <<'EOF'
Usage:
  verify-remote-cases.sh <target-name-or-env-file>
  verify-remote-cases.sh --argv-only <target-name-or-env-file>

Runs a remote regression matrix for quoting, spaces, stdin EOF, large
captured output, raw PowerShell blocking, and optional WSL staging.
EOF
  exit 2
fi

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    printf 'error: missing required command: %s\n' "$1" >&2
    exit 1
  }
}

json_get() {
  local file="$1"
  local expr="$2"
  python3 - "$file" "$expr" <<'PY'
import json
import sys

path, expr = sys.argv[1], sys.argv[2]
with open(path, "r", encoding="utf-8-sig") as f:
    data = json.load(f)

value = data
for part in expr.split("."):
    if part:
        value = value[part]

if isinstance(value, str):
    print(value, end="")
else:
    print(json.dumps(value, ensure_ascii=False), end="")
PY
}

assert_json_int_ge() {
  local file="$1"
  local expr="$2"
  local expected="$3"
  local actual
  actual="$(json_get "${file}" "${expr}")"
  if (( actual < expected )); then
    printf 'error: expected %s in %s to be >= %s, got %s\n' "${expr}" "${file}" "${expected}" "${actual}" >&2
    exit 1
  fi
}

assert_json_eq() {
  local file="$1"
  local expr="$2"
  local expected="$3"
  local actual
  actual="$(json_get "${file}" "${expr}")"
  if [[ "${actual}" != "${expected}" ]]; then
    printf 'error: expected %s in %s to equal %s, got %s\n' "${expr}" "${file}" "${expected}" "${actual}" >&2
    exit 1
  fi
}

assert_json_contains() {
  local file="$1"
  local expr="$2"
  local needle="$3"
  local actual
  actual="$(json_get "${file}" "${expr}")"
  if [[ "${actual}" != *"${needle}"* ]]; then
    printf 'error: expected %s in %s to contain %s\nactual: %s\n' "${expr}" "${file}" "${needle}" "${actual}" >&2
    exit 1
  fi
}

status_line() {
  printf '[verify-remote-cases] %s\n' "$*"
}

require_cmd python3
require_cmd cmp

TMP_DIR="$(mktemp -d)"
REMOTE_CASE_ROOT="C:/CodexRemote/inbox/wre case $(date +%Y%m%d-%H%M%S)-$$"

cleanup() {
  rm -rf "${TMP_DIR}"
  "${WIN_REMOTE}" exec "${TARGET}" --stdin >/dev/null 2>&1 <<EOF || true
\$ErrorActionPreference = 'SilentlyContinue'
Remove-Item -LiteralPath '${REMOTE_CASE_ROOT}' -Recurse -Force
EOF
}
trap cleanup EXIT

status_line "probe native executor"
PROBE_JSON="${TMP_DIR}/probe.json"
"${WIN_REMOTE}" probe "${TARGET}" --out "${PROBE_JSON}" >/dev/null
python3 - "${PROBE_JSON}" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8-sig") as f:
    payload = json.load(f)

if not isinstance(payload, dict) or not payload:
    raise SystemExit("probe did not return a JSON object")
PY

if [[ ${ARGV_ONLY} -eq 0 ]]; then
  status_line "capture large stdout/stderr without pipe deadlock"
  LARGE_JSON="${TMP_DIR}/large.json"
  "${WIN_REMOTE}" capture "${TARGET}" --out "${LARGE_JSON}" py.exe -3 -c 'import sys; sys.stdout.write("x"*1048576); sys.stderr.write("e"*65536)' >/dev/null
  assert_json_eq "${LARGE_JSON}" "exitCode" "0"
  assert_json_int_ge "${LARGE_JSON}" "stdoutBytes" 1048576
  assert_json_int_ge "${LARGE_JSON}" "stderrBytes" 65536

  status_line "capture process that reads stdin observes EOF"
  STDIN_JSON="${TMP_DIR}/stdin.json"
  "${WIN_REMOTE}" capture "${TARGET}" --out "${STDIN_JSON}" py.exe -3 -c 'import sys; print("stdin-len=%d" % len(sys.stdin.read()))' >/dev/null
  assert_json_eq "${STDIN_JSON}" "exitCode" "0"
  assert_json_contains "${STDIN_JSON}" "stdoutText" "stdin-len=0"
else
  status_line "argv-only allows ordinary native argv capture"
  WHOAMI_JSON="${TMP_DIR}/whoami.json"
  "${WIN_REMOTE}" capture "${TARGET}" --out "${WHOAMI_JSON}" whoami.exe /user >/dev/null
  assert_json_eq "${WHOAMI_JSON}" "exitCode" "0"
  assert_json_int_ge "${WHOAMI_JSON}" "stdoutBytes" 1
fi

status_line "raw PowerShell transport is blocked by default"
RAW_PS_LOG="${TMP_DIR}/raw-powershell.log"
if "${WIN_REMOTE}" capture "${TARGET}" powershell.exe -NoProfile -Command 'Write-Output should-not-run' >"${RAW_PS_LOG}" 2>&1; then
  printf 'error: raw PowerShell capture unexpectedly succeeded\n' >&2
  exit 1
fi
if ! grep -q 'blocks raw PowerShell transport by default' "${RAW_PS_LOG}"; then
  printf 'error: raw PowerShell failure did not mention guardrail\n' >&2
  sed -n '1,120p' "${RAW_PS_LOG}" >&2
  exit 1
fi

status_line "structured PowerShell exec path still works"
EXEC_LOG="${TMP_DIR}/exec.log"
if [[ ${ARGV_ONLY} -eq 1 ]]; then
  status_line "argv-only still allows staged exec bridge"
fi
"${WIN_REMOTE}" exec "${TARGET}" --stdin >"${EXEC_LOG}" <<'EOF'
Write-Output 'structured-exec-ok'
EOF
grep -q 'structured-exec-ok' "${EXEC_LOG}"

status_line "put/get remote path and filename containing spaces"
LOCAL_PAYLOAD="${TMP_DIR}/payload with spaces.txt"
ROUNDTRIP_PAYLOAD="${TMP_DIR}/roundtrip payload with spaces.txt"
printf 'space path payload\nquote="ok"\nunicode=値\n' >"${LOCAL_PAYLOAD}"
REMOTE_PAYLOAD="${REMOTE_CASE_ROOT}/payload with spaces.txt"
"${WIN_REMOTE}" put "${TARGET}" "${LOCAL_PAYLOAD}" "${REMOTE_PAYLOAD}" >/dev/null
"${WIN_REMOTE}" get "${TARGET}" "${REMOTE_PAYLOAD}" "${ROUNDTRIP_PAYLOAD}" >/dev/null
cmp "${LOCAL_PAYLOAD}" "${ROUNDTRIP_PAYLOAD}"

status_line "put/get remote path containing shell metacharacters"
REMOTE_WEIRD_NAME='literal `tick` (semi; amp&) bang! caret^.txt'
REMOTE_WEIRD_PAYLOAD="${REMOTE_CASE_ROOT}/${REMOTE_WEIRD_NAME}"
ROUNDTRIP_WEIRD_PAYLOAD="${TMP_DIR}/roundtrip weird payload.txt"
"${WIN_REMOTE}" put "${TARGET}" "${LOCAL_PAYLOAD}" "${REMOTE_WEIRD_PAYLOAD}" >/dev/null
"${WIN_REMOTE}" get "${TARGET}" "${REMOTE_WEIRD_PAYLOAD}" "${ROUNDTRIP_WEIRD_PAYLOAD}" >/dev/null
cmp "${LOCAL_PAYLOAD}" "${ROUNDTRIP_WEIRD_PAYLOAD}"

if [[ ${ARGV_ONLY} -eq 0 ]]; then
  status_line "python script path, cwd, and argv with spaces/quotes/unicode"
  REMOTE_SCRIPT="${REMOTE_CASE_ROOT}/script dir/echo args with spaces.py"
  "${WIN_REMOTE}" put "${TARGET}" "${TOOL_ROOT}/examples/echo_args.py" "${REMOTE_SCRIPT}" >/dev/null
  PY_LOG="${TMP_DIR}/py.log"
  "${WIN_REMOTE}" py "${TARGET}" "${REMOTE_SCRIPT}" --cwd "${REMOTE_CASE_ROOT}/script dir" -- \
    "alpha beta" \
    'quote"ok' \
    'unicode-値' >"${PY_LOG}"
  python3 - "${PY_LOG}" "${REMOTE_CASE_ROOT}/script dir" <<'PY'
import json
import sys

path, expected_cwd = sys.argv[1], sys.argv[2].replace("/", "\\")
with open(path, "r", encoding="utf-8-sig") as f:
    payload = json.load(f)

argv = payload["argv"]
cwd = payload["cwd"]
required = ["alpha beta", 'quote"ok', "unicode-値"]
if argv[-3:] != required:
    raise SystemExit(f"argv mismatch: {argv!r}")
if cwd.lower() != expected_cwd.lower():
    raise SystemExit(f"cwd mismatch: {cwd!r} != {expected_cwd!r}")
PY
else
  status_line "argv-only blocks Python helper and interpreter attempts"
  PY_LOG="${TMP_DIR}/py-blocked.log"
  if "${WIN_REMOTE}" py "${TARGET}" C:/CodexRemote/inbox/does-not-matter.py >"${PY_LOG}" 2>&1; then
    printf 'error: win-remote py unexpectedly succeeded in argv-only mode\n' >&2
    exit 1
  fi
  grep -q 'argv-only' "${PY_LOG}"

  PY_CAPTURE_LOG="${TMP_DIR}/py-capture-blocked.log"
  if "${WIN_REMOTE}" capture "${TARGET}" py.exe -3 -c 'print("should-not-run")' >"${PY_CAPTURE_LOG}" 2>&1; then
    printf 'error: py.exe capture unexpectedly succeeded in argv-only mode\n' >&2
    exit 1
  fi
  grep -q 'argv-only' "${PY_CAPTURE_LOG}"
fi

if [[ ${ARGV_ONLY} -eq 0 ]]; then
  status_line "direct capture argv with spaces/quotes/unicode"
  ARGV_JSON="${TMP_DIR}/argv.json"
  "${WIN_REMOTE}" capture "${TARGET}" --out "${ARGV_JSON}" py.exe -3 -X utf8 -c 'import json, sys; print(json.dumps(sys.argv[1:], ensure_ascii=False))' \
    "alpha beta" \
    'quote"ok' \
    'unicode-値' >/dev/null
  assert_json_eq "${ARGV_JSON}" "exitCode" "0"
  python3 - "${ARGV_JSON}" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8-sig") as f:
    payload = json.load(f)

argv = json.loads(payload["stdoutText"])
expected = ["alpha beta", 'quote"ok', "unicode-値"]
if argv != expected:
    raise SystemExit(f"argv mismatch: {argv!r}")
PY
fi

if [[ ${ARGV_ONLY} -eq 0 ]]; then
  status_line "optional WSL structured argv/staging"
  WSL_PROBE="${TMP_DIR}/wsl-probe.json"
  if "${WIN_REMOTE}" wsl-capture "${TARGET}" --out "${WSL_PROBE}" /usr/bin/uname -a >/dev/null 2>&1; then
  WSL_JSON="${TMP_DIR}/wsl-sh.json"
  "${WIN_REMOTE}" wsl-sh-capture "${TARGET}" --out "${WSL_JSON}" --stdin -- \
    "alpha beta" \
    'quote"ok' \
    'unicode-値' >/dev/null <<'EOF'
set -eu
printf 'argc=%s\n' "$#"
for arg in "$@"; do
  printf '<%s>\n' "$arg"
done
EOF
  assert_json_eq "${WSL_JSON}" "exitCode" "0"
  assert_json_contains "${WSL_JSON}" "stdoutText" "argc=3"
  assert_json_contains "${WSL_JSON}" "stdoutText" "<alpha beta>"
  assert_json_contains "${WSL_JSON}" "stdoutText" '<quote"ok>'
  assert_json_contains "${WSL_JSON}" "stdoutText" "<unicode-値>"

  WSL_PY_PROBE_JSON="${TMP_DIR}/wsl-python-probe.json"
  if "${WIN_REMOTE}" wsl-sh-capture "${TARGET}" --out "${WSL_PY_PROBE_JSON}" --stdin >/dev/null <<'EOF'
set -eu
command -v python3 || command -v python
EOF
  then
    WSL_PYTHON_PATH="$(json_get "${WSL_PY_PROBE_JSON}" "stdoutText")"
    WSL_PYTHON_PATH="${WSL_PYTHON_PATH%%$'\n'*}"
    WSL_PY_SCRIPT="/tmp/win-remote-wsl-py-${RANDOM}-$$.py"
    WSL_PY_CREATE_JSON="${TMP_DIR}/wsl-python-create.json"
    "${WIN_REMOTE}" wsl-sh-capture "${TARGET}" --out "${WSL_PY_CREATE_JSON}" --stdin -- "${WSL_PY_SCRIPT}" >/dev/null <<'EOF'
set -eu
cat >"$1" <<'PY'
import json
import os
import sys

print(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd()}, ensure_ascii=False))
PY
chmod 700 "$1"
EOF
    assert_json_eq "${WSL_PY_CREATE_JSON}" "exitCode" "0"

    status_line "optional WSL Python argv capture"
    WSL_PY_JSON="${TMP_DIR}/wsl-python.json"
    "${WIN_REMOTE}" wsl-py-capture "${TARGET}" --out "${WSL_PY_JSON}" --python "${WSL_PYTHON_PATH}" --cwd /tmp "${WSL_PY_SCRIPT}" -- \
      "alpha beta" \
      'quote"ok' \
      'unicode-値' >/dev/null
    assert_json_eq "${WSL_PY_JSON}" "exitCode" "0"
    python3 - "${WSL_PY_JSON}" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8-sig") as f:
    capture = json.load(f)

payload = json.loads(capture["stdoutText"])
expected = ["alpha beta", 'quote"ok', "unicode-値"]
if payload["argv"] != expected:
    raise SystemExit(f"argv mismatch: {payload['argv']!r}")
if payload["cwd"] != "/tmp":
    raise SystemExit(f"cwd mismatch: {payload['cwd']!r}")
PY
    "${WIN_REMOTE}" wsl-sh-capture "${TARGET}" --out "${TMP_DIR}/wsl-python-cleanup.json" --stdin -- "${WSL_PY_SCRIPT}" >/dev/null <<'EOF'
set -eu
rm -f "$1"
EOF
  else
    status_line "WSL Python unavailable on target; skipping wsl-py case"
  fi
  else
    status_line "WSL unavailable or blocked on target; skipping WSL-specific case"
  fi
else
  status_line "argv-only blocks WSL command/script paths"
  WSL_LOG="${TMP_DIR}/wsl-blocked.log"
  if "${WIN_REMOTE}" wsl-capture "${TARGET}" /usr/bin/uname -a >"${WSL_LOG}" 2>&1; then
    printf 'error: WSL capture unexpectedly succeeded in argv-only mode\n' >&2
    exit 1
  fi
  grep -q 'argv-only' "${WSL_LOG}"
fi

status_line "all cases passed"
