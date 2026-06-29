#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${TOOL_ROOT}/.." && pwd)"
DEFAULT_OUT_DIR="${TOOL_ROOT}/dist"

usage() {
  cat >&2 <<'EOF'
Usage:
  make-bootstrap-package.sh --native-zip <release-scd-or-fdd-win-x64.zip> --public-key <id_ed25519.pub> [options]

Options:
  --out-dir <dir>          Output directory. Default: windows-remote-executor/dist
  --target-name <name>     Suggested target name. Default: win-new
  --target-user <user>     Windows user to authorize. Default: Administrator
  --listen-address <ip>    Optional fixed SSH ListenAddress for the installer.
  --codex-root <path>      Remote install root. Default: C:\CodexRemote
  --access-token <token>   Package an access token for native policy.
  --generate-token         Generate and package a random access token.
  --command-mode <mode>    standard or argv-only. Default: standard
  --install-tailscale      Add README command example with -InstallTailscale.

The package is meant for desktop-only bootstrap on a new Windows machine. Copy the
zip to the Windows desktop, extract it, open an elevated PowerShell, and run the
included install-wre-new-host.ps1.

For a brand-new Windows machine that may not have the .NET 8 runtime installed,
prefer the self-contained release asset
(windows-remote-executor-native-v<version>-scd-win-x64.zip). The framework-dependent
asset (...-fdd-win-x64.zip) is also accepted but requires the .NET 8 runtime to be
present on the target.
EOF
}

require_value() {
  local option="$1"
  local value="${2:-}"
  if [[ -z "${value}" ]]; then
    printf 'error: %s requires a value\n' "${option}" >&2
    exit 2
  fi
  printf '%s' "${value}"
}

sha256_hex() {
  python3 - "$1" <<'PY'
import hashlib
import sys
print(hashlib.sha256(sys.argv[1].encode("utf-8")).hexdigest())
PY
}

generate_token() {
  python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
}

json_quote() {
  python3 - "$1" <<'PY'
import json
import sys
print(json.dumps(sys.argv[1], ensure_ascii=False))
PY
}

NATIVE_ZIP=""
PUBLIC_KEY_PATH=""
OUT_DIR="${DEFAULT_OUT_DIR}"
TARGET_NAME="win-new"
TARGET_USER="Administrator"
LISTEN_ADDRESS=""
CODEX_ROOT='C:\CodexRemote'
ACCESS_TOKEN=""
GENERATE_TOKEN=0
COMMAND_MODE="standard"
INSTALL_TAILSCALE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --native-zip)
      NATIVE_ZIP="$(require_value "$1" "${2:-}")"
      shift 2
      ;;
    --public-key)
      PUBLIC_KEY_PATH="$(require_value "$1" "${2:-}")"
      shift 2
      ;;
    --out-dir)
      OUT_DIR="$(require_value "$1" "${2:-}")"
      shift 2
      ;;
    --target-name)
      TARGET_NAME="$(require_value "$1" "${2:-}")"
      shift 2
      ;;
    --target-user)
      TARGET_USER="$(require_value "$1" "${2:-}")"
      shift 2
      ;;
    --listen-address)
      LISTEN_ADDRESS="$(require_value "$1" "${2:-}")"
      shift 2
      ;;
    --codex-root)
      CODEX_ROOT="$(require_value "$1" "${2:-}")"
      shift 2
      ;;
    --access-token)
      ACCESS_TOKEN="$(require_value "$1" "${2:-}")"
      shift 2
      ;;
    --generate-token)
      GENERATE_TOKEN=1
      shift
      ;;
    --command-mode)
      COMMAND_MODE="$(require_value "$1" "${2:-}")"
      shift 2
      ;;
    --install-tailscale)
      INSTALL_TAILSCALE=1
      shift
      ;;
    -h|--help|help)
      usage
      exit 0
      ;;
    *)
      printf 'error: unknown option: %s\n' "$1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ -z "${NATIVE_ZIP}" || -z "${PUBLIC_KEY_PATH}" ]]; then
  usage
  exit 2
