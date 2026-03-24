#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="${ROOT_DIR}/src/WindowsRemoteExecutor.Native/WindowsRemoteExecutor.Native.csproj"
OUT_DIR="${ROOT_DIR}/publish/scd-win-x64"

dotnet publish "${PROJECT}" \
  -c Release \
  -r win-x64 \
  --self-contained true \
  -p:PublishSingleFile=true \
  -o "${OUT_DIR}"

echo "Published self-contained build to ${OUT_DIR}"
