import base64
import time

from fastapi.testclient import TestClient

from app.app import create_app
from tests.conftest import FakeRelay, make_settings


def KEY(i):
    return base64.b64encode(bytes([i] * 32)).decode()


def test_restore_runs_at_startup(tmp_path):
    settings = make_settings(DATABASE=str(tmp_path / "l.db"))
    with TestClient(create_app(settings, relay=FakeRelay())) as c:
        room = c.post("/rooms", json={"name": "", "lifetime_hours": 1}).json()
        owner = {"Authorization": "Bearer " + room["owner_token"]}
        for i in (1, 2):
            c.post(f"/r/{room['id']}/members", json={"name": f"m{i}", "public_key": KEY(i)}, headers=owner)
    relay = FakeRelay()
    with TestClient(create_app(settings, relay=relay)) as c:
        assert len(relay.opened) == 1 and c.get("/healthz").json()["ports_in_use"] == 1


def test_sweeper_runs_periodically(tmp_path):
    settings = make_settings(DATABASE=str(tmp_path / "s.db"), SWEEP_SECONDS="0.05")
    app = create_app(settings, relay=FakeRelay())
    with TestClient(app):
        time.sleep(0.3)
    assert app.state.sweeps >= 2
