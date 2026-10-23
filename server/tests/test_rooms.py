import base64
from datetime import datetime, timedelta, timezone

import pytest

from app.models import create_db_and_tables, make_engine
from app.rooms import (
    DuplicateKey,
    Invalid,
    RateLimited,
    RoomFull,
    RoomGone,
    RoomService,
    ServerFull,
    TooManyRooms,
    Unauthorized,
)
from app.wgconfig import PRIVATE_KEY_PLACEHOLDER
from tests.conftest import FakeRelay, make_settings


def KEY(i):
    return base64.b64encode(bytes([i] * 32)).decode()


@pytest.fixture
def clock():
    class C:
        t = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    return C


def service(tmp_path, clock, name="r.db", **settings_over):
    engine = make_engine(str(tmp_path / name))
    create_db_and_tables(engine)
    relay = FakeRelay()
    s = RoomService(engine, make_settings(**{"RELAY_PORT_RANGE": "30000-30009", **settings_over}), relay, now=lambda: clock.t)
    s.relay = relay
    return s


@pytest.fixture
def svc(tmp_path, clock):
    return service(tmp_path, clock)


def test_create_room_fields_and_limits(svc, clock):
    r = svc.create_room(" game night ", 4, "1.1.1.1")
    assert len(r.id) == 26 and r.name == "game night" and r.expires_at == clock.t + timedelta(hours=4)
    assert r.subnet.startswith("10.") and r.subnet.endswith(".0/24") and len(r.owner_token) == 22
    with pytest.raises(Invalid):
        svc.create_room("", 2, "1.1.1.1")
    with pytest.raises(Invalid):
        svc.create_room("x" * 41, 1, "1.1.1.1")
    svc.create_room("", 1, "1.1.1.1")
    svc.create_room("", 1, "1.1.1.1")
    with pytest.raises(RateLimited):
        svc.create_room("", 1, "1.1.1.1")
    clock.t += timedelta(hours=1, seconds=1)
    svc.create_room("", 1, "1.1.1.1")  # the window slid


def test_global_room_cap(tmp_path, clock):
    s = service(tmp_path, clock, "g.db", ROOM_MAX_LIVE="2", ROOM_CREATE_PER_IP_PER_HOUR="10")
    s.create_room("", 1, "a")
    s.create_room("", 1, "a")
    with pytest.raises(TooManyRooms):
        s.create_room("", 1, "a")


async def test_join_assigns_addresses_pairs_and_ports(svc):
    r = svc.create_room("", 1, "ip")
    a = await svc.join(r.id, "alice", KEY(1))
    b = await svc.join(r.id, "bob", KEY(2))
    c = await svc.join(r.id, "carol", KEY(3))
    base = r.subnet[: -len("0/24")]  # "10.x.y."
    assert (a.address, b.address, c.address) == (base + "2", base + "3", base + "4") and len(a.token) == 22
    assert len(svc.relay.opened) == 3 and all(30000 <= p <= 30009 for p in svc.relay.opened)
    view = svc.member_view(r.id, a.token)
    assert view.address == a.address and PRIVATE_KEY_PLACEHOLDER in view.config_template
    assert [p.name for p in view.peers] == ["bob", "carol"] and view.config_template.count("[Peer]") == 2
    assert {p.port for p in view.peers} <= set(svc.relay.opened) and len(view.peers_hash) == 16
    state = svc.room_view(r.id)
    assert [m.name for m in state.members] == ["alice", "bob", "carol"] and state.max_members == 8


async def test_join_rejections(svc):
    r = svc.create_room("", 1, "ip")
    await svc.join(r.id, "alice", KEY(1))
    with pytest.raises(DuplicateKey):
        await svc.join(r.id, "alice2", KEY(1))
    with pytest.raises(Invalid):
        await svc.join(r.id, "bad\nname", KEY(2))
    with pytest.raises(Invalid):
        await svc.join(r.id, "ok", "not-a-key")
    with pytest.raises(RoomGone):
        await svc.join("z" * 26, "ok", KEY(2))
    with pytest.raises(RoomGone):
        await svc.join("not an id", "ok", KEY(2))


