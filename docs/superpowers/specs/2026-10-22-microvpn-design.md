# microvpn: design

Date: 2026-10-22
Status: for review

## Goal

A login-less site that creates temporary WireGuard "rooms". A visitor creates a room, shares its
link, and everyone who opens the link gets a WireGuard configuration for their device: a `.conf`
download or a QR code for the phone apps, or a one-line shell command for a headless machine.
Members then reach each other over a private virtual LAN. Nothing is routed to the internet.

The server facilitates every connection, so no member opens a port or touches a router, and yet
the server never holds a key and never sees a decrypted byte: it is a blind UDP forwarder between
members, and every tunnel is end-to-end between two members' own WireGuard instances.

Served at `https://raubcamaioni.com/microvpn/`. Repository `RaubCamaioni/microvpn`, site name
"microvpn". Built in the style of PyTools and the CadQuery Catalog: FastAPI, the monochrome
terminal theme, a version and commit bottom-right, one container image, released by tag.

## What the server knows, and what it does not

The server is never a WireGuard peer. It has no WireGuard interface and no WireGuard tooling.

- It never receives a private key. Keys are generated on the member's device: in the browser,
  or by `wg genkey` on a Linux box. Only the public key is posted.
- The configuration file, the QR code and the download are assembled on the member's device from
  the public parts the server returns (the other members' public keys, tunnel addresses, relay
  ports) plus the local private key.
- Relayed packets are WireGuard ciphertext between two members. The server forwards them
  unchanged and cannot decrypt them.
- What it does see: room names, device names, public keys, the public IP address and port each
  member sends from, packet sizes and timing. This is stated on the room page in one line.

The room creator is simply the first member and has one extra power, ending the room early.
Nobody, including the creator, can read traffic between two other members.

## Rooms

A room has:

- an **id**: 16 random bytes, lower-case base32 without padding (26 characters). The link
  `https://raubcamaioni.com/microvpn/r/<id>` is the only credential; anyone with it may join.
- an optional **name** (at most 40 characters, shown on the page);
- a **lifetime** chosen at creation from 1, 4, 12 or 24 hours (`ROOM_LIFETIMES`), giving
  `expires_at`;
- a **subnet**: a random `/24` inside `10.0.0.0/8`, unique among live rooms. Members get
  `.2`, `.3`, ... in it;
- an **owner token** (16 random bytes, url-safe base64) returned once at creation and kept in the
  creator's browser; it allows `DELETE /r/<id>`.

Limits, all environment variables with these defaults:

| Variable | Default | Meaning |
|---|---|---|
| `ROOM_MAX_MEMBERS` | 8 | members per room |
| `ROOM_MAX_LIVE` | 64 | live rooms at once; creation returns 429 beyond it |
| `ROOM_CREATE_PER_IP_PER_HOUR` | 3 | room creations per client IP in a sliding hour |
| `ROOM_MBPS` | 50 | relay bandwidth per room, shared by all of its pairs, both directions |
| `RELAY_TOTAL_MBPS` | 200 | relay bandwidth for the whole server |
| `ROOM_LIFETIMES` | `1,4,12,24` | lifetime choices, hours |
| `RELAY_PORT_RANGE` | `20000-21999` | UDP ports for pair relays |

The client IP is `X-Real-IP` from nginx (uvicorn runs with proxy headers trusted from the Docker
network only). The per-IP window is in memory; it resets on restart, which is acceptable.

When `expires_at` passes, or the owner ends the room, the relay ports close and every row of the
room is deleted. A 30-second sweeper does the expiry. Nothing about a room outlives it.

Room, member and pair rows live in SQLite (SQLModel) on `/data`, as in the other apps, so a
deploy restart does not kill live rooms: on startup the relay re-binds every pair port from the
database. Relay slots, rate buckets and the per-IP window are in memory only.

## Members and pairs

A **member** has a device name (at most 32 characters), a public key (44-character base64 of 32
bytes, validated), a tunnel address in the room's subnet, a **member token** (16 random bytes,
url-safe base64) returned once on joining, and `created_at`. Joining a full room returns 409;
a public key already in the room returns 409; an expired or unknown room returns 404.

A **pair** is an unordered pair of members of one room with one UDP relay port, allocated from
`RELAY_PORT_RANGE` at random among free ports when the second member of the pair joins. A room of
N members has N(N-1)/2 pairs: 28 for 8 members. Leaving (`DELETE /r/<id>/me`) deletes the member
and its pairs; the ports and the address return to the pool.

