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
    j = client.post(f"/r/{room['id']}/members", json={"name": "alice", "public_key": KEY(1)})
    assert j.status_code == 201 and j.json()["address"].endswith(".2") and len(j.json()["member_token"]) == 22
    assert client.post(f"/r/{room['id']}/members", json={"name": "x", "public_key": KEY(1)}).status_code == 409
    assert client.post(f"/r/{room['id']}/members", json={"name": "x", "public_key": "bad"}).status_code == 400
    client.post(f"/r/{room['id']}/members", json={"name": "bob", "public_key": KEY(2)})
    state = client.get(f"/r/{room['id']}/state").json()
    assert [m["name"] for m in state["members"]] == ["alice", "bob"] and state["max_members"] == 8
    auth = {"Authorization": "Bearer " + j.json()["member_token"]}
    me = client.get(f"/r/{room['id']}/me", headers=auth)
    assert me.status_code == 200 and me.json()["peers"][0]["name"] == "bob"
    assert "__PRIVATE_KEY__" in me.json()["config_template"]
    conf = client.get(f"/r/{room['id']}/me.conf", headers=auth)
    assert conf.headers["content-type"].startswith("text/plain") and conf.text == me.json()["config_template"]
    assert client.get(f"/r/{room['id']}/me").status_code == 401
    assert client.get(f"/r/{room['id']}/me", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.delete(f"/r/{room['id']}/me", headers=auth).status_code == 204
    assert [m["name"] for m in client.get(f"/r/{room['id']}/state").json()["members"]] == ["bob"]
    assert client.delete(f"/r/{room['id']}", headers=auth).status_code == 401
    owner = {"Authorization": "Bearer " + room["owner_token"]}
    assert client.delete(f"/r/{room['id']}", headers=owner).status_code == 204
    assert client.get(f"/r/{room['id']}/state").status_code == 404
    assert client.get("/r/zzzz/state").status_code == 404


def test_healthz_counts_live_state(client):
    room = create(client).json()
    client.post(f"/r/{room['id']}/members", json={"name": "a", "public_key": KEY(1)})
    client.post(f"/r/{room['id']}/members", json={"name": "b", "public_key": KEY(2)})
    assert client.get("/healthz").json() == {"rooms": 1, "members": 2, "ports_in_use": 1, "relay": "ok"}
