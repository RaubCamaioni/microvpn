# Accounts and Permanent Rooms Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sign in with GitHub, and let an entitled account (an admin, for now) hold rooms that never expire.

**Architecture:** A new `AccountService` owns accounts and sessions beside the existing `RoomService`. A room gains an owning account and a `permanent` flag; a permanent room's `expires_at` is a far-future constant so every "still live" query is untouched. One function, `RoomService.may_hold_permanent`, decides who may create one.

**Tech Stack:** FastAPI, SQLModel on SQLite, Jinja templates, plain JS, httpx for the two GitHub calls, pytest inside the image's `test` stage (`./test.sh`).

**Spec:** `docs/superpowers/specs/2026-11-21-accounts-permanent-rooms-design.md`

## Global Constraints

- Anonymous use is unchanged: every existing test passes unmodified, except where a response gains a field.
- With `GITHUB_CLIENT_ID` unset: `/auth/*` answers 404 and no page shows a login.
- The server stores no GitHub token and no private key.
- Admins are matched by GitHub's numeric id (`ADMIN_GITHUB_IDS`), never by login.
- Session cookie `microvpn_session`: HttpOnly, SameSite=Lax, Secure when `PUBLIC_URL` is https, Max-Age 30 days, Path `ROOT_PATH or "/"`, not tied to an address.
- A permanent room's `expires_at` is `9999-12-31T00:00:00+00:00`.
- `PERMANENT_ROOMS_PER_ACCOUNT` default 3.
- Datetimes are timezone-aware UTC (`app.models.utcnow`).
- Tests run with `./test.sh` (podman; on Windows it re-runs inside WSL). Nothing runs on the host.
- Commit after each task; end commit messages with `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>`.

## Review Focus

1. The person cancels at GitHub, or the callback's `state` is missing or wrong: they land on the front page signed out, never a 500. (Task 3)
2. GitHub is unreachable or rejects the code: a plain 502 answer, no session, no account row. (Task 3)
3. A signed-in phone changes network: the session still works from a new address. (Task 2)
4. The app is deployed over an existing database: the new columns appear and old rooms are not permanent. (Task 1)
5. A permanent room across a restart and many sweeps: it and its relay ports survive. (Task 4)

Also pinned: an account dropped from `ADMIN_GITHUB_IDS` keeps the permanent rooms it has and can still end them, but cannot make another (Task 4).

## Files

| File | Change |
|---|---|
| `server/src/app/settings.py` | four settings, `github_enabled` |
| `server/src/app/models.py` | `Account`, `AccountSession`, two `Room` columns, `PERMANENT_EXPIRES` |
| `server/src/app/accounts.py` | new: `AccountService`, `current_account` |
| `server/src/app/github.py` | new: `authorize_url`, `HttpGitHub`, `GitHubError` |
| `server/src/app/routes/auth.py` | new: the three auth routes |
| `server/src/app/rooms.py` | permanence, entitlement, admin by account |
| `server/src/app/routes/api.py`, `pages.py`, `app.py`, `templating.py` | wiring |
| `server/src/app/webapp/templates/*.html`, `static/index.js`, `static/room.js` | login link, "no expiry" |
| `server/pyproject.toml` | `httpx>=0.27` as a runtime dependency |
| `README.md`, `deploy/env.example` | the new settings and the one-time GitHub setup |

---

### Task 1: Settings and tables

**Files:** Modify `server/src/app/settings.py`, `server/src/app/models.py`; Test `server/tests/test_settings.py`, `server/tests/test_models.py`

**Interfaces — Produces:**
- `Settings.GITHUB_CLIENT_ID: str = ""`, `GITHUB_CLIENT_SECRET: str = ""`, `ADMIN_GITHUB_IDS: frozenset[int] = frozenset()`, `PERMANENT_ROOMS_PER_ACCOUNT: int = 3`, property `github_enabled -> bool` (both id and secret set).
- `models.PERMANENT_EXPIRES = datetime(9999, 12, 31, tzinfo=timezone.utc)`
- `models.Account(id: int | None pk, github_id: int unique+index, login: str, created_at, last_login)`
- `models.AccountSession(id: str pk  # sha256 of the cookie token, account_id: int index, created_at, last_seen)`
- `Room.account_id: int | None = None (index)`, `Room.permanent: bool = False`; both in `_ADDED_COLUMNS["room"]` (`INTEGER`, `BOOLEAN NOT NULL DEFAULT 0`).