## The WireGuard configuration

Each member's configuration lists every other current member as a peer:

```ini
[Interface]
PrivateKey = <the member's own key, never sent to the server>
Address = 10.R1.R2.N/24

[Peer]
# alice-laptop
PublicKey = <alice's public key>
AllowedIPs = 10.R1.R2.M/32
Endpoint = raubcamaioni.com:<port of the pair (me, alice)>
PersistentKeepalive = 25
```

No `DNS`, no default route, no `ListenPort`. The keepalive keeps NAT mappings open and lets the
relay learn both ends of a pair before any real traffic. The endpoint host is `RELAY_HOST`.

The server renders this text with `PrivateKey = __PRIVATE_KEY__` as a placeholder
(`GET /r/<id>/me`); the browser and the shell script substitute the local key. Downloads are named
`mv<first 8 of the room id>.conf`: 10 characters, within the 15-character interface-name limit
of `wg-quick`, and acceptable to the Windows, macOS, Android and iOS apps.

Because the peer list is baked into the file, a member who joins later is unknown to the files
already imported. The page says "your config covers N peers" and marks it stale when the peer set
differs from what was last downloaded or copied (a hash of the peer set is kept in localStorage
at download time). A group that gathers over a few minutes imports once, when everyone is in.

## Joining from a browser

The room page keeps per-room state in `localStorage` under `microvpn:<room id>`: private key,
public key, member token, device name, and the peer-set hash of the last download.

1. Not yet joined: the page shows the room (name, time left, members N of max) and a join form
   with a device name. Submitting generates an X25519 keypair with a vendored `tweetnacl`
   (`nacl.box.keyPair()`, whose keys are what WireGuard uses), stores it, and posts name and
   public key. The response's member token is stored.
2. Joined: the page polls `GET /r/<id>/me` every 5 seconds and renders the configuration text
   with the private key substituted, a `[download]` (Blob), a `[copy]`, a QR code of the same
   text drawn in the browser with a vendored `qrcode-generator`, the CLI one-liner, and the
   peer list with relay status (below). `[leave]` deletes the membership and clears the stored
   state. The owner also sees `[end room]`.
3. Gone: an unknown or expired room renders a 404 page: "this room ended or never existed".

The front page (`/`) has the create form (name, lifetime), a short explanation of the flow, and
the "what the server sees" note. Creation is a `fetch` from the page's script, which stores the
owner token and navigates to the room.

The pages are server-rendered Jinja templates in the terminal theme of the landing page (a
single hand-written stylesheet, `ui-monospace`, black and white, bracketed actions), plus one
small vanilla JavaScript module per page. No htmx: the client must assemble the configuration
itself, so the page logic is JavaScript anyway.

## Joining from a shell

`GET /r/<id>/join.sh` returns a POSIX `sh` script with the room id and the public base URL baked
in. The page shows it as:

```sh
curl -sS https://raubcamaioni.com/microvpn/r/<id>/join.sh | sh -s -- <device-name>
```

The script requires `curl` and `wg`. It reuses `./mv<room8>.key` and `./mv<room8>.token` if
present, otherwise runs `wg genkey`, posts the public key with the device name, and keeps the
token. It then fetches `/r/<id>/me` with the token, substitutes the key, writes
`./mv<room8>.conf` with mode 600, and prints `sudo wg-quick up ./mv<room8>.conf`. It never runs
anything as root. Running it again refreshes the peer list under the same identity.

## The relay

One asyncio datagram endpoint per pair port, bound on `0.0.0.0` (IPv4 only in this version),
running in the same process and event loop as the web app, on one uvicorn worker with uvloop.

Each port holds two **slots**, each an address and a last-seen time. A packet from the address in
slot A is forwarded unchanged to the address in slot B, and vice versa.

A packet is accepted only if it looks like WireGuard: at least 32 bytes, type byte 1 to 4, the
three reserved bytes zero, and the length matching the type (148 for a handshake initiation, 92
for a response, 64 for a cookie reply, at least 32 for data).

A packet from an unknown address takes a slot when a slot is empty, or when the packet is a
handshake initiation or response, in which case it replaces the least recently active slot.
Anything else from an unknown address is dropped. This lets a client roam between networks (its
next handshake claims the slot) while junk cannot displace an active pair unless it imitates a
handshake, and if it does, the real client's next handshake takes the slot back within seconds.

