#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="${ROOT_DIR}/src/WindowsRemoteExecutor.Native/WindowsRemoteExecutor.Native.csproj"
OUT_DIR="${ROOT_DIR}/publish/fdd-win-x64"

copy_windows_runtime_asset() {
  local package_name="$1"
  local file_name="$2"
  local version
  version="$(sed -nE "s/.*PackageReference Include=\"${package_name}\" Version=\"([^\"]+)\".*/\\1/p" "${PROJECT}" | head -n 1)"
  if [[ -z "${version}" ]]; then
    echo "Unable to find ${package_name} PackageReference version in ${PROJECT}" >&2
    exit 1
  fi

  local package_dir="${NUGET_PACKAGES:-${HOME}/.nuget/packages}"
  local asset_path="${package_dir}/$(printf '%s' "${package_name}" | tr '[:upper:]' '[:lower:]')/${version}/runtimes/win/lib/net8.0/${file_name}"
  if [[ ! -f "${asset_path}" ]]; then
    echo "Missing Windows runtime asset: ${asset_path}" >&2
    exit 1
  fi

  cp "${asset_path}" "${OUT_DIR}/${file_name}"
}

dotnet publish "${PROJECT}" \
  -c Release \
  -r win-x64 \
  --self-contained false \
  -p:PublishSingleFile=false \
  -o "${OUT_DIR}"

copy_windows_runtime_asset "System.Management" "System.Management.dll"

echo "Published framework-dependent build to ${OUT_DIR}"
