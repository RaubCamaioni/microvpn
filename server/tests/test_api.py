def test_healthz_reports_counts_and_no_store(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert r.json() == {"rooms": 0, "members": 0, "ports_in_use": 0, "relay": "ok"}
