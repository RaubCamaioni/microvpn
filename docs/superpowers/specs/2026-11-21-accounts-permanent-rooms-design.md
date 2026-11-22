# Accounts and permanent rooms

Approved by the owner on 2026-11-21. Project 1 of 2; the paid plan (Stripe, $1 a month) is project 2
and plugs into the one check named below.

## Why

Rooms end after 24 hours at most, and a room's admin is a browser cookie tied to one address. The
owner wants a standing end-to-end tunnel between a phone and a home PC (to view sites a Claude
session on the PC serves), which needs a room that does not end and an owner that outlasts a cookie
and follows the person across devices and networks. Strangers must not be able to hold relay ports
forever for free, so permanent rooms are for entitled accounts only: admins now, subscribers later.

## Goals

- Sign in with GitHub. Anonymous use stays exactly as it is.
- An entitled account can create a room with no expiry and manage it from any browser it signs in on.
- Admin accounts are entitled. They are named in the server's environment.
- One function decides entitlement, so project 2 changes only that function.

## Non-goals

- Payments, plans, a subscription page (project 2).
- Requiring an account to join a room: a device still opens the link and waits for the owner.
- Kicking an accepted member, or replacing one device, in a permanent room. The existing rule stands
  (end the room and make a new one), and so does the limit of 28 retired relay ports per room.
- An account page, deleting an account, other login providers.

## Anti-goals

- The server learning anything new about tunnels. It stays a blind relay and holds no private key.
- An anonymous visitor reaching a permanent room's admin actions, or creating a permanent room.

## Approaches

1. **Accounts with GitHub login; permanence is a property of the room, gated by the account**
   (chosen). The owner asked for a GitHub login and a paid gate, and both need an identity that is
   not a cookie.
2. A secret "admin code" in the environment that unlocks permanent rooms from any browser. Smaller,
   but it gives nothing to build the paid plan on and the room would still be tied to a cookie.
3. Leave the app alone and have a script on the PC extend a 24-hour room every day. No app change,
   but the room dies whenever the PC is off for a day, and it is no product.

## Design

**Accounts.** Table `account`: id, `github_id` (GitHub's numeric id, unique), `login`, `created_at`,
`last_login`. The role is not stored: an account is an admin when its `github_id` is in
`ADMIN_GITHUB_IDS`. The numeric id is used because a GitHub username can be renamed and re-registered
by someone else.

**Login.** GitHub's OAuth web flow, no scopes: `GET /auth/github` sets a short-lived `state` cookie and
redirects to GitHub; `GET /auth/github/callback` checks the state, exchanges the code, reads
`https://api.github.com/user` for `id` and `login`, upserts the account and starts a session;
`POST /auth/logout` ends it. GitHub's token is used once and never stored. With `GITHUB_CLIENT_ID`
unset the routes answer 404 and the pages show no login: the app runs as today.

**Sessions.** Table `accountsession`: sha256 of a random token, account id, created, last seen. Cookie
`microvpn_session`: HttpOnly, SameSite=Lax, Secure on https, 30 days, scoped to `ROOT_PATH`. Unlike
the visitor cookie it is not tied to an address, since a phone changes networks. Sessions unused for
30 days are swept.

**Entitlement.** `RoomService.may_hold_permanent(account) -> bool`: true for an admin. Project 2 adds
"or has a paid plan".

**Permanent rooms.** `room` gains `account_id` (nullable) and `permanent` (bool). A permanent room's
`expires_at` is 9999-12-31 UTC, so every existing "still live" query is unchanged. Creating one:
`POST /rooms` with `permanent: true` in place of a lifetime; refused with 401 when signed out, 403
when not entitled, 409 over `PERMANENT_ROOMS_PER_ACCOUNT` (default 3). Any room created while signed
in records `account_id`. `extend` on a permanent room is refused; ending it works as for any room.
Permanent rooms count toward `ROOM_MAX_LIVE` and keep the per-room member and port limits.

**Admin of a room.** As today (owner token, or the creating browser's cookie), or the signed-in
account that owns the room. A join from the owning account is accepted at once, like the creator's
own device today.

**Pages.** The top bar shows `[sign in with GitHub]`, or the login and `[sign out]`. The front page
lists the account's rooms beside the browser's own, and offers "no expiry" in the lifetime choices
only to an entitled account. A permanent room shows "no expiry" where others show the time left and
has no extend control.

**Settings.** `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `ADMIN_GITHUB_IDS` (comma-separated numbers),
`PERMANENT_ROOMS_PER_ACCOUNT`. `deploy/env.example` and the README gain them.

**From the owner, once.** Register an OAuth app at GitHub (Settings, Developer settings, OAuth Apps)
with callback `https://raubcamaioni.com/microvpn/auth/github/callback`, and put its id and secret,
and the owner's numeric GitHub id, in `/home/microvpn/microvpn/.env` on the server.

## Left to the builder

Template and CSS details in the app's existing style, test layout, the HTTP client for the two
GitHub calls (tests substitute it), and names not fixed above.
