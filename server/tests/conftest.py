"""Shared fixtures: settings on a temporary database, a relay double, the app and a client.

Tests never bind the real relay unless they construct one themselves (tests/test_relay.py).
"""

import pytest
from fastapi.testclient import TestClient

from app.app import create_app
from app.settings import Settings


def make_settings(**over) -> Settings:
    base = {
        "DATABASE": "x",
        "RELAY_BIND": "127.0.0.1",
        "RELAY_PORT_RANGE": "30000-30099",
        "RELAY_HOST": "relay.test",
    }
    return Settings.from_environment({**base, **over})


class FakeRelay:
    """Stands in for app.relay.Relay: records what the service asks of it, binds nothing."""

    def __init__(self):
        self.opened: dict[int, str] = {}
        self.closed: list[int] = []
        self.activity: dict[int, int] = {}
        self.fail_ports: set[int] = set()

    async def open(self, port: int, room_id: str) -> bool:
        if port in self.fail_ports:
            return False
        self.opened[port] = room_id
        return True

    def close(self, port: int) -> None:
        if port in self.opened:
            del self.opened[port]
            self.closed.append(port)

    def close_room(self, room_id: str) -> None:
        for port in [p for p, r in self.opened.items() if r == room_id]:
            self.close(port)

    def slots_active(self, port: int, window: float = 30.0) -> int:
        return self.activity.get(port, 0)

    def ports_in_use(self) -> int:
        return len(self.opened)


@pytest.fixture
def settings(tmp_path):
    return make_settings(DATABASE=str(tmp_path / "test.db"))


@pytest.fixture
def relay():
    return FakeRelay()


@pytest.fixture
def app(settings, relay):
    return create_app(settings, relay=relay)


@pytest.fixture
def client(app):
    # the context manager runs the lifespan (tables, restore, sweeper)
    with TestClient(app) as c:
        yield c