Bandwidth: a token bucket per room (`ROOM_MBPS`, burst of one second) and one for the whole
server (`RELAY_TOTAL_MBPS`). Bytes forwarded on any port of a room draw from the room's bucket
and the global one; a packet that does not fit is dropped, which WireGuard and the applications
above it treat as ordinary loss.

Relay status: for the member viewing the page, each peer line shows how many of the pair's two
slots saw a packet in the last 30 seconds: `○` none, `◐` one side, `●` both. "Both" means the
relay is seeing traffic from both ends, which is the only feedback members get that their tunnel
reached the server. The relay cannot tell which slot is which member, so status is per pair.

Expiry: the sweeper closes the ports of expired rooms every 30 seconds. Startup re-binds all
pairs from the database before the web app accepts requests.

Capacity: at 50 Mbit/s a room moves about 4,500 packets per second at WireGuard's packet size.
A Python asyncio forwarder handles tens of thousands per second on one core, and the global cap
bounds the total. The server's 2 vCPUs are shared with the two other apps.

## API

All paths are under `ROOT_PATH` (`/microvpn`). JSON unless noted.

| Method and path | Auth | Result |
|---|---|---|
| `POST /rooms` `{name?, lifetime_hours}` | none | 201 `{id, url, owner_token, expires_at}`; 429 at the per-IP or global cap; 400 on a bad lifetime |
| `GET /r/{id}` | none | HTML room page; 404 if gone |
| `GET /r/{id}/join.sh` | none | the shell script, `text/x-shellscript` |
| `GET /r/{id}/state` | none | `{name, expires_at, max_members, members: [{name, address, public_key}]}` |
| `POST /r/{id}/members` `{name, public_key}` | none | 201 `{member_token, address}`; 409 full or duplicate key; 400 bad key or name; 404 gone |
| `GET /r/{id}/me` | `Authorization: Bearer <member token>` | `{address, config_template, peers_hash, peers: [{name, address, public_key, port, slots_active}]}` |
| `DELETE /r/{id}/me` | member token | 204, membership removed |
| `DELETE /r/{id}` | `Authorization: Bearer <owner token>` | 204, room ended |
| `GET /healthz` | none | `{rooms, members, ports_in_use, relay: "ok"}` |

Every response carries `Cache-Control: no-store`. User-supplied names are length-limited and
escaped in templates; nothing user-supplied is rendered as HTML.

## Data

```
Room:   id (pk), name, subnet (text, e.g. "10.37.201.0/24"), created_at, expires_at, owner_token
Member: id (pk), room_id (fk), name, public_key (unique per room), address, token, created_at
Pair:   id (pk), room_id (fk), member_a_id, member_b_id, port (unique)
```

`DATABASE` is the SQLite path (`/data/microvpn.db` in production).

## Deployment

Nothing runs on the host but podman. The app is one container under a new unprivileged user:

- **User** `microvpn`, created by `deploy/setup.sh` with linger enabled and a subordinate id
  range (verified in `/etc/subuid`, added if `useradd` did not).
- **Networking**: rootless podman with `--network=host`. This is required rather than preferred:
  rootless port forwarding rewrites every sender to one address (slirp4netns) or forwards a
  2000-port range slowly, and the relay must see members' real source addresses. The container
  binds HTTP on `HTTP_BIND=172.18.0.1:8082`, which only the Docker network may reach, and the
  UDP range on all addresses. It runs with no added capabilities, as a non-root user inside the
  container as well, capped at 256 MB and 256 pids.
- **Units** in `/home/microvpn/.config/systemd/user/`, in the shape of the catalog's:
  `microvpn.service` (the `podman run`), `microvpn-update.service` and `.timer` (pull `latest`
  from Docker Hub every minute, restart only on a newer `org.opencontainers.image.revision`).
  Until the Docker Hub secrets exist on the repository, the image is built on the server by
  `deploy/build-on-server.sh` (clone or pull the repository as `microvpn`, `podman build -t
  localhost/microvpn:latest`, restart the service) and the unit runs `localhost/microvpn:latest`;
  switching to Docker Hub is a one-line change of `IMAGE=` in the unit and enabling the timer.