fi

case "${COMMAND_MODE}" in
  standard|argv-only) ;;
  *)
    printf 'error: unsupported command mode: %s\n' "${COMMAND_MODE}" >&2
    exit 2
    ;;
esac

if [[ ${GENERATE_TOKEN} -eq 1 && -n "${ACCESS_TOKEN}" ]]; then
  printf 'error: use either --access-token or --generate-token, not both\n' >&2
  exit 2
fi

if [[ ${GENERATE_TOKEN} -eq 1 ]]; then
  ACCESS_TOKEN="$(generate_token)"
fi

NATIVE_KIND="$(python3 - "${NATIVE_ZIP}" <<'PY'
import pathlib
import re
import sys
import zipfile

path = pathlib.Path(sys.argv[1])
if not path.is_file():
    raise SystemExit(f"native zip not found: {path}")

scd = re.match(r"windows-remote-executor-native-v[^/]+-scd-win-x64\.zip$", path.name)
fdd = re.match(r"windows-remote-executor-native-v[^/]+-fdd-win-x64\.zip$", path.name)
if not scd and not fdd:
    raise SystemExit(
        "native zip must be a GitHub release asset named "
        "windows-remote-executor-native-v<version>-scd-win-x64.zip (self-contained, "
        "preferred for new hosts) or windows-remote-executor-native-v<version>-fdd-win-x64.zip "
        "(framework-dependent, requires .NET 8 on the target)"
    )

with zipfile.ZipFile(path) as zf:
    names = {pathlib.Path(info.filename).name for info in zf.infolist() if not info.is_dir()}

if scd:
    required = {"WindowsRemoteExecutor.Native.exe"}
    kind = "scd"
else:
    required = {
        "WindowsRemoteExecutor.Native.exe",
        "WindowsRemoteExecutor.Native.dll",
        "WindowsRemoteExecutor.Native.runtimeconfig.json",
        "WindowsRemoteExecutor.Native.deps.json",
    }
    kind = "fdd"

missing = sorted(required - names)
if missing:
    raise SystemExit("native zip missing required files: " + ", ".join(missing))

print(kind)
PY
)"

if [[ "${NATIVE_KIND}" == "scd" ]]; then
  RUNTIME_NOTE="self-contained (bundles the .NET runtime; no .NET 8 install required on the target)"
else
  RUNTIME_NOTE="framework-dependent (requires the .NET 8 runtime to be installed on the target)"
fi

if [[ ! -f "${PUBLIC_KEY_PATH}" ]]; then
  printf 'error: public key not found: %s\n' "${PUBLIC_KEY_PATH}" >&2
  exit 2
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
SAFE_TARGET="$(printf '%s' "${TARGET_NAME}" | tr -c 'A-Za-z0-9_.-' '-')"
PACKAGE_NAME="wre-bootstrap-${SAFE_TARGET}-${STAMP}"
PACKAGE_DIR="${OUT_DIR}/${PACKAGE_NAME}"
NATIVE_DIR="${PACKAGE_DIR}/native"

rm -rf "${PACKAGE_DIR}"
mkdir -p "${NATIVE_DIR}"
python3 - "${NATIVE_ZIP}" "${NATIVE_DIR}" <<'PY'
import pathlib
import sys
import zipfile

zip_path = pathlib.Path(sys.argv[1])
out_dir = pathlib.Path(sys.argv[2])
with zipfile.ZipFile(zip_path) as zf:
    for info in zf.infolist():
        if info.is_dir():
            continue
        name = pathlib.PurePosixPath(info.filename).name
        if not name:
            continue
        target = out_dir / name
        with zf.open(info) as src, target.open("wb") as dst:
            dst.write(src.read())
PY

cp "${TOOL_ROOT}/windows/install-wre-new-host.ps1" "${PACKAGE_DIR}/install-wre-new-host.ps1"
cp "${PUBLIC_KEY_PATH}" "${PACKAGE_DIR}/authorized_key.pub"

