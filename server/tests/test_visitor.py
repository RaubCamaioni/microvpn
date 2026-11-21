"""The browser identity: a cookie set when the browser first creates or joins a room, honoured only
from the IP it was issued to."""

import base64

from fastapi.testclient import TestClient
from sqlalchemy import func
from sqlmodel import Session, select

from app.app import create_app
from app.models import Visitor
from tests.conftest import FakeRelay, fake_locate, make_settings

COOKIE = "microvpn_visitor"


def KEY(i):
    return base64.b64encode(bytes([i] * 32)).decode()


def visitors(client) -> int:
    with Session(client.app.state.engine) as s:
        return s.exec(select(func.count()).select_from(Visitor)).one()


def test_requests_without_a_cookie_leave_nothing_behind(client):
    ip = {"X-Real-IP": "203.0.113.5"}
    for _ in range(1000):
        client.cookies.clear()
        r = client.get("/", headers=ip)
        client.cookies.clear()
        assert r.status_code == 200 and client.get("/no/such/path", headers=ip).status_code == 404
    assert visitors(client) == 0
    assert "no rooms yet" in r.text and "set-cookie" not in r.headers
    assert "set-cookie" not in client.get("/r/" + "a" * 26, headers=ip).headers
    assert "set-cookie" not in client.get("/r/" + "a" * 26 + "/state", headers=ip).headers
    # a cookie nobody issued is no better than none
    client.cookies.set(COOKIE, "x" * 22)
    assert "set-cookie" not in client.get("/", headers=ip).headers
    assert visitors(client) == 0


def test_creating_a_room_sets_a_cookie_and_it_sticks(client):
    ip = {"X-Real-IP": "203.0.113.5"}
    r = client.post("/rooms", json={"name": "", "lifetime_hours": 1}, headers=ip)
    assert COOKIE in r.cookies
    token = r.cookies[COOKIE]
    assert len(token) == 22
    set_cookie = r.headers["set-cookie"]
    assert "HttpOnly" in set_cookie and "samesite=lax" in set_cookie.lower()
    assert "Path=/" in set_cookie
    assert visitors(client) == 1
    r2 = client.get("/", headers=ip)
    assert "set-cookie" not in r2.headers  # known browser: nothing to set
    r3 = client.post("/rooms", json={"name": "", "lifetime_hours": 1}, headers=ip)
    assert r3.status_code == 201 and "set-cookie" not in r3.headers and visitors(client) == 1


def test_joining_a_room_sets_a_cookie_and_the_front_page_lists_the_room(client):
    creator = TestClient(client.app)
    room = creator.post("/rooms", json={"name": "theirs", "lifetime_hours": 1}, headers={"X-Real-IP": "198.51.100.1"}).json()
    ip = {"X-Real-IP": "203.0.113.5"}
    r = client.post(f"/r/{room['id']}/members", json={"name": "rob-laptop", "public_key": KEY(1)}, headers=ip)
    assert r.status_code == 201 and r.json()["approved"] is False and COOKIE in r.cookies
    assert visitors(client) == 2  # the creator and this browser
    page = client.get("/", headers=ip)
    assert "theirs" in page.text and "rob-laptop" in page.text and "set-cookie" not in page.headers
    r2 = client.post(f"/r/{room['id']}/members", json={"name": "rob-phone", "public_key": KEY(2)}, headers=ip)
    assert r2.status_code == 201 and "set-cookie" not in r2.headers and visitors(client) == 2
    # a device joined with the owner token alone (no cookie) is the admin's, and its browser is remembered
    cli = TestClient(client.app)
    owner = {"Authorization": "Bearer " + room["owner_token"], "X-Real-IP": "192.0.2.7"}
    r3 = cli.post(f"/r/{room['id']}/members", json={"name": "server", "public_key": KEY(3)}, headers=owner)
    assert r3.json()["approved"] is True and COOKIE in r3.cookies and visitors(client) == 3
    assert "theirs" in cli.get("/", headers={"X-Real-IP": "192.0.2.7"}).text


def test_a_refused_create_or_join_remembers_nobody(client):
    ip = {"X-Real-IP": "203.0.113.5"}
    r = client.post("/rooms", json={"name": "", "lifetime_hours": 2}, headers=ip)
    assert r.status_code == 400 and "set-cookie" not in r.headers
    r = client.post("/r/" + "a" * 26 + "/members", json={"name": "x", "public_key": KEY(1)}, headers=ip)
    assert r.status_code == 404 and "set-cookie" not in r.headers
    for _ in range(3):
        TestClient(client.app).post("/rooms", json={"name": "", "lifetime_hours": 1}, headers=ip)
    r = client.post("/rooms", json={"name": "", "lifetime_hours": 1}, headers=ip)
    assert r.status_code == 429 and "set-cookie" not in r.headers
    assert visitors(client) == 3  # the three browsers that did create a room


def test_cookie_from_another_ip_is_not_honoured_and_is_replaced_by_the_next_room(client):
    home = {"X-Real-IP": "203.0.113.5"}
    away = {"X-Real-IP": "198.51.100.1"}
    r = client.post("/rooms", json={"name": "bridge club", "lifetime_hours": 1}, headers=home)
    token, room = r.cookies[COOKIE], r.json()
    page = client.get("/", headers=away)
    assert "bridge club" not in page.text and "set-cookie" not in page.headers
    assert 'data-admin="1"' not in client.get(f"/r/{room['id']}", headers=away).text
    assert client.delete(f"/r/{room['id']}", headers=away).status_code == 401
    assert "bridge club" in client.get("/", headers=home).text  # back at the first address it is known again
    r = client.post("/rooms", json={"name": "", "lifetime_hours": 1}, headers=away)
    assert "set-cookie" in r.headers and token not in r.headers["set-cookie"]


def test_secure_flag_follows_the_public_url(tmp_path):
    settings = make_settings(DATABASE=str(tmp_path / "s.db"), PUBLIC_URL="https://example.test/microvpn", ROOT_PATH="/microvpn")
    with TestClient(create_app(settings, relay=FakeRelay(), locate=fake_locate), root_path="/microvpn") as c:
        r = c.post("/microvpn/rooms", json={"name": "", "lifetime_hours": 1}, headers={"X-Real-IP": "1.2.3.4"})
        set_cookie = r.headers["set-cookie"]
        assert "Secure" in set_cookie and "Path=/microvpn" in set_cookie


def test_home_page_lists_the_rooms_of_this_browser_and_deletes_them(client):
    ip = {"X-Real-IP": "203.0.113.5"}
    me = {**ip}
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
    stranger_browser.post(f"/r/{room['id']}/members", json={"name": "y", "public_key": KEY(3)}, headers={"X-Real-IP": "198.51.100.9"})
    admin = client.get(f"/r/{room['id']}/admin", headers=ip).json()
    sid2 = [m["id"] for m in admin["members"] if m["name"] == "y"][0]
    assert client.post(f"/r/{room['id']}/members/{sid2}/approve", headers=ip).status_code == 204  # accept by cookie
    assert stranger_browser.get(f"/r/{room['id']}/admin", headers={"X-Real-IP": "198.51.100.1"}).status_code == 401
