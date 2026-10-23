#!/bin/bash
# One-time setup of microvpn on the raubcamaioni.com host. Run as root: bash setup.sh
# Creates the rootless `microvpn` user, clones the repository, installs the units and scripts from
# the clone's deploy/ directory, opens the firewall, builds the image on the server and starts it.
# Idempotent: safe to run again. Nothing runs on the host outside podman.
set -euo pipefail
user=microvpn
repo_url=https://github.com/RaubCamaioni/microvpn.git

echo "== user $user (rootless podman, lingering)"
id "$user" >/dev/null 2>&1 || useradd -m -s /bin/bash "$user"
grep -q "^$user:" /etc/subuid || usermod --add-subuids 200000-265535 "$user"
grep -q "^$user:" /etc/subgid || usermod --add-subgids 200000-265535 "$user"
loginctl enable-linger "$user"
uid=$(id -u "$user")
systemctl start "user@$uid.service"
as_user() {
  sudo -u "$user" XDG_RUNTIME_DIR=/run/user/$uid DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$uid/bus \
    HOME=/home/$user bash -c "cd /home/$user && $1"
}

echo "== directories, repository clone, environment file"
as_user "mkdir -p microvpn/data .config/systemd/user"
as_user "[ -d microvpn/repo/.git ] || git clone -q $repo_url microvpn/repo"
as_user "git -C microvpn/repo pull -q --ff-only origin main 2>/dev/null || true"
as_user "[ -f microvpn/.env ] || cp microvpn/repo/deploy/env.example microvpn/.env"
as_user "chmod 600 microvpn/.env"
src=/home/$user/microvpn/repo/deploy
install -m 755 -o "$user" -g "$user" "$src/build-on-server.sh" "$src/update.sh" "/home/$user/microvpn/"
install -m 644 -o "$user" -g "$user" "$src/microvpn.service" "$src/microvpn-update.service" "$src/microvpn-update.timer" \
  "/home/$user/.config/systemd/user/"
# /data belongs to uid 1000 inside the container, which is a subordinate uid on the host
as_user "podman unshare chown -R 1000:1000 microvpn/data"
as_user "systemctl --user daemon-reload"

echo "== firewall"
ufw allow proto udp to any port 20000:21999 comment "microvpn relay" >/dev/null
ufw allow from 172.18.0.0/16 to 172.18.0.1 port 8082 proto tcp comment "nginx -> microvpn" >/dev/null

echo "== first build and start"
as_user "systemctl --user enable microvpn.service >/dev/null 2>&1"
as_user "./microvpn/build-on-server.sh main"
echo "setup done. The update timer stays disabled until the image comes from Docker Hub:"
echo "  sudo -iu $user systemctl --user enable --now microvpn-update.timer"