- **Environment file** `/home/microvpn/microvpn/.env`: `DATABASE=/data/microvpn.db`,
  `ROOT_PATH=/microvpn`, `PUBLIC_URL=https://raubcamaioni.com/microvpn`,
  `RELAY_HOST=raubcamaioni.com`, `HTTP_BIND=172.18.0.1:8082`, `RELAY_PORT_RANGE=20000-21999`,
  `ROOM_MBPS=50`. Data directory `/home/microvpn/microvpn/data`, mounted at `/data`.
- **Firewall**: `ufw allow proto udp to any port 20000:21999` and
  `ufw allow from 172.18.0.0/16 to 172.18.0.1 port 8082 proto tcp`.
- **nginx** (repository `raubcamaioni.com`): `location = /microvpn { return 301 /microvpn/; }`
  and `location /microvpn/ { proxy_pass http://172.18.0.1:8082/; ... }` with the usual headers,
  `client_max_body_size 64k`, `proxy_read_timeout 60s`; a line on the landing page; applied with
  `apply.sh` as the catalog was.
- **Image**: `containers/Containerfile` from `python:3.12-slim`, no systemd, the app installed
  into a venv, `GIT_SHA` build argument written to `/etc/microvpn-commit`, started by
  `uvicorn` with `--loop uvloop --proxy-headers --forwarded-allow-ips 172.18.0.0/16`.
- **CI**: `.github/workflows/main.yml` in the shape of the catalog's: every push and pull request
  builds the image; a `v<semver>` tag matching `server/pyproject.toml`, or a manual run,
  publishes `raubcamaioni/microvpn` as `latest` and the version. Publishing steps are guarded
  on the Docker Hub secrets being present, so the workflow passes on builds until they are added.

## Repository layout

| Path | Contents |
|---|---|
| `server/` | the FastAPI application: `app.py`, `config.py`, `models.py`, `rooms.py` (room, member and pair rules), `relay.py`, `routes/`, `templates/`, `static/` (stylesheet, `room.js`, `index.js`, vendored `nacl-fast.min.js` and `qrcode.min.js`), `tests/` |
| `containers/Containerfile` | the image |
| `deploy/` | `setup.sh`, `build-on-server.sh`, `microvpn.service`, `microvpn-update.service`, `microvpn-update.timer`, `env.example` |
| `docs/superpowers/` | this spec and the implementation plan |
| `.github/workflows/` | build and publish |
| `README.md` | what it is, how it works, running it, releasing |

## Testing

- Unit tests (pytest, no network): room creation and limits (per IP, global, lifetimes), joining
  (full room, duplicate key, bad key, name limits), address and port allocation and release,
  pair creation on join and removal on leave, configuration rendering, expiry sweep, token
  checks on `me` and `DELETE`.
- Relay tests with real UDP sockets on localhost: forwarding in both directions, rejection of
  malformed packets, slot claim rules (empty slot, handshake takeover, junk dropped), roaming,
  per-room and global rate limits, slot activity reporting, port close on expiry.
- API tests with FastAPI's test client, including `join.sh` content and `ROOT_PATH` links.
- Acceptance on the real server: a room joined from the WireGuard app on this Windows machine and
  from one other device of the owner's, a ping across the tunnel, the status markers turning
  `●`, and a room expiring on schedule.

## Out of scope for the first version

A companion app and one-click open; direct peer-to-peer with hole punching (the relay built here
is the fallback that design needs); internet egress; IPv6 relay; accounts; proof-of-work or
captcha; rooms that outlive their timer; any chat or presence beyond the relay markers.

## Risks

- **Stale configurations.** Members who import before everyone has joined must re-import. The
  stale marker and the "N peers" line make this visible; it remains the main UX cost of using
  the official clients unchanged.
- **Lost browser state.** If localStorage is cleared, the member rejoins with a new key and the
  others must re-import to reach the new identity.
- **Slot displacement.** Junk imitating a handshake can knock a pair's slot out until the next
  real handshake (seconds). Ports are random within the range, and the room link is the only
  thing that reveals which ports matter.
- **Shared host.** The relay competes for 2 vCPUs with two sandboxed apps; the global cap keeps
  it from starving them, and `ROOM_MBPS` and `RELAY_TOTAL_MBPS` can be lowered live.
- **Metadata.** The server learns who talks to whom and when. This is stated on the page.
