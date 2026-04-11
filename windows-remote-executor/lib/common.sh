#!/usr/bin/env bash

set -euo pipefail

TOOL_ROOT="${TOOL_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
TARGETS_DIR="${TOOL_ROOT}/targets"

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

require_local_deps() {
  require_cmd ssh
  require_cmd scp
  require_cmd iconv
  require_cmd base64
}

encode_utf8_b64() {
  printf '%s' "$1" | base64 | tr -d '\r\n'
}

decode_utf8_b64() {
  local value="$1"
  local decoded=""

  if decoded="$(printf '%s' "${value}" | base64 --decode 2>/dev/null)"; then
    printf '%s' "${decoded}"
    return 0
  fi

  if decoded="$(printf '%s' "${value}" | base64 -d 2>/dev/null)"; then
    printf '%s' "${decoded}"
    return 0
  fi

  if decoded="$(printf '%s' "${value}" | base64 -D 2>/dev/null)"; then
    printf '%s' "${decoded}"
    return 0
  fi

  die "Unable to decode base64 with the local base64 command."
}

normalize_remote_path() {
  local input="${1//\\//}"
  printf '%s' "${input}"
}

remote_cmd_path() {
  local remote_path
  remote_path="$(normalize_remote_path "$1")"
  printf '%s' "${remote_path//\//\\}"
}

resolve_target_env() {
  local raw_target="$1"
  if [[ -f "${raw_target}" ]]; then
    printf '%s' "${raw_target}"
    return
  fi

  local named_target="${TARGETS_DIR}/${raw_target}.env"
  [[ -f "${named_target}" ]] || die "Target env not found: ${named_target}"
  printf '%s' "${named_target}"
}

load_target() {
  local env_file
  env_file="$(resolve_target_env "$1")"
  TARGET_ENV_FILE="${env_file}"

  # shellcheck disable=SC1090
  source "${env_file}"

  : "${TARGET_HOST:?TARGET_HOST is required in ${env_file}}"
  : "${TARGET_USER:?TARGET_USER is required in ${env_file}}"

  TARGET_NAME="${TARGET_NAME:-$1}"
  TARGET_PORT="${TARGET_PORT:-22}"
  TARGET_PS_EXE="${TARGET_PS_EXE:-powershell.exe}"
  TARGET_EVERYTHING_ES="$(normalize_remote_path "${TARGET_EVERYTHING_ES:-C:/CodexRemote/tools/es.exe}")"
  TARGET_NATIVE_EXE="$(normalize_remote_path "${TARGET_NATIVE_EXE:-C:/CodexRemote/tools/WindowsRemoteExecutor.Native.exe}")"
  TARGET_TOOLS_DIR="$(normalize_remote_path "${TARGET_TOOLS_DIR:-$(dirname "${TARGET_NATIVE_EXE}")}")"
  TARGET_NATIVE_LAUNCHER="$(normalize_remote_path "${TARGET_NATIVE_LAUNCHER:-${TARGET_TOOLS_DIR}/WindowsRemoteExecutor.cmd}")"
  TARGET_NATIVE_RELEASES_DIR="$(normalize_remote_path "${TARGET_NATIVE_RELEASES_DIR:-${TARGET_TOOLS_DIR}/releases}")"
  TARGET_NATIVE_CURRENT_FILE="$(normalize_remote_path "${TARGET_NATIVE_CURRENT_FILE:-${TARGET_TOOLS_DIR}/current-release.txt}")"
  TARGET_STAGE_ROOT="$(normalize_remote_path "${TARGET_STAGE_ROOT:-C:/CodexRemote/staging}")"
  TARGET_POLICY_PATH="$(normalize_remote_path "${TARGET_POLICY_PATH:-${TARGET_TOOLS_DIR}/access-policy.json}")"
  TARGET_GUARD_LOG_PATH="$(normalize_remote_path "${TARGET_GUARD_LOG_PATH:-C:/CodexRemote/logs/sshd-guard.log}")"
  TARGET_REPAIR_LOG_PATH="$(normalize_remote_path "${TARGET_REPAIR_LOG_PATH:-C:/CodexRemote/logs/sshd-repair.log}")"
  TARGET_WSL_DISTRO="${TARGET_WSL_DISTRO:-}"
  TARGET_WSL_USER="${TARGET_WSL_USER:-}"
  TARGET_WSL_SHELL="${TARGET_WSL_SHELL:-/bin/bash}"
  TARGET_EXPECTED_LISTEN_ADDRESS="${TARGET_EXPECTED_LISTEN_ADDRESS:-}"
  TARGET_EXPOSURE_MODE="${TARGET_EXPOSURE_MODE:-private-only}"
  TARGET_POLICY_LABEL="${TARGET_POLICY_LABEL:-}"
  TARGET_ACCESS_TOKEN="${TARGET_ACCESS_TOKEN:-}"

  SSH_ARGS=(-p "${TARGET_PORT}" -o BatchMode=yes -o StrictHostKeyChecking=accept-new)
  SCP_ARGS=(-P "${TARGET_PORT}" -o BatchMode=yes -o StrictHostKeyChecking=accept-new)

  if [[ -n "${TARGET_KEY:-}" ]]; then
    SSH_ARGS+=(-i "${TARGET_KEY}")
    SCP_ARGS+=(-i "${TARGET_KEY}")
  fi
}

