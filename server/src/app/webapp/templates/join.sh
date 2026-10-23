#!/bin/sh
# microvpn: join a room from a shell. The WireGuard key is generated here and never leaves this
# machine; only the public key is sent. Needs wg (wireguard-tools) and curl. Nothing runs as root.
#
#   usage: curl -sS {{ base|safe }}/join.sh | sh -s -- <device-name>
#
# Writes {{ tag|safe }}.conf (and keeps {{ tag|safe }}.key and {{ tag|safe }}.token beside it so that running
# the same command again refreshes the peer list under the same identity).
set -eu
BASE="{{ base|safe }}"
TAG="{{ tag|safe }}"
NAME=${1:-$(hostname 2>/dev/null || echo device)}
NAME=$(printf %s "$NAME" | tr -d '"\\')

for tool in wg curl; do
  command -v "$tool" >/dev/null 2>&1 || { echo "join.sh: $tool is required (install wireguard-tools and curl)" >&2; exit 1; }
done

umask 077
if [ ! -f "$TAG.key" ]; then
  wg genkey > "$TAG.key"
fi
PRIV=$(cat "$TAG.key")
PUB=$(printf %s "$PRIV" | wg pubkey)

if [ ! -f "$TAG.token" ]; then
  resp=$(curl -sS -f -X POST "$BASE/members" -H 'content-type: application/json' \
    --data "{\"name\":\"$NAME\",\"public_key\":\"$PUB\"}") || { echo "join.sh: joining failed: $resp" >&2; exit 1; }
  token=$(printf %s "$resp" | sed -n 's/.*"member_token":"\([^"]*\)".*/\1/p')
  [ -n "$token" ] || { echo "join.sh: unexpected answer from the server: $resp" >&2; exit 1; }
  printf %s "$token" > "$TAG.token"
fi
TOKEN=$(cat "$TAG.token")

curl -sS -f "$BASE/me.conf" -H "Authorization: Bearer $TOKEN" | sed "s|__PRIVATE_KEY__|$PRIV|" > "$TAG.conf"
chmod 600 "$TAG.conf"
grep -q '^\[Interface\]' "$TAG.conf" || { echo "join.sh: could not fetch the config (room ended? token stale? delete $TAG.token to rejoin)" >&2; rm -f "$TAG.conf"; exit 1; }
peers=$(grep -c '^\[Peer\]' "$TAG.conf" || true)
echo "wrote $TAG.conf ($peers peers). Bring it up with: sudo wg-quick up ./$TAG.conf"
echo "re-run this command after others join; the config lists only the peers present when it was written."
