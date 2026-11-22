def test_healthz_reports_counts_and_no_store(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert r.json() == {"rooms": 0, "members": 0, "ports_in_use": 0, "relay": "ok"}


import base64  # noqa: E402


def KEY(i):
    return base64.b64encode(bytes([i] * 32)).decode()


def create(client, **body):
    return client.post("/rooms", json={"name": "", "lifetime_hours": 1, **body}, headers={"X-Real-IP": "9.9.9.9"})


def test_create_room(client):
    r = create(client, name="night")
    assert r.status_code == 201
    body = r.json()
    assert len(body["id"]) == 26 and body["url"].endswith("/r/" + body["id"]) and len(body["owner_token"]) == 22
    assert create(client, lifetime_hours=2).status_code == 400
    assert create(client, lifetime_hours="x").status_code in (400, 422)


def test_per_ip_limit_uses_x_real_ip(client):
    for _ in range(3):
        assert create(client).status_code == 201
    assert create(client).status_code == 429
    other = client.post("/rooms", json={"name": "", "lifetime_hours": 1}, headers={"X-Real-IP": "8.8.8.8"})
    assert other.status_code == 201


def test_join_state_me_leave_end(client):
    room = create(client).json()
    owner = {"Authorization": "Bearer " + room["owner_token"]}
    j = client.post(f"/r/{room['id']}/members", json={"name": "alice", "public_key": KEY(1)}, headers=owner)
    assert j.status_code == 201 and j.json()["address"].endswith(".2") and len(j.json()["member_token"]) == 22
    assert j.json()["approved"] is True  # the admin's own device
    assert client.post(f"/r/{room['id']}/members", json={"name": "x", "public_key": KEY(1)}).status_code == 409
    assert client.post(f"/r/{room['id']}/members", json={"name": "x", "public_key": "bad"}).status_code == 400
    bob = client.post(f"/r/{room['id']}/members", json={"name": "bob", "public_key": KEY(2)}).json()
    assert bob["approved"] is False
    state = client.get(f"/r/{room['id']}/state").json()
    assert [(m["name"], m["approved"]) for m in state["members"]] == [("alice", True), ("bob", False)]
    assert all("id" in m and m["active"] is False and m["handshaking"] is False and m["bytes"] == 0 for m in state["members"])
    assert state["max_members"] == 8
    auth = {"Authorization": "Bearer " + j.json()["member_token"]}
    bob_auth = {"Authorization": "Bearer " + bob["member_token"]}
    waiting = client.get(f"/r/{room['id']}/me", headers=bob_auth).json()
    assert waiting["approved"] is False and waiting["peers"] == [] and waiting["config_template"] == ""
    bob_id = state["members"][1]["id"]
    assert client.post(f"/r/{room['id']}/members/{bob_id}/approve", headers=auth).status_code == 401
    assert client.post(f"/r/{room['id']}/members/{bob_id}/approve", headers=owner).status_code == 204
    assert client.post(f"/r/{room['id']}/members/4242/approve", headers=owner).status_code == 404
    me = client.get(f"/r/{room['id']}/me", headers=auth)
    assert me.status_code == 200 and me.json()["peers"][0]["name"] == "bob"
    assert "__PRIVATE_KEY__" in me.json()["config_template"]
    conf = client.get(f"/r/{room['id']}/me.conf", headers=auth)
    assert conf.headers["content-type"].startswith("text/plain") and conf.text == me.json()["config_template"]
    assert client.get(f"/r/{room['id']}/me").status_code == 401
    assert client.get(f"/r/{room['id']}/me", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.delete(f"/r/{room['id']}/members/{bob_id}", headers=auth).status_code == 401
    kick = client.delete(f"/r/{room['id']}/members/{bob_id}", headers=owner)
    assert kick.status_code == 409 and "new one" in kick.json()["detail"]  # accepted members are not kicked
    assert client.get(f"/r/{room['id']}/me", headers=bob_auth).status_code == 200
    assert client.delete(f"/r/{room['id']}/me", headers=auth).status_code == 204
    assert client.delete(f"/r/{room['id']}/me", headers=bob_auth).status_code == 204
    assert client.get(f"/r/{room['id']}/state").json()["members"] == []
    assert client.delete(f"/r/{room['id']}", headers=auth).status_code == 401
    assert client.delete(f"/r/{room['id']}", headers=owner).status_code == 204
    assert client.get(f"/r/{room['id']}/state").status_code == 404
    assert client.get("/r/zzzz/state").status_code == 404


def test_healthz_counts_live_state(client):
    room = create(client).json()
    owner = {"Authorization": "Bearer " + room["owner_token"]}
    client.post(f"/r/{room['id']}/members", json={"name": "a", "public_key": KEY(1)}, headers=owner)
    client.post(f"/r/{room['id']}/members", json={"name": "b", "public_key": KEY(2)}, headers=owner)
    assert client.get("/healthz").json() == {"rooms": 1, "members": 2, "ports_in_use": 1, "relay": "ok"}


def test_join_script_endpoint_is_gone(client):
    room = create(client).json()
    assert client.get(f"/r/{room['id']}/join.sh").status_code == 404


def test_admin_endpoint_shows_ip_and_location_to_the_owner_only(client):
    room = create(client).json()
    owner = {"Authorization": "Bearer " + room["owner_token"]}
    client.post(f"/r/{room['id']}/members", json={"name": "admin-pc", "public_key": KEY(1)}, headers={**owner, "X-Real-IP": "203.0.113.7"})
    client.post(f"/r/{room['id']}/members", json={"name": "phone", "public_key": KEY(2)}, headers={"X-Real-IP": "198.51.100.9"})
    assert client.get(f"/r/{room['id']}/admin").status_code == 401
    assert client.get(f"/r/{room['id']}/admin", headers={"Authorization": "Bearer nope"}).status_code == 401
    view = client.get(f"/r/{room['id']}/admin", headers=owner).json()
    assert [(m["name"], m["approved"], m["joined_ip"], m["location"]) for m in view["members"]] == [
        ("admin-pc", True, "203.0.113.7", "Testville, 203.0.113.7"),
        ("phone", False, "198.51.100.9", "Testville, 198.51.100.9"),
    ]
    public = client.get(f"/r/{room['id']}/state").json()
    assert "joined_ip" not in public["members"][0] and "location" not in public["members"][0]


def test_extend_endpoint(client):
    room = create(client, lifetime_hours=4).json()
    owner = {"Authorization": "Bearer " + room["owner_token"]}
    assert client.post(f"/r/{room['id']}/extend", json={}).status_code == 401
    assert client.post(f"/r/{room['id']}/extend", json={"lifetime_hours": 3}, headers=owner).status_code == 400
    r = client.post(f"/r/{room['id']}/extend", json={}, headers=owner)
    assert r.status_code == 200 and r.json()["expires_at"] >= room["expires_at"]
    r2 = client.post(f"/r/{room['id']}/extend", json={"lifetime_hours": 1}, headers=owner)
    assert r2.status_code == 200 and r2.json()["expires_at"] < r.json()["expires_at"]
    assert client.get(f"/r/{room['id']}/state").json()["expires_at"] == r2.json()["expires_at"]


# -- accounts: rooms with no expiry, and the account as a room's admin ----------------------------

from fastapi.testclient import TestClient  # noqa: E402

from tests.conftest import sign_in  # noqa: E402


def test_permanent_room_over_the_api(auth_client):
    body = {"name": "lab", "permanent": True}
    assert auth_client.post("/rooms", json=body).status_code == 401
    sign_in(auth_client, 8, "stranger")
    assert auth_client.post("/rooms", json=body).status_code == 403
    auth_client.post("/auth/logout")
    assert auth_client.post("/rooms", json=body).status_code == 401
    sign_in(auth_client, 7, "raub")
    r = auth_client.post("/rooms", json=body)
    assert r.status_code == 201 and r.json()["permanent"] is True and r.json()["expires_at"].startswith("9999-12-31")
    rid = r.json()["id"]
    assert auth_client.get(f"/r/{rid}/state").json()["permanent"] is True
    assert auth_client.post(f"/r/{rid}/extend", json={}).status_code == 400
    timed = auth_client.post("/rooms", json={"name": "", "lifetime_hours": 1}).json()
    assert timed["permanent"] is False and auth_client.get(f"/r/{timed['id']}/state").json()["permanent"] is False


def test_account_administers_from_a_fresh_browser(auth_app):
    with TestClient(auth_app) as home, TestClient(auth_app) as phone:
        sign_in(home, 7, "raub")
        rid = home.post("/rooms", json={"name": "lab", "permanent": True}, headers={"X-Real-IP": "1.1.1.1"}).json()["id"]
        away = {"X-Real-IP": "2.2.2.2"}
        assert phone.get(f"/r/{rid}/admin", headers=away).status_code == 401
        waiting = phone.post(f"/r/{rid}/members", json={"name": "phone", "public_key": KEY(1)}, headers=away).json()
        assert waiting["approved"] is False
        sign_in(phone, 7, "raub")
        assert phone.get(f"/r/{rid}/admin", headers=away).status_code == 200
        mine = phone.post(f"/r/{rid}/members", json={"name": "pc", "public_key": KEY(2)}, headers=away).json()
        assert mine["approved"] is True
        member_id = phone.get(f"/r/{rid}/admin", headers=away).json()["members"][0]["id"]
        assert phone.post(f"/r/{rid}/members/{member_id}/approve", headers=away).status_code == 204
        assert phone.delete(f"/r/{rid}", headers=away).status_code == 204


def test_another_account_is_not_the_admin(auth_app):
    with TestClient(auth_app) as owner, TestClient(auth_app) as other:
        sign_in(owner, 7, "raub")
        rid = owner.post("/rooms", json={"name": "lab", "permanent": True}).json()["id"]
        sign_in(other, 8, "stranger")
        for call in (
            lambda: other.get(f"/r/{rid}/admin"),
            lambda: other.delete(f"/r/{rid}"),
            lambda: other.post(f"/r/{rid}/extend", json={}),
        ):
            assert call().status_code == 401
        assert other.post(f"/r/{rid}/members", json={"name": "x", "public_key": KEY(3)}).json()["approved"] is False


def test_room_without_lifetime_or_permanent_is_400(client):
    assert client.post("/rooms", json={"name": ""}).status_code == 400
    # the app has no login here: nobody can be signed in, so a permanent room is refused as signed out
    assert client.post("/rooms", json={"name": "", "permanent": True}).status_code == 401
