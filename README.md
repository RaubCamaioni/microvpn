# microvpn

Temporary WireGuard rooms. Create a room, share its link, and everyone who opens it gets a
WireGuard configuration for their device: a `.conf` download, a QR code for the phone apps, or a
one-line shell command for a headless machine. Members then reach each other over a private
virtual LAN. Nothing is routed to the internet, nobody opens a port, and the room ends on its own.

The public instance runs at **https://raubcamaioni.com/microvpn/**.

The server facilitates every connection without being able to read any of it. It is never a
WireGuard peer and never holds a key: it is a blind UDP forwarder between members, and every
tunnel is end-to-end between two members' own WireGuard instances. The design and the reasons
behind it are in `docs/superpowers/specs/2026-10-22-microvpn-design.md`.

## How it works

1. **Create.** The front page makes a room with a random 26-character id and a lifetime of 1, 4,
   12 or 24 hours. The link `/r/<id>` is the only credential; anyone who has it can join.
2. **Join.** Each device sends only a public key. The browser makes the key with a vendored
   [tweetnacl](https://tweetnacl.js.org/) (`nacl.box.keyPair()`, the X25519 keys WireGuard uses) and
   keeps the private key in `localStorage`; or, for a machine without a browser, make the key there
   with `wg genkey | tee private.key | wg pubkey` and paste the public key into the join form.
3. **Configure.** The server renders the member's config with every other member as a peer and
   `PrivateKey = __PRIVATE_KEY__`; the browser substitutes the local key (a member who brought their
   own key fills it in). The page shows the text, a download, a QR code for the phone apps, a QR
   code of the room link itself for inviting another device, and on iOS and macOS an
   `[add to WireGuard]` action that saves an Apple configuration profile (built in the browser, in
   the format the WireGuard project documents). Because the peer list is baked into the file, the
   page marks a downloaded config stale when members change.
4. **Relay.** Every pair of members gets its own UDP port on the server. Both send their WireGuard
   packets to it, and the server forwards what one side sends to the other side's last-seen
   address. WireGuard's handshake runs between the two members through that port, so the server
   carries only ciphertext. Per pair ports are the reason: WireGuard packets do not say who sent
   them, so the port is the only thing that can mean "the alice-bob conversation".
5. **End.** When the lifetime passes, or the creator ends the room, the ports close and every row
   of the room is deleted.

What the server sees: room and device names, public keys, the public IP address and port each
member sends from, packet sizes and timing. This is stated on the room page.

## Limits

| Variable | Default | Meaning |
|---|---|---|
| `ROOM_MAX_MEMBERS` | 8 | members per room (28 relay ports when full) |
| `ROOM_MAX_LIVE` | 64 | live rooms at once |
| `ROOM_CREATE_PER_IP_PER_HOUR` | 3 | room creations per client address |
| `ROOM_MBPS` | 50 | relay bandwidth per room, shared by all of its pairs |
| `RELAY_TOTAL_MBPS` | 200 | relay bandwidth for the whole server |
| `ROOM_LIFETIMES` | `1,4,12,24` | lifetime choices, hours |
| `RELAY_PORT_RANGE` | `20000-21999` | UDP ports for pair relays |

Other settings: `DATABASE` (SQLite path, required), `ROOT_PATH` (URL prefix behind the proxy),
`PUBLIC_URL`, `RELAY_HOST` (the host in configs' `Endpoint` lines), `RELAY_BIND`, `HTTP_BIND`,
`SWEEP_SECONDS`, `FORWARDED_ALLOW_IPS`. All are read from the environment (`server/src/app/settings.py`).

## Repository layout

| Path | Contents |
|---|---|
| `server/` | The FastAPI application: settings, `rooms.py` (the rules), `relay.py` (the UDP forwarder), `wgconfig.py`, routes, templates, static assets, tests. |
| `containers/Containerfile` | One file, three stages: `base` (deps), `test` (dev deps, node, sources; runs pytest), `runtime` (the shipped image, non-root). |
| `deploy/` | Host setup for the production server: the rootless user, systemd user units, the server-side build, the Docker Hub update timer. |
| `docs/superpowers/` | The design spec and the implementation plan. |
| `.github/workflows/` | Tests and builds on every push; publishes on a release tag. |

## Running it

```bash
podman run --rm --name microvpn \
  -p 172.18.0.1:8082:8080/tcp -p 20000-21999:20000-21999/udp \
  --env-file .env -v ./data:/data \
  docker.io/raubcamaioni/microvpn:latest
```

The container runs as a non-root user with no capabilities and no host networking. Rootless
podman with pasta (podman 5) preserves members' real source addresses on the published UDP
range, which the relay needs to tell the two sides of a pair apart. `.env` holds the variables
above; `deploy/env.example` is the production one.

## Development

Nothing runs on the host. Tests run inside the image's `test` stage and the app inside `runtime`:

```bash
./test.sh                      # the whole suite; arguments go to pytest
./test.sh tests/test_relay.py -v
./build.sh                     # localhost/microvpn:dev
```

Both scripts use podman, and on Windows re-run themselves inside the WSL `Ubuntu` distro. The
relay tests bind real UDP sockets on localhost. `.devcontainer/` opens the repository in the
`test` stage for VS Code.

## Deployment and releasing

Production is one rootless podman container under the `microvpn` user on the raubcamaioni.com
host, behind the site's nginx at `/microvpn/`. `deploy/setup.sh` (run once, as root) creates the
user, installs the units and firewall rules, clones this repository and builds the image on the
server; `deploy/build-on-server.sh` rebuilds and restarts from any ref:

```bash
ssh root@<host> 'sudo -iu microvpn /home/microvpn/microvpn/build-on-server.sh main'
```

Once the `DOCKER_USERNAME` and `DOCKER_TOKEN` secrets exist on the repository, a release deploys
the way PyTools and the CadQuery Catalog do: bump `version` in `server/pyproject.toml`, merge, tag
`vX.Y.Z` and push the tag. The workflow refuses a tag that does not match the version, publishes
`raubcamaioni/microvpn` as `latest` and the version, and the server's `microvpn-update.timer`
pulls and restarts within a minute. Switching the server over is a one-line change of `IMAGE=` in
`microvpn.service` and `systemctl --user enable --now microvpn-update.timer`.

## Out of scope, for now

A companion app for one-click open, direct peer-to-peer with hole punching (the relay here is the
fallback that design needs), internet egress, IPv6, accounts, proof-of-work.