- [ ] **Step 1: Failing tests**

```python
# test_settings.py
def test_account_settings():
    s = make_settings(GITHUB_CLIENT_ID="id", GITHUB_CLIENT_SECRET="sec", ADMIN_GITHUB_IDS="12, 34", PERMANENT_ROOMS_PER_ACCOUNT="5")
    assert s.github_enabled and s.ADMIN_GITHUB_IDS == frozenset({12, 34}) and s.PERMANENT_ROOMS_PER_ACCOUNT == 5
    d = make_settings()
    assert not d.github_enabled and d.ADMIN_GITHUB_IDS == frozenset() and d.PERMANENT_ROOMS_PER_ACCOUNT == 3
    assert not make_settings(GITHUB_CLIENT_ID="id").github_enabled
    with pytest.raises(ValueError, match="ADMIN_GITHUB_IDS"):
        make_settings(ADMIN_GITHUB_IDS="raub")

# test_models.py
def test_old_database_gains_account_columns(tmp_path):
    # a room table as the previous release made it, with one row
    engine = make_engine(str(tmp_path / "old.db"))
    with engine.begin() as c:
        c.execute(text("CREATE TABLE room (id VARCHAR PRIMARY KEY, name VARCHAR, subnet VARCHAR, created_at DATETIME,"
                       " expires_at DATETIME, owner_token VARCHAR, visitor_id VARCHAR NOT NULL DEFAULT '',"
                       " lifetime_hours INTEGER NOT NULL DEFAULT 1)"))
        c.execute(text("INSERT INTO room (id, name, subnet, created_at, expires_at, owner_token)"
                       " VALUES ('r', '', '10.1.1.0/24', '2026-01-01 00:00:00+00:00', '2026-01-01 01:00:00+00:00', 't')"))
    create_db_and_tables(engine)
    with Session(engine) as s:
        room = s.get(Room, "r")
        assert room.permanent is False and room.account_id is None
        s.add(Account(github_id=1, login="a", created_at=utcnow(), last_login=utcnow())); s.commit()

def test_permanent_expiry_round_trips(tmp_path):
    # a Room saved with expires_at=PERMANENT_EXPIRES reads back equal and compares > utcnow()
```

- [ ] **Step 2:** `./test.sh tests/test_settings.py tests/test_models.py -q` fails on the new names.
- [ ] **Step 3:** Implement. `ADMIN_GITHUB_IDS` parses a comma-separated list of positive integers; blank items are skipped.
- [ ] **Step 4:** Same command passes; `./test.sh -q` whole suite passes.
- [ ] **Step 5:** Commit `Settings and tables for accounts and permanent rooms`.

### Task 2: Accounts and sessions

**Files:** Create `server/src/app/accounts.py`; Test `server/tests/test_accounts.py`

**Interfaces — Consumes:** Task 1's models and settings. `app.keys.new_token()`.
**Produces:**
```python
SESSION_COOKIE = "microvpn_session"
SESSION_DAYS = 30
class AccountService:
    def __init__(self, engine: Engine, settings: Settings, now: Callable[[], datetime] = utcnow): ...
    def sign_in(self, github_id: int, login: str) -> tuple[Account, str]   # upsert by github_id, update login and last_login; new session; returns the cookie token
    def account_of(self, token: str | None) -> Account | None              # None for unknown or a session unused for SESSION_DAYS; touches last_seen at most hourly
    def sign_out(self, token: str | None) -> None                          # deletes that session only
    def is_admin(self, account: Account | None) -> bool                    # github_id in settings.ADMIN_GITHUB_IDS
    def sweep(self) -> int                                                 # deletes sessions unused for SESSION_DAYS; returns how many
def current_account(request) -> Account | None   # reads the cookie, asks request.app.state.accounts, caches on request.state.account
```