sha256_hex() {
  local input="$1"

  if command -v shasum >/dev/null 2>&1; then
    printf '%s' "${input}" | shasum -a 256 | awk '{print $1}'
    return
  fi

  if command -v sha256sum >/dev/null 2>&1; then
    printf '%s' "${input}" | sha256sum | awk '{print $1}'
    return
  fi

  if command -v openssl >/dev/null 2>&1; then
    printf '%s' "${input}" | openssl dgst -sha256 -binary | xxd -p -c 256
    return
  fi

  die "Missing a SHA-256 command: need shasum, sha256sum, or openssl."
}

generate_access_token() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32 | tr -d '\r\n'
    return
  fi

  if [[ -r /dev/urandom ]]; then
    od -An -tx1 -N32 /dev/urandom | tr -d ' \n'
    return
  fi

  die "Unable to generate an access token on this machine."
}

is_ipv4_literal() {
  local candidate="$1"
  [[ "${candidate}" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || return 1

  local octet
  local IFS='.'
  read -r -a octets <<<"${candidate}"
  [[ ${#octets[@]} -eq 4 ]] || return 1

  for octet in "${octets[@]}"; do
    [[ "${octet}" =~ ^[0-9]{1,3}$ ]] || return 1
    (( octet >= 0 && octet <= 255 )) || return 1
  done
}

encode_powershell() {
  printf '%s' "$1" | iconv -f UTF-8 -t UTF-16LE | base64 | tr -d '\r\n'
}

compose_powershell_script() {
  local body="$1"
  cat <<EOF
\$ErrorActionPreference = 'Stop'
\$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(\$false)
\$OutputEncoding = [Console]::OutputEncoding
try { chcp 65001 > \$null } catch {}
${body}
EOF
}

ssh_raw() {
  local target="$1"
  shift
  ssh "${SSH_ARGS[@]}" "${target}" "$@"
}

scp_raw() {
  scp "${SCP_ARGS[@]}" "$@"
}

scp_remote_spec() {
  local remote_path
  remote_path="$(normalize_remote_path "$1")"
  [[ "${remote_path}" != *" "* ]] || die "Remote paths with spaces are not supported by scp wrapper: ${remote_path}"
  printf "%s@%s:%s" "${TARGET_USER}" "${TARGET_HOST}" "${remote_path}"
}

ensure_remote_dir() {
  local remote_dir
  remote_dir="$(normalize_remote_path "$1")"
  local remote_dir_cmd
  remote_dir_cmd="$(remote_cmd_path "${remote_dir}")"
  ssh_raw "${TARGET_USER}@${TARGET_HOST}" \
    "cmd.exe /d /s /c \"if not exist ${remote_dir_cmd} mkdir ${remote_dir_cmd}\"" \
    >/dev/null
}

ensure_remote_parent() {
  local remote_path
  remote_path="$(normalize_remote_path "$1")"
  local parent
  parent="$(dirname "${remote_path}")"
  ensure_remote_dir "${parent}"
}
