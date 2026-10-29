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