- [ ] **Step 1: Failing tests** (`now` is a mutable clock, as `test_rooms.py` does it)

```python
def test_sign_in_upserts_by_github_id(svc):
    a, t1 = svc.sign_in(7, "old"); b, t2 = svc.sign_in(7, "renamed")
    assert a.id == b.id and b.login == "renamed" and t1 != t2
    assert svc.account_of(t1).id == a.id and svc.account_of(t2).login == "renamed"   # two devices, both signed in
def test_unknown_and_missing_tokens(svc):
    assert svc.account_of(None) is None and svc.account_of("") is None and svc.account_of("nope") is None
def test_token_is_stored_hashed(svc, engine):
    _, t = svc.sign_in(7, "a")
    assert all(row.id != t for row in rows(engine, AccountSession))
def test_sign_out_ends_one_session(svc):
    _, t1 = svc.sign_in(7, "a"); _, t2 = svc.sign_in(7, "a"); svc.sign_out(t1)
    assert svc.account_of(t1) is None and svc.account_of(t2) is not None
def test_session_expires_after_30_idle_days_and_is_swept(svc, clock):
    _, t = svc.sign_in(7, "a"); clock.advance(days=29); assert svc.account_of(t)      # use renews it
    clock.advance(days=29); assert svc.account_of(t); clock.advance(days=31)
    assert svc.account_of(t) is None and svc.sweep() == 1
def test_admin_is_by_numeric_id(tmp_path):
    svc = service(ADMIN_GITHUB_IDS="7"); a, _ = svc.sign_in(7, "x"); b, _ = svc.sign_in(8, "x")
    assert svc.is_admin(a) and not svc.is_admin(b) and not svc.is_admin(None)
```

- [ ] **Step 2–4:** Fail, implement, pass (`./test.sh tests/test_accounts.py -q`).
- [ ] **Step 5:** Commit `Accounts and sessions`.

### Task 3: GitHub login routes

**Files:** Create `server/src/app/github.py`, `server/src/app/routes/auth.py`; Modify `server/src/app/app.py`, `server/pyproject.toml` (move `httpx>=0.27` to `dependencies`); Test `server/tests/test_auth.py`, `server/tests/conftest.py`

**Interfaces — Consumes:** Task 2. **Produces:**
```python
# github.py
class GitHubError(Exception): ...
def authorize_url(settings: Settings, state: str) -> str      # https://github.com/login/oauth/authorize?client_id=..&state=..&redirect_uri={PUBLIC_URL}/auth/github/callback
class HttpGitHub:
    def __init__(self, settings: Settings): ...
    async def identity(self, code: str) -> tuple[int, str]    # POST https://github.com/login/oauth/access_token (Accept: application/json), then GET https://api.github.com/user; (id, login); GitHubError on any failure; 10 s timeout
# app.py
def create_app(settings, relay=None, locate=None, github=None) -> FastAPI   # app.state.accounts, app.state.github (HttpGitHub by default)
```
Routes (router included only when `settings.github_enabled`; otherwise the paths 404):
- `GET /auth/github`: sets cookie `microvpn_oauth_state` (random, HttpOnly, SameSite=Lax, Max-Age 600, same Path and Secure rules) and 302s to `authorize_url`.
- `GET /auth/github/callback?code=&state=`: when `state` is missing or differs from the cookie (`hmac.compare_digest`), or `code` is missing (GitHub sends `error=access_denied` on cancel): 302 to `{ROOT_PATH}/`, no session. On `GitHubError`: 502, plain text `GitHub sign-in failed; try again`. Otherwise `sign_in`, set `microvpn_session`, clear the state cookie, 302 to `{ROOT_PATH}/`. No `next` parameter exists: the redirect target is fixed.
- `POST /auth/logout`: `sign_out`, clear the cookie, 204.
- The sweeper in `app.py` also calls `accounts.sweep`.

