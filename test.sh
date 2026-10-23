#!/usr/bin/env bash
# Run the test suite inside the `test` stage of containers/Containerfile; arguments go to pytest.
# Nothing runs on the host. On Windows (Git Bash) without podman, re-run inside the WSL `Ubuntu`
# distro, which has podman; MSYS_NO_PATHCONV stops Git Bash rewriting the /mnt/c path.
set -euo pipefail
cd "$(dirname "$0")"
if ! command -v podman >/dev/null 2>&1 && command -v wsl.exe >/dev/null 2>&1; then
  here=$(pwd)
  case "$here" in /[a-zA-Z]/*) here="/mnt/${here:1:1}${here:2}" ;; esac
  args=""
  [ $# -gt 0 ] && args=$(printf '%q ' "$@")
  export MSYS_NO_PATHCONV=1
  exec wsl.exe -d Ubuntu -- bash -c "cd '$here' && ./test.sh $args"
fi
podman build -q --target test -t localhost/microvpn:test -f containers/Containerfile . >/dev/null
exec podman run --rm -t localhost/microvpn:test pytest "$@"