if [[ -n "${ACCESS_TOKEN}" ]]; then
  printf '%s\n' "${ACCESS_TOKEN}" >"${PACKAGE_DIR}/access-token.txt"
fi

INSTALL_ARGS=(
  "-TargetName" "${TARGET_NAME}"
  "-TargetUser" "${TARGET_USER}"
  "-CodexRoot" "${CODEX_ROOT}"
  "-CommandMode" "${COMMAND_MODE}"
)
if [[ -n "${LISTEN_ADDRESS}" ]]; then
  INSTALL_ARGS+=("-ListenAddress" "${LISTEN_ADDRESS}")
fi
if [[ ${INSTALL_TAILSCALE} -eq 1 ]]; then
  INSTALL_ARGS+=("-InstallTailscale")
fi

INSTALL_ARGS_PS=""
for arg in "${INSTALL_ARGS[@]}"; do
  quoted="$(json_quote "${arg}")"
  INSTALL_ARGS_PS+=" ${quoted}"
done

cat >"${PACKAGE_DIR}/README.txt" <<EOF
Windows Remote Executor bootstrap package

Target name: ${TARGET_NAME}
Target user: ${TARGET_USER}
Codex root: ${CODEX_ROOT}
Command mode: ${COMMAND_MODE}
Native asset: $(basename "${NATIVE_ZIP}")
Native runtime: ${RUNTIME_NOTE}
Access token packaged: $([[ -n "${ACCESS_TOKEN}" ]] && printf yes || printf no)

Run on the Windows desktop:

1. Extract this zip to a local folder.
2. Open Windows Terminal / PowerShell as Administrator.
3. Run:

   Set-ExecutionPolicy Bypass -Scope Process -Force
   cd <this extracted folder>
   .\install-wre-new-host.ps1${INSTALL_ARGS_PS}

The installer writes target-*.env beside itself and under C:\CodexRemote\logs.
Copy that target template back to the controller, set TARGET_KEY to the matching
private key, then run:

   ./windows-remote-executor/bin/win-remote probe ${TARGET_NAME}
   ./windows-remote-executor/scripts/verify-v3-remote-cases.sh ${TARGET_NAME}
   ./windows-remote-executor/scripts/verify-remote-cases.sh ${TARGET_NAME}
EOF

cat >"${PACKAGE_DIR}/manifest.json" <<EOF
{
  "package": $(json_quote "${PACKAGE_NAME}"),
  "createdAt": $(json_quote "$(date -u +%Y-%m-%dT%H:%M:%SZ)"),
  "targetName": $(json_quote "${TARGET_NAME}"),
  "targetUser": $(json_quote "${TARGET_USER}"),
  "listenAddress": $(json_quote "${LISTEN_ADDRESS}"),
  "codexRoot": $(json_quote "${CODEX_ROOT}"),
  "commandMode": $(json_quote "${COMMAND_MODE}"),
  "nativeKind": $(json_quote "${NATIVE_KIND}"),
  "nativeZip": $(json_quote "$(basename "${NATIVE_ZIP}")"),
  "nativeZipSha256": $(json_quote "$(python3 - "${NATIVE_ZIP}" <<'PY'
import hashlib
import sys
from pathlib import Path
print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)"),
  "publicKeySha256": $(json_quote "$(sha256_hex "$(cat "${PUBLIC_KEY_PATH}")")"),
  "accessTokenPackaged": $([[ -n "${ACCESS_TOKEN}" ]] && printf true || printf false)
}
EOF

(
  cd "${OUT_DIR}"
  zip -rq "${PACKAGE_NAME}.zip" "${PACKAGE_NAME}"
)

printf 'created: %s\n' "${PACKAGE_DIR}.zip"
printf 'dir: %s\n' "${PACKAGE_DIR}"
if [[ -n "${ACCESS_TOKEN}" ]]; then
  printf 'access-token: %s\n' "${ACCESS_TOKEN}"
fi
