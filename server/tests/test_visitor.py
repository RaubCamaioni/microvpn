"""The browser identity: a cookie set on first contact, honoured only from the IP it was issued to."""

import base64

from fastapi.testclient import TestClient

from app.app import create_app
from tests.conftest import FakeRelay, fake_locate, make_settings

COOKIE = "microvpn_visitor"


def KEY(i):
    return base64.b64encode(bytes([i] * 32)).decode()


def test_first_contact_sets_a_cookie_and_it_sticks(client):
    r = client.get("/", headers={"X-Real-IP": "203.0.113.5"})
    assert COOKIE in r.cookies
    token = r.cookies[COOKIE]
    assert len(token) == 22
    set_cookie = r.headers["set-cookie"]
    assert "HttpOnly" in set_cookie and "SameSite=lax" in set_cookie.lower().replace("samesite=lax", "SameSite=lax")
    assert "Path=/" in set_cookie
    client.cookies.set(COOKIE, token)
    r2 = client.get("/", headers={"X-Real-IP": "203.0.113.5"})
    assert "set-cookie" not in r2.headers  # known browser: nothing to set


def test_cookie_from_another_ip_is_replaced(client):
    token = client.get("/", headers={"X-Real-IP": "203.0.113.5"}).cookies[COOKIE]
    client.cookies.set(COOKIE, token)
    r = client.get("/", headers={"X-Real-IP": "198.51.100.1"})
    assert "set-cookie" in r.headers and token not in r.headers["set-cookie"]


def test_secure_flag_follows_the_public_url(tmp_path):
    settings = make_settings(DATABASE=str(tmp_path / "s.db"), PUBLIC_URL="https://example.test/microvpn", ROOT_PATH="/microvpn")
    with TestClient(create_app(settings, relay=FakeRelay(), locate=fake_locate), root_path="/microvpn") as c:
        set_cookie = c.get("/microvpn/", headers={"X-Real-IP": "1.2.3.4"}).headers["set-cookie"]
        assert "Secure" in set_cookie and "Path=/microvpn" in set_cookie


def test_home_page_lists_the_rooms_of_this_browser_and_deletes_them(client):
    ip = {"X-Real-IP": "203.0.113.5"}
    token = client.get("/", headers=ip).cookies[COOKIE]
    me = {**ip}
    client.cookies.set(COOKIE, token)
    assert "no rooms yet" in client.get("/", headers=me).text  # the list is always shown
    mine = client.post("/rooms", json={"name": "game night", "lifetime_hours": 1}, headers=me).json()
    # a room someone else (another browser, another address) created, which this browser joined
    other_browser = TestClient(client.app)
    other = other_browser.post("/rooms", json={"name": "theirs", "lifetime_hours": 1}, headers={"X-Real-IP": "198.51.100.1"}).json()
    client.post(f"/r/{other['id']}/members", json={"name": "rob-laptop", "public_key": KEY(1)}, headers=me)
    # a room this browser has nothing to do with
    TestClient(client.app).post("/rooms", json={"name": "stranger", "lifetime_hours": 1}, headers={"X-Real-IP": "198.51.100.2"})
    page = client.get("/", headers=me).text
    assert "your rooms" in page and "game night" in page and "theirs" in page and "stranger" not in page
    assert f'href="/r/{mine["id"]}"' in page and "admin" in page and "rob-laptop" in page
    assert f'data-delete="{mine["id"]}"' in page and f'data-delete="{other["id"]}"' not in page  # delete only your own
    # the cookie alone (no owner token) ends a room this browser created, and nothing else
    assert client.delete(f"/r/{other['id']}", headers=me).status_code == 401
    assert client.delete(f"/r/{mine['id']}", headers=me).status_code == 204
    assert client.get(f"/r/{mine['id']}/state").status_code == 404
    assert "game night" not in client.get("/", headers=me).text


def test_admin_actions_work_with_the_creator_cookie_alone(client):
    ip = {"X-Real-IP": "203.0.113.5"}
    client.cookies.set(COOKIE, client.get("/", headers=ip).cookies[COOKIE])
    room = client.post("/rooms", json={"name": "", "lifetime_hours": 1}, headers=ip).json()
    page = client.get(f"/r/{room['id']}", headers=ip).text
    assert 'data-admin="1"' in page
    stranger_browser = TestClient(client.app)
    assert 'data-admin="1"' not in stranger_browser.get(f"/r/{room['id']}", headers={"X-Real-IP": "198.51.100.1"}).text
    joined = client.post(f"/r/{room['id']}/members", json={"name": "me", "public_key": KEY(1)}, headers=ip).json()
    assert joined["approved"] is True  # the creator's browser is the admin
    stranger = stranger_browser.post(f"/r/{room['id']}/members", json={"name": "x", "public_key": KEY(2)}, headers={"X-Real-IP": "198.51.100.9"}).json()
    assert stranger["approved"] is False
    admin = client.get(f"/r/{room['id']}/admin", headers=ip).json()
    sid = [m["id"] for m in admin["members"] if m["name"] == "x"][0]
    assert client.delete(f"/r/{room['id']}/members/{sid}", headers=ip).status_code == 204  # reject by cookie
    sid2 = client.post(f"/r/{room['id']}/members", json={"name": "y", "public_key": KEY(3)}, headers={"X-Real-IP": "198.51.100.9"})
    admin = client.get(f"/r/{room['id']}/admin", headers=ip).json()
    sid2 = [m["id"] for m in admin["members"] if m["name"] == "y"][0]
    assert client.post(f"/r/{room['id']}/members/{sid2}/approve", headers=ip).status_code == 204  # accept by cookie
    assert stranger_browser.get(f"/r/{room['id']}/admin", headers={"X-Real-IP": "198.51.100.1"}).status_code == 401
