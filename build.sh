#!/usr/bin/env bash
# Build the `runtime` stage of containers/Containerfile as localhost/microvpn:dev.
# On Windows (Git Bash) without podman, re-run inside the WSL `Ubuntu` distro.
set -euo pipefail
cd "$(dirname "$0")"
if ! command -v podman >/dev/null 2>&1 && command -v wsl.exe >/dev/null 2>&1; then
  here=$(pwd)
  case "$here" in /[a-zA-Z]/*) here="/mnt/${here:1:1}${here:2}" ;; esac
  export MSYS_NO_PATHCONV=1
  exec wsl.exe -d Ubuntu -- bash -c "cd '$here' && ./build.sh"
fi
sha=$(git rev-parse HEAD 2>/dev/null || echo dev)
podman build --target runtime --build-arg "GIT_SHA=$sha" -t localhost/microvpn:dev -f containers/Containerfile .