`conftest.py` gains `FakeGitHub` (`identities: dict[code, (id, login)]`; an unknown code raises `GitHubError`) and fixtures `auth_settings` (client id/secret set, `ADMIN_GITHUB_IDS="7"`), `auth_app`, `auth_client`, and a helper `sign_in(client, github_id, login)` that walks both routes and returns nothing (the client keeps the cookie).

- [ ] **Step 1: Failing tests**

```python
def test_login_disabled_without_client_id(client):
    assert client.get("/auth/github", follow_redirects=False).status_code == 404
    assert client.get("/auth/github/callback?code=c&state=s", follow_redirects=False).status_code == 404
def test_login_round_trip(auth_client, auth_app):
    r = auth_client.get("/auth/github", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("https://github.com/login/oauth/authorize?")
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    auth_app.state.github.identities["good"] = (7, "raub")
    r = auth_client.get(f"/auth/github/callback?code=good&state={state}", follow_redirects=False)
    assert r.status_code == 302 and "microvpn_session=" in r.headers["set-cookie"] and "HttpOnly" in r.headers["set-cookie"]
    assert auth_client.post("/auth/logout").status_code == 204
def test_cancel_and_bad_state_land_signed_out(auth_client):
    auth_client.get("/auth/github", follow_redirects=False)
    for q in ("error=access_denied&state=x", "code=good&state=wrong", "code=good"):
        r = auth_client.get("/auth/github/callback?" + q, follow_redirects=False)
        assert r.status_code == 302 and "microvpn_session" not in r.headers.get("set-cookie", "")
def test_github_failure_is_502_and_creates_nothing(auth_client, auth_app):
    # valid state, a code FakeGitHub does not know -> 502, no Account rows
def test_session_survives_a_change_of_address(auth_client, auth_app):
    sign_in(auth_client, 7, "raub")
    for ip in ("1.1.1.1", "2.2.2.2"):
        assert "raub" in auth_client.get("/", headers={"X-Real-IP": ip}).text      # passes after Task 5 adds the login to the top bar; until then assert via current_account in a tiny test route
```
(Implement the last test against `app.state.accounts.account_of` with the client's cookie now; Task 5 adds the page assertion.)

- [ ] **Step 2–4:** Fail, implement, pass (`./test.sh tests/test_auth.py -q`, then the whole suite).
- [ ] **Step 5:** Commit `Sign in with GitHub`.

### Task 4: Permanent rooms in the service

**Files:** Modify `server/src/app/rooms.py`; Test `server/tests/test_rooms.py`

**Interfaces — Consumes:** `Account`, `PERMANENT_EXPIRES`, settings. **Produces:**
```python
class SignInRequired(RoomError): status, detail = 401, "sign in to make a room with no expiry"
class NotEntitled(RoomError):    status, detail = 403, "this account may not hold rooms with no expiry"
class TooManyPermanent(RoomError): status, detail = 409, "this account has reached its limit of rooms with no expiry"
RoomService.may_hold_permanent(self, account: Account | None) -> bool      # account is not None and account.github_id in settings.ADMIN_GITHUB_IDS
RoomService.create_room(self, name, lifetime_hours: int | None, client_ip, visitor_id="", visitor_token=None, account: Account | None = None, permanent: bool = False) -> Room
# every admin method gains a trailing keyword `account_id: int | None = None`:
#   is_admin, extend, end_room, admin_view, approve, remove_member, join, _admin_room
RoomService.rooms_of_visitor(self, visitor_id: str, account_id: int | None = None) -> list[MyRoom]
MyRoom.permanent: bool = False;  RoomView.permanent: bool = False
```
Rules:
- `permanent=True`: `SignInRequired` without an account, `NotEntitled` when `may_hold_permanent` is false, `TooManyPermanent` when the account already has `PERMANENT_ROOMS_PER_ACCOUNT` permanent rooms; `lifetime_hours` is ignored and stored as 0; `expires_at = PERMANENT_EXPIRES`. Otherwise `lifetime_hours` is required and validated as today.
- Any room created with an account records `account_id`.
- `_admin_room` also passes when `account_id` is not None and equals `room.account_id`. `join` from the owning account is accepted at once.
- `extend` on a permanent room raises `Invalid("a room with no expiry is not extended")`.
- `rooms_of_visitor` also lists rooms whose `account_id` matches, `is_admin=True` for those.

- [ ] **Step 1: Failing tests** (helpers `admin = Account(id=1, github_id=7, ...)`, `user = Account(id=2, github_id=8, ...)`, service built with `ADMIN_GITHUB_IDS="7"`)

```python
def test_permanent_needs_an_entitled_account(svc):
    with pytest.raises(SignInRequired): svc.create_room("", None, "1.1.1.1", permanent=True)
    with pytest.raises(NotEntitled):   svc.create_room("", None, "1.1.1.1", account=user, permanent=True)
    room = svc.create_room("lab", None, "1.1.1.1", account=admin, permanent=True)
    assert room.permanent and room.account_id == 1 and room.expires_at == PERMANENT_EXPIRES
def test_permanent_room_limit_per_account(svc):          # PERMANENT_ROOMS_PER_ACCOUNT="2", ROOM_CREATE_PER_IP_PER_HOUR="9": third raises TooManyPermanent; ending one frees a place
def test_timed_room_still_needs_a_lifetime(svc):         # create_room("", None, ip) raises Invalid; account=admin with lifetime 1 records account_id and is not permanent
def test_account_is_admin_of_its_room_from_any_browser(svc):
    room = svc.create_room("", None, "1.1.1.1", account=admin, permanent=True)
    assert svc.is_admin(room.id, "", account_id=1) and not svc.is_admin(room.id, "", account_id=2) and not svc.is_admin(room.id, "")
    svc.admin_view(room.id, None, account_id=1)
    with pytest.raises(Unauthorized): svc.admin_view(room.id, None, account_id=2)
    with pytest.raises(Unauthorized): svc.end_room(room.id, None)
async def test_owner_account_joins_at_once_others_wait(svc):   # join(account_id=1).approved is True; a join with no account is pending until approve(account_id=1)
def test_permanent_room_is_not_extended(svc):                  # extend(..., account_id=1) raises Invalid
async def test_permanent_room_survives_sweeps_and_restart(svc, clock, relay, engine):
    # room with two accepted members; clock.advance(days=4000); svc.sweep() == 0; room_view works;
    # a fresh RoomService on the same engine with a fresh FakeRelay: restore() == 1 and that port is open
def test_account_rooms_listed_without_the_cookie(svc):         # rooms_of_visitor("", account_id=1) lists it, is_admin and permanent True
def test_dropped_admin_keeps_and_can_end_its_rooms(engine):
    # make the room with ADMIN_GITHUB_IDS="7"; a second service on the same engine with ADMIN_GITHUB_IDS="":
    # create permanent raises NotEntitled; room_view still works; end_room(account_id=1) succeeds
```

- [ ] **Step 2–4:** Fail, implement, pass (`./test.sh tests/test_rooms.py -q`, then the whole suite).
- [ ] **Step 5:** Commit `Permanent rooms for entitled accounts`.

### Task 5: API, pages and scripts

**Files:** Modify `server/src/app/routes/api.py`, `routes/pages.py`, `templating.py`, `webapp/templates/base.html`, `index.html`, `room.html`, `webapp/static/index.js`, `room.js`, `style.css` if needed; Test `server/tests/test_api.py`, `server/tests/test_pages.py`, `server/tests/test_auth.py`

**Interfaces — Consumes:** `current_account`, Task 4's signatures.
**Produces:**
- `CreateRoom`: `lifetime_hours: int | None = None`, `permanent: bool = False`. `POST /rooms` answers gain `"permanent"`; `GET /r/{id}/state` gains `"permanent"`.
- Every admin route passes `account_id` of `current_account(request)`.
- Template context on every page (context processor in `templating.py`): `account` (Account or None), `login_enabled` (bool), `may_hold_permanent` (bool).
- `base.html`: the prompt shows the login in place of `guest` when signed in; nav shows `<a href="{{ root_path }}/auth/github">[sign in]</a>` when `login_enabled` and signed out, `<a href="#" id="sign-out">[sign out]</a>` when signed in (a small inline script in `base.html` POSTs `/auth/logout` and reloads).
- `index.html`: lifetime `<select>` gains `<option value="permanent">no expiry</option>` only when `may_hold_permanent`; a permanent room's row shows `no expiry` in place of `ends …` and has no `[extend]`.
- `index.js`: value `permanent` sends `{name, permanent: true}`.
- `room.html` / `room.js`: `<main data-permanent="1">` when permanent; then `#expires` reads `no expiry`, no countdown runs and the extend form is not rendered.

- [ ] **Step 1: Failing tests**

```python
# test_api.py (auth_client fixtures)
def test_permanent_room_over_the_api(auth_client):
    body = {"name": "lab", "permanent": True}
    assert auth_client.post("/rooms", json=body).status_code == 401
    sign_in(auth_client, 8, "stranger"); assert auth_client.post("/rooms", json=body).status_code == 403
    auth_client.post("/auth/logout"); sign_in(auth_client, 7, "raub")
    r = auth_client.post("/rooms", json=body); assert r.status_code == 201 and r.json()["permanent"] is True
    rid = r.json()["id"]
    assert auth_client.get(f"/r/{rid}/state").json()["permanent"] is True
    assert auth_client.post(f"/r/{rid}/extend", json={}).status_code == 400
def test_account_administers_from_a_fresh_browser(auth_app):
    # client A signs in as 7 and makes a permanent room; client B (new TestClient, no cookies, other X-Real-IP)
    # gets 401 on /r/{id}/admin, then signs in as 7 and gets 200, and its join is approved at once
def test_room_without_lifetime_or_permanent_is_400(client):
    assert client.post("/rooms", json={"name": ""}).status_code == 400
# test_pages.py
def test_front_page_login_and_no_expiry_option(client, auth_client):
    assert "[sign in]" not in client.get("/").text                     # login not configured
    page = auth_client.get("/").text; assert "[sign in]" in page and "no expiry" not in page
    sign_in(auth_client, 8, "stranger"); page = auth_client.get("/").text
    assert "stranger" in page and "[sign out]" in page and "no expiry" not in page
    auth_client.post("/auth/logout"); sign_in(auth_client, 7, "raub"); assert 'value="permanent"' in auth_client.get("/").text
def test_permanent_room_page(auth_client):                             # data-permanent="1", no id="extend-form"; front page row says "no expiry" and has no data-extend
```
Add the page assertion to `test_session_survives_a_change_of_address` (Task 3).

- [ ] **Step 2–4:** Fail, implement, pass; whole suite passes.
- [ ] **Step 5:** `./build.sh`, run the image with `GITHUB_CLIENT_ID`/`SECRET` set to dummy values and `PUBLIC_URL=http://localhost:8080`, open the front page and a timed room in a browser: the top bar, the create form and the countdown look and work as before, `[sign in]` redirects to github.com.
- [ ] **Step 6:** Commit `Login in the top bar; no-expiry rooms on the pages and API`.

### Task 6: Documentation and deployment settings

**Files:** Modify `README.md`, `deploy/env.example`

- [ ] **Step 1:** README: the intro and "How it works" say rooms end on their own unless a signed-in, entitled account makes one with no expiry; a new section "Accounts and permanent rooms" (what an account is, what is stored: GitHub id and login only; admins by numeric id; the per-account limit; what the server sees is unchanged); the four settings in the table; move "accounts" out of "Out of scope"; the one-time GitHub OAuth app setup with the callback URL `https://raubcamaioni.com/microvpn/auth/github/callback` and how to find a numeric id (`https://api.github.com/users/<login>`).
- [ ] **Step 2:** `deploy/env.example`: the four variables, the two secrets left blank with a comment.
- [ ] **Step 3:** `./test.sh -q` passes. Commit `Document accounts and permanent rooms`.