async def test_room_full(tmp_path, clock):
    s = service(tmp_path, clock, "f.db", ROOM_MAX_MEMBERS="2")
    r = s.create_room("", 1, "ip")
    await s.join(r.id, "a", KEY(1))
    await s.join(r.id, "b", KEY(2))
    with pytest.raises(RoomFull):
        await s.join(r.id, "c", KEY(3))


async def test_join_rolls_back_when_ports_run_out(tmp_path, clock):
    s = service(tmp_path, clock, "p.db", RELAY_PORT_RANGE="30000-30001")
    r = s.create_room("", 1, "ip")
    await s.join(r.id, "a", KEY(1))
    await s.join(r.id, "b", KEY(2))  # one pair, one of the two ports
    ab_port = set(s.relay.opened)
    with pytest.raises(ServerFull):
        await s.join(r.id, "c", KEY(3))  # needs two ports, one is free
    assert [m.name for m in s.room_view(r.id).members] == ["a", "b"]
    assert set(s.relay.opened) == ab_port and len(s.relay.closed) == 1  # the port c briefly took was closed


async def test_leave_and_end_room_release_ports(svc):
    r = svc.create_room("", 1, "ip")
    a = await svc.join(r.id, "a", KEY(1))
    b = await svc.join(r.id, "b", KEY(2))
    await svc.join(r.id, "c", KEY(3))
    svc.leave(r.id, b.token)
    assert [m.name for m in svc.room_view(r.id).members] == ["a", "c"] and len(svc.relay.opened) == 1
    with pytest.raises(Unauthorized):
        svc.leave(r.id, "wrong")
    with pytest.raises(Unauthorized):
        svc.end_room(r.id, a.token)
    svc.end_room(r.id, r.owner_token)
    with pytest.raises(RoomGone):
        svc.room_view(r.id)
    assert svc.relay.opened == {} and svc.counts() == {"rooms": 0, "members": 0, "ports_in_use": 0}


async def test_expired_room_is_gone_before_sweep(svc, clock):
    r = svc.create_room("", 1, "ip")
    a = await svc.join(r.id, "a", KEY(1))
    clock.t += timedelta(hours=1, seconds=1)
    with pytest.raises(RoomGone):
        svc.room_view(r.id)
    with pytest.raises(RoomGone):
        svc.member_view(r.id, a.token)
    with pytest.raises(RoomGone):
        await svc.join(r.id, "b", KEY(2))
    assert svc.sweep() == 1 and svc.counts()["rooms"] == 0 and svc.sweep() == 0


async def test_restore_reopens_ports_and_skips_failures(tmp_path, clock):
    engine = make_engine(str(tmp_path / "s.db"))
    create_db_and_tables(engine)
    relay1 = FakeRelay()
    s1 = RoomService(engine, make_settings(RELAY_PORT_RANGE="30000-30009"), relay1, now=lambda: clock.t)
    r = s1.create_room("", 1, "ip")
    await s1.join(r.id, "a", KEY(1))
    await s1.join(r.id, "b", KEY(2))
    await s1.join(r.id, "c", KEY(3))
    ports = set(relay1.opened)
    relay2 = FakeRelay()
    relay2.fail_ports = {min(ports)}
    s2 = RoomService(engine, make_settings(RELAY_PORT_RANGE="30000-30009"), relay2, now=lambda: clock.t)
    assert await s2.restore() == 2 and set(relay2.opened) == ports - {min(ports)}


async def test_member_view_reports_relay_activity_and_bad_tokens(svc):
    r = svc.create_room("", 1, "ip")
    a = await svc.join(r.id, "a", KEY(1))
    await svc.join(r.id, "b", KEY(2))
    port = next(iter(svc.relay.opened))
    svc.relay.activity[port] = 2
    assert svc.member_view(r.id, a.token).peers[0].slots_active == 2
    with pytest.raises(Unauthorized):
        svc.member_view(r.id, "nope")
