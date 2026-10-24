#!/bin/bash
# Build the runtime image from the repository clone and restart the service. Runs as the
# `microvpn` user on the host:   ~/microvpn/build-on-server.sh [ref]      (default: main)
# Every push to main deploys itself (Docker Hub and update.sh); this deploys any ref by hand, e.g. a
# branch to try out. The build takes the Hub image's tag, so microvpn.service runs it and update.sh
# keeps it until Hub has a commit that is not already in it.
set -euo pipefail
ref=${1:-main}
repo=~/microvpn/repo
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
export DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}

git -C "$repo" fetch -q origin
git -C "$repo" checkout -q "$ref"
git -C "$repo" pull -q --ff-only origin "$ref" 2>/dev/null || true  # a branch moves on; a tag does not
sha=$(git -C "$repo" rev-parse HEAD)
image=docker.io/raubcamaioni/microvpn:latest
echo "== building $ref ($sha) as $image"
# the revision label is what update.sh compares; CI's images carry it too
podman build -q --target runtime -f "$repo/containers/Containerfile" --build-arg "GIT_SHA=$sha" \
  --label "org.opencontainers.image.revision=$sha" -t "$image" "$repo" >/dev/null
echo "== restarting microvpn.service"
systemctl --user restart microvpn.service
for i in $(seq 1 30); do
  if out=$(curl -sf http://172.18.0.1:8082/healthz); then
    echo "up after $((i * 2))s: $out"
    podman image prune -f >/dev/null
    exit 0
  fi
  sleep 2
done
echo "microvpn did not answer on 172.18.0.1:8082 within 60s" >&2
systemctl --user --no-pager status microvpn.service 2>&1 | tail -15 >&2
exit 1
