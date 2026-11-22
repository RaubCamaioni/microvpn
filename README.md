# microvpn

Temporary WireGuard rooms. Create a room, share its link, and everyone who opens it gets a
WireGuard configuration for their device: a `.conf` download, a QR code for the phone apps, or an
Apple configuration profile. Members then reach each other over a private
virtual LAN. Nothing is routed to the internet, nobody opens a port, and the room ends on its own.
Someone signed in with GitHub, whose account is allowed to, can instead make a room with no
expiry (see "Accounts and permanent rooms").

The public instance runs at **https://raubcamaioni.com/microvpn/**.

The server facilitates every connection without being able to read any of it. It is never a
WireGuard peer and never holds a key: it is a blind UDP forwarder between members, and every
tunnel is end-to-end between two members' own WireGuard instances. The design and the reasons
behind it are in `docs/superpowers/specs/2026-10-22-microvpn-design.md`.

## How it works

1. **Create.** The front page makes a room with a random 26-character id and a lifetime of 1, 4,
   12 or 24 hours. The link `/r/<id>` is what you share. Whoever creates the room is its admin:
   every other device that asks to join waits until the admin's browser accepts it, and the admin
   sees each device's alias, IP address and approximate location (DB-IP's city database, bundled
   into the image and queried offline; [IP Geolocation by DB-IP](https://db-ip.com), CC BY 4.0).
   The admin can also reject, remove members and end the room.
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
   of the room is deleted. A room with no expiry ends only when its admin ends it.

What the server sees: room and device names, public keys, the public IP address and port each
member sends from, packet sizes and timing. This is stated on the room page.

## Accounts and permanent rooms

Signing in is optional and changes nothing about how a room works. It exists so that a room can
belong to a person instead of to one browser's cookie, and so that a room can be allowed to live
without an expiry.

- **Signing in** is GitHub's OAuth web flow with no scopes. The server keeps the account's numeric
  GitHub id and its login, nothing else, and does not keep GitHub's token. A signed-in browser holds
  a session cookie (30 days from its last use; the server stores only its hash). Unlike the
  anonymous browser cookie it is not tied to an address, so a phone stays signed in as it moves.
- **A room made while signed in belongs to the account.** Signed in on any browser, the account is
  the room's admin: it accepts devices, ends the room, and its own devices are accepted at once.
  The front page lists the account's rooms beside the browser's own. The account is that room's
  only admin: it has no owner token, and the browser it was made in keeps no claim on it, so
  signing out of a browser lets go of the room.
- **Only this site's own pages act for you.** Anything but a read is refused when the browser says
  the request comes from another site, or from another name under the same domain. Apps served from
  the same origin as this one are not kept apart from it by a browser; host it with apps you trust.
- **No expiry.** An account that may hold permanent rooms sees "no expiry" among the lifetimes.
  Such a room lives until its admin ends it, is never extended, and survives a restart of the
  server with its relay ports. The other rules are unchanged: 8 members, an accepted member is not
  removed one by one, and a room that has retired 28 relay ports is spent.
- **Who may hold one.** Today, admins: the accounts whose numeric GitHub ids are in
  `ADMIN_GITHUB_IDS`. The id is used because a login can be renamed and taken by someone else. The
  decision is one function, `RoomService.may_hold_permanent`; a paid plan is meant to be the other
  way in. An account taken off the list keeps the rooms it has and can end them, but makes no more.
- **Joining needs no account.** A device opens the link and waits for the admin, as before.

What the server sees of a tunnel is unchanged: it is still a blind forwarder and holds no key.

To turn the login on, register an OAuth app at GitHub (Settings, Developer settings, OAuth Apps)
with the callback `<PUBLIC_URL>/auth/github/callback` (production:
`https://raubcamaioni.com/microvpn/auth/github/callback`), and set `GITHUB_CLIENT_ID` and
`GITHUB_CLIENT_SECRET`. An account's numeric id is the `id` field of
`https://api.github.com/users/<login>`. Without the two settings the app has no login and
`/auth/*` answers 404.

## Limits

| Variable | Default | Meaning |
|---|---|---|
| `ROOM_MAX_MEMBERS` | 8 | members per room (28 relay ports when full) |
| `ROOM_MAX_LIVE` | 64 | live rooms at once |
| `ROOM_CREATE_PER_IP_PER_HOUR` | 3 | room creations per client address |
| `ROOM_JOIN_PER_IP_PER_HOUR` | 60 | devices joining, in any room, per client address |
| `ROOM_MBPS` | 50 | relay bandwidth per room, shared by all of its pairs |
| `RELAY_TOTAL_MBPS` | 200 | relay bandwidth for the whole server |
| `ROOM_LIFETIMES` | `1,4,12,24` | lifetime choices, hours |
| `RELAY_PORT_RANGE` | `20000-21999` | UDP ports for pair relays |
| `PERMANENT_ROOMS_PER_ACCOUNT` | 3 | rooms with no expiry one account may hold |
| `ADMIN_GITHUB_IDS` | none | numeric GitHub ids of the admins, comma-separated |
| `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` | none | the GitHub OAuth app; without both there is no login |

A port a pair gave up is not used again while its room lives, because the device that left still
points at it. A room that has given up 28 ports accepts no more devices, and no room holds more than
56 ports, in use and given up together: end it and make a new room (`server/src/app/rooms.py`).

Other settings: `DATABASE` (SQLite path, required), `ROOT_PATH` (URL prefix behind the proxy),
`PUBLIC_URL`, `RELAY_HOST` (the host in configs' `Endpoint` lines), `RELAY_BIND`, `HTTP_BIND`,
`SWEEP_SECONDS`, `FORWARDED_ALLOW_IPS`. All are read from the environment (`server/src/app/settings.py`).

## Repository layout

| Path | Contents |
|---|---|
| `server/` | The FastAPI application: settings, `rooms.py` (the rules), `accounts.py` and `github.py` (signing in), `relay.py` (the UDP forwarder), `wgconfig.py`, routes, templates, static assets, tests. |
| `containers/Containerfile` | One file, three stages: `base` (deps), `test` (dev deps, node, sources; runs pytest), `runtime` (the shipped image, non-root). |
| `deploy/` | Host setup for the production server: the rootless user, systemd user units, the server-side build, the Docker Hub update timer. |
| `docs/superpowers/` | The design specs and the implementation plans. |
| `.github/workflows/` | Tests and builds on every push; publishes (and so deploys) on a release tag. |

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
server.

A release deploys the way PyTools does: bump `version` in `server/pyproject.toml`, merge, tag
`vX.Y.Z` and push the tag. The workflow refuses a tag that does not match the version, publishes
`raubcamaioni/microvpn` to Docker Hub as `latest` and the version (with the `DOCKER_USERNAME` and
`DOCKER_TOKEN` repository secrets), and the server's `microvpn-update.timer` pulls it and restarts
within a minute when it carries a newer commit. A merge to `main` alone deploys nothing.

`deploy/build-on-server.sh` builds and deploys any other ref by hand, under the same image tag;
the timer leaves it running until a release has a commit it does not contain:

```bash
ssh root@<host> 'sudo -iu microvpn /home/microvpn/microvpn/build-on-server.sh <ref>'
```

## Out of scope, for now

A companion app for one-click open, direct peer-to-peer with hole punching (the relay here is the
fallback that design needs), internet egress, IPv6, a paid plan for permanent rooms, proof-of-work.
