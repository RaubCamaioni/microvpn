from urllib.parse import parse_qs, urlparse

from sqlmodel import Session, select

from app.models import Account
from tests.conftest import oauth_state, sign_in


def test_login_disabled_without_client_id(client):
    assert client.get("/auth/github", follow_redirects=False).status_code == 404
    assert client.get("/auth/github/callback?code=c&state=s", follow_redirects=False).status_code == 404
    assert client.post("/auth/logout").status_code == 404


def test_login_round_trip(auth_client, auth_app):
    r = auth_client.get("/auth/github", follow_redirects=False)
    assert r.status_code == 302
    where = urlparse(r.headers["location"])
    query = parse_qs(where.query)
    assert f"{where.scheme}://{where.netloc}{where.path}" == "https://github.com/login/oauth/authorize"
    assert query["client_id"] == ["cid"] and "scope" not in query
    assert query["redirect_uri"] == ["http://localhost:8080/auth/github/callback"]
    auth_app.state.github.identities["good"] = (7, "raub")
    r = auth_client.get(f"/auth/github/callback?code=good&state={query['state'][0]}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/"
    cookie = next(c for c in r.headers.get_list("set-cookie") if c.startswith("microvpn_session="))
    attrs = cookie.lower()
    assert "httponly" in attrs and "samesite=lax" in attrs and "max-age=2592000" in attrs and "secure" not in attrs
    assert "path=/" in attrs
    token = auth_client.cookies["microvpn_session"]
    assert auth_app.state.accounts.account_of(token).login == "raub"
    assert auth_client.post("/auth/logout").status_code == 204
    assert auth_app.state.accounts.account_of(token) is None


def test_a_state_is_used_once(auth_client, auth_app):
    state = oauth_state(auth_client)
    auth_app.state.github.identities["good"] = (7, "raub")
    assert "microvpn_session" in auth_client.get(f"/auth/github/callback?code=good&state={state}", follow_redirects=False).headers["set-cookie"]
    auth_client.cookies.delete("microvpn_session")
    # the state cookie went with the first use, so the same link signs nobody in again
    again = auth_client.get(f"/auth/github/callback?code=good&state={state}", follow_redirects=False)
    assert again.status_code == 302 and "microvpn_session" not in auth_client.cookies


def test_cancel_and_bad_state_land_signed_out(auth_client, auth_app):
    auth_app.state.github.identities["good"] = (7, "raub")
    state = oauth_state(auth_client)
    for q in ("error=access_denied&state=" + state, "code=good&state=wrong", "code=good", ""):
        r = auth_client.get("/auth/github/callback?" + q, follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "/"
        assert "microvpn_session" not in auth_client.cookies
    with Session(auth_app.state.engine) as s:
        assert s.exec(select(Account)).all() == []


def test_callback_without_ever_starting_lands_signed_out(auth_client, auth_app):
    auth_app.state.github.identities["good"] = (7, "raub")
    r = auth_client.get("/auth/github/callback?code=good&state=", follow_redirects=False)
    assert r.status_code == 302 and "microvpn_session" not in auth_client.cookies


def test_github_failure_is_502_and_creates_nothing(auth_client, auth_app):
    state = oauth_state(auth_client)
    r = auth_client.get(f"/auth/github/callback?code=unknown&state={state}", follow_redirects=False)
    assert r.status_code == 502 and "GitHub sign-in failed" in r.text
    assert "microvpn_session" not in auth_client.cookies
    with Session(auth_app.state.engine) as s:
        assert s.exec(select(Account)).all() == []


def test_session_survives_a_change_of_address(auth_client, auth_app):
    sign_in(auth_client, 7, "raub")
    token = auth_client.cookies["microvpn_session"]
    for ip in ("1.1.1.1", "2.2.2.2"):
        assert auth_client.get("/", headers={"X-Real-IP": ip}).status_code == 200
        assert auth_app.state.accounts.account_of(token).login == "raub"


def test_stale_sessions_are_swept_with_the_rooms(tmp_path):
    """The app's sweeper also forgets sessions nobody has used for 30 days."""
    import time
    from datetime import timedelta

    from fastapi.testclient import TestClient

    from app.app import create_app
    from app.models import AccountSession, utcnow
    from tests.conftest import FakeGitHub, FakeRelay, fake_locate, make_settings

    settings = make_settings(
        DATABASE=str(tmp_path / "s.db"), GITHUB_CLIENT_ID="cid", GITHUB_CLIENT_SECRET="cs", SWEEP_SECONDS="0.05"
    )
    app = create_app(settings, relay=FakeRelay(), locate=fake_locate, github=FakeGitHub())
    with TestClient(app) as c:
        sign_in(c, 7, "raub")
        with Session(app.state.engine) as s:
            row = s.exec(select(AccountSession)).one()
            row.last_seen = utcnow() - timedelta(days=31)
            s.add(row)
            s.commit()
        seen = app.state.sweeps
        deadline = time.monotonic() + 5
        while app.state.sweeps < seen + 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        with Session(app.state.engine) as s:
            assert s.exec(select(AccountSession)).all() == []


# -- found by the branch review ------------------------------------------------------------------


def test_signing_out_gives_up_the_rooms_made_while_signed_in(auth_client):
    """A room made while signed in is the account's alone: the browser it was made in holds no owner
    token and no cookie claim on it, so after [sign out] that browser is a stranger to the room."""
    sign_in(auth_client, 7, "raub")
    made = auth_client.post("/rooms", json={"name": "lab", "permanent": True}).json()
    rid = made["id"]
    assert made["owner_token"] == ""
    assert auth_client.get(f"/r/{rid}/admin").status_code == 200
    auth_client.post("/auth/logout")
    assert auth_client.get(f"/r/{rid}/admin").status_code == 401
    assert auth_client.delete(f"/r/{rid}").status_code == 401
    assert 'data-admin="0"' in auth_client.get(f"/r/{rid}").text
    assert rid not in auth_client.get("/").text  # the front page no longer lists it for this browser
    joined = auth_client.post(f"/r/{rid}/members", json={"name": "x", "public_key": "A" * 43 + "="}).json()
    assert joined["approved"] is False
    sign_in(auth_client, 7, "raub")
    assert auth_client.get(f"/r/{rid}/admin").status_code == 200


def test_a_session_in_use_keeps_its_cookie_fresh(auth_client, auth_app):
    """30 days from the last use, in the browser as on the server: the cookie is issued again when the
    session is touched, not on every request."""
    from datetime import timedelta

    from app.models import AccountSession, utcnow

    def session_cookies(response):
        return [c for c in response.headers.get_list("set-cookie") if c.startswith("microvpn_session=")]

    sign_in(auth_client, 7, "raub")
    assert session_cookies(auth_client.get("/")) == []  # just signed in: nothing to refresh
    with Session(auth_app.state.engine) as s:
        row = s.exec(select(AccountSession)).one()
        row.last_seen = utcnow() - timedelta(days=3)
        s.add(row)
        s.commit()
    token = auth_client.cookies["microvpn_session"]
    (cookie,) = session_cookies(auth_client.get("/"))
    attrs = cookie.lower()
    assert cookie.startswith(f"microvpn_session={token};") and "max-age=2592000" in attrs
    assert "httponly" in attrs and "samesite=lax" in attrs and "path=/" in attrs
    assert session_cookies(auth_client.get("/")) == []


def test_requests_from_another_site_cannot_act(auth_client):
    """A page on another site, or on another name under this domain, cannot make the browser act with
    its cookies: anything but a read is refused when the browser says the request is not this site's own."""
    sign_in(auth_client, 7, "raub")
    body = {"name": "lab", "permanent": True}
    for site in ("cross-site", "same-site"):
        r = auth_client.post("/rooms", json=body, headers={"Sec-Fetch-Site": site})
        assert r.status_code == 403 and r.json() == {"detail": "request from another site refused"}
    rid = auth_client.post("/rooms", json=body, headers={"Sec-Fetch-Site": "same-origin"}).json()["id"]
    waiting = auth_client.post(f"/r/{rid}/members", json={"name": "x", "public_key": "B" * 43 + "="})
    member_id = auth_client.get(f"/r/{rid}/admin").json()["members"][0]["id"]
    assert waiting.status_code == 201
    forged = {"Sec-Fetch-Site": "same-site"}
    assert auth_client.post(f"/r/{rid}/members/{member_id}/approve", headers=forged).status_code == 403
    assert auth_client.delete(f"/r/{rid}", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert auth_client.post("/auth/logout", headers=forged).status_code == 403
    # reads, and arriving from another site by a link, are untouched; so is a client that is no browser
    assert auth_client.get(f"/r/{rid}", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200
    assert auth_client.get(f"/r/{rid}/state", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200
    assert auth_client.post(f"/r/{rid}/members/{member_id}/approve", headers={"Sec-Fetch-Site": "none"}).status_code == 204
    assert auth_client.delete(f"/r/{rid}").status_code == 204
