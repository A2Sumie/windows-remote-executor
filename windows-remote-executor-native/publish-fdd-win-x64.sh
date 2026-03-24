#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="${ROOT_DIR}/src/WindowsRemoteExecutor.Native/WindowsRemoteExecutor.Native.csproj"
OUT_DIR="${ROOT_DIR}/publish/fdd-win-x64"

dotnet publish "${PROJECT}" \
  -c Release \
  -r win-x64 \
  --self-contained false \
  -p:PublishSingleFile=false \
  -o "${OUT_DIR}"

echo "Published framework-dependent build to ${OUT_DIR}"
