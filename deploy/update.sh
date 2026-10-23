#!/bin/bash
# Pull the microvpn image and restart only when it is NEWER than what is running (revisions
# compared by git ancestry in ~/microvpn/repo), the way PyTools and the catalog update.
# Run by microvpn-update.timer once the image comes from Docker Hub.
set -euo pipefail
IMAGE=docker.io/raubcamaioni/microvpn:latest
LABEL="org.opencontainers.image.revision"
REPO=~/microvpn/repo
running=$(podman inspect --format "{{.Image}}" microvpn 2>/dev/null || true)
running_rev=$(podman inspect --format "{{index .Config.Labels \"$LABEL\"}}" microvpn 2>/dev/null || true)
podman pull -q "$IMAGE" >/dev/null
pulled=$(podman image inspect --format "{{.Id}}" "$IMAGE")
pulled_rev=$(podman image inspect --format "{{index .Config.Labels \"$LABEL\"}}" "$IMAGE")
[ "$pulled" = "$running" ] && exit 0
if [ -n "$pulled_rev" ] && [ -n "$running_rev" ]; then
  git -C "$REPO" fetch -q origin main 2>/dev/null || true
  if [ "$pulled_rev" = "$running_rev" ] || git -C "$REPO" merge-base --is-ancestor "$pulled_rev" "$running_rev" 2>/dev/null; then
    [ -n "$running" ] && podman tag "$running" "$IMAGE"
    exit 0
  fi
fi
echo "new image ${pulled:0:12} rev ${pulled_rev:0:7} (running ${running:0:12} rev ${running_rev:0:7}); restarting microvpn"
systemctl --user restart microvpn.service
podman image prune -f >/dev/null
