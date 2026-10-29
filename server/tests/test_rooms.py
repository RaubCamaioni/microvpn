import asyncio
import base64
from datetime import datetime, timedelta, timezone

import pytest

from app.models import create_db_and_tables, make_engine
from app.rooms import (
    CannotKick,
    DuplicateKey,
    Invalid,
    MemberNotFound,
    RateLimited,
    RoomFull,
    RoomGone,
    RoomService,
    ServerFull,
    TooManyRooms,
    Unauthorized,
)
from app.relay import MemberStats
from app.wgconfig import PRIVATE_KEY_PLACEHOLDER
from tests.conftest import FakeRelay, fake_locate, make_settings


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
    s = RoomService(
        engine, make_settings(**{"RELAY_PORT_RANGE": "30000-30009", **settings_over}), relay, now=lambda: clock.t,
        locate=fake_locate,
    )
    s.relay = relay
    return s


@pytest.fixture
def svc(tmp_path, clock):
    return service(tmp_path, clock)


async def admit(s, r, name, key):
    """Join and have the admin accept: the member is then connected to everyone already accepted."""
    m = await s.join(r.id, name, key)
    await s.approve(r.id, r.owner_token, m.id)
    return m


def names(s, room_id, approved=True):
    return [m.name for m in s.room_view(room_id).members if m.approved == approved]


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
    a = await admit(svc, r, "alice", KEY(1))
    b = await admit(svc, r, "bob", KEY(2))
    c = await admit(svc, r, "carol", KEY(3))
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
    await admit(svc, r, "alice", KEY(1))
    with pytest.raises(DuplicateKey):
        await admit(svc, r, "alice2", KEY(1))
    with pytest.raises(Invalid):
        await admit(svc, r, "bad\nname", KEY(2))
    with pytest.raises(Invalid):
        await admit(svc, r, "ok", "not-a-key")
    with pytest.raises(RoomGone):
        await svc.join("z" * 26, "ok", KEY(2))
    with pytest.raises(RoomGone):
        await svc.join("not an id", "ok", KEY(2))


async def test_room_full(tmp_path, clock):
    s = service(tmp_path, clock, "f.db", ROOM_MAX_MEMBERS="2")
    r = s.create_room("", 1, "ip")
    await admit(s, r, "a", KEY(1))
    await admit(s, r, "b", KEY(2))
    with pytest.raises(RoomFull):
        await admit(s, r, "c", KEY(3))


async def test_join_rolls_back_when_ports_run_out(tmp_path, clock):
    s = service(tmp_path, clock, "p.db", RELAY_PORT_RANGE="30000-30001")
    r = s.create_room("", 1, "ip")
    await admit(s, r, "a", KEY(1))
    await admit(s, r, "b", KEY(2))  # one pair, one of the two ports
    ab_port = set(s.relay.opened)
    with pytest.raises(ServerFull):
        await admit(s, r, "c", KEY(3))  # needs two ports, one is free
    assert names(s, r.id) == ["a", "b"] and names(s, r.id, approved=False) == ["c"]  # c stays pending
    assert set(s.relay.opened) == ab_port and len(s.relay.closed) == 1  # the port c briefly took was closed


async def test_leave_and_end_room_release_ports(svc):
    r = svc.create_room("", 1, "ip")
    a = await admit(svc, r, "a", KEY(1))
    b = await admit(svc, r, "b", KEY(2))
    await admit(svc, r, "c", KEY(3))
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
    a = await admit(svc, r, "a", KEY(1))
    clock.t += timedelta(hours=1, seconds=1)
    with pytest.raises(RoomGone):
        svc.room_view(r.id)
    with pytest.raises(RoomGone):
        svc.member_view(r.id, a.token)
    with pytest.raises(RoomGone):
        await admit(svc, r, "b", KEY(2))
    assert svc.sweep() == 1 and svc.counts()["rooms"] == 0 and svc.sweep() == 0


async def test_restore_reopens_ports_and_skips_failures(tmp_path, clock):
    engine = make_engine(str(tmp_path / "s.db"))
    create_db_and_tables(engine)
    relay1 = FakeRelay()
    s1 = RoomService(engine, make_settings(RELAY_PORT_RANGE="30000-30009"), relay1, now=lambda: clock.t)
    r = s1.create_room("", 1, "ip")
    await admit(s1, r, "a", KEY(1))
    await admit(s1, r, "b", KEY(2))
    await admit(s1, r, "c", KEY(3))
    ports = set(relay1.opened)
    relay2 = FakeRelay()
    relay2.fail_ports = {min(ports)}
    s2 = RoomService(engine, make_settings(RELAY_PORT_RANGE="30000-30009"), relay2, now=lambda: clock.t)
    assert await s2.restore() == 2 and set(relay2.opened) == ports - {min(ports)}


async def test_concurrent_joins_get_distinct_addresses_and_pairs(tmp_path, clock):
    s = service(tmp_path, clock, "c.db")
    plain_open = s.relay.open

    async def slow_open(port, room_id, keys=None):
        await asyncio.sleep(0.01)  # yield to the other joins, as a real bind and the loop would
        return await plain_open(port, room_id, keys)

    s.relay.open = slow_open
    r = s.create_room("", 1, "ip")
    members = await asyncio.gather(*(admit(s, r, f"m{i}", KEY(i)) for i in range(1, 5)))
    assert len({m.address for m in members}) == 4
    assert len(s.relay.opened) == 6 and len({m.token for m in members}) == 4
    assert s.member_view(r.id, members[0].token).config_template.count("[Peer]") == 3


async def test_join_closes_its_ports_on_any_failure(tmp_path, clock):
    s = service(tmp_path, clock, "x.db")
    r = s.create_room("", 1, "ip")
    await admit(s, r, "a", KEY(1))
    await admit(s, r, "b", KEY(2))
    plain_open = s.relay.open
    calls = []

    async def flaky_open(port, room_id, keys=None):
        calls.append(port)
        if len(calls) == 2:
            raise RuntimeError("bind exploded")
        return await plain_open(port, room_id, keys)

    s.relay.open = flaky_open
    with pytest.raises(RuntimeError):
        await admit(s, r, "c", KEY(3))
    assert names(s, r.id) == ["a", "b"] and names(s, r.id, approved=False) == ["c"]
    assert len(s.relay.opened) == 1 and s.relay.closed == [calls[0]]


async def test_relay_gets_the_pair_public_keys_on_join_and_restore(svc):
    r = svc.create_room("", 1, "ip")
    await admit(svc, r, "a", KEY(1))
    await admit(svc, r, "b", KEY(2))
    assert len(svc.relay.keys) == 1 and set(next(iter(svc.relay.keys.values()))) == {KEY(1), KEY(2)}
    relay2 = FakeRelay()
    s2 = RoomService(svc.engine, svc.settings, relay2, now=svc.now)
    assert await s2.restore() == 1
    assert len(relay2.keys) == 1 and set(next(iter(relay2.keys.values()))) == {KEY(1), KEY(2)}


async def test_member_view_reports_relay_activity_and_bad_tokens(svc):
    r = svc.create_room("", 1, "ip")
    a = await admit(svc, r, "a", KEY(1))
    await admit(svc, r, "b", KEY(2))
    port = next(iter(svc.relay.opened))
    svc.relay.activity[port] = 2
    assert svc.member_view(r.id, a.token).peers[0].slots_active == 2
    with pytest.raises(Unauthorized):
        svc.member_view(r.id, "nope")


async def test_joins_wait_for_the_admin(svc):
    r = svc.create_room("", 1, "ip")
    a = await admit(svc, r, "admin-pc", KEY(1))
    b = await svc.join(r.id, "stranger", KEY(2))
    assert names(svc, r.id) == ["admin-pc"] and names(svc, r.id, approved=False) == ["stranger"]
    assert svc.relay.opened == {}  # no pair until accepted
    pending = svc.member_view(r.id, b.token)
    assert pending.approved is False and pending.peers == [] and pending.config_template == ""
    assert svc.member_view(r.id, a.token).peers == []  # the admin does not see the stranger as a peer yet
    await svc.approve(r.id, r.owner_token, b.id)
    assert names(svc, r.id) == ["admin-pc", "stranger"] and len(svc.relay.opened) == 1
    assert svc.member_view(r.id, b.token).approved
    assert [p.name for p in svc.member_view(r.id, a.token).peers] == ["stranger"]
    await svc.approve(r.id, r.owner_token, b.id)  # a second approval changes nothing
    assert len(svc.relay.opened) == 1


async def test_admin_token_on_join_accepts_immediately(svc):
    r = svc.create_room("", 1, "ip")
    a = await svc.join(r.id, "admin-pc", KEY(1), owner_token=r.owner_token)
    assert names(svc, r.id) == ["admin-pc"] and svc.member_view(r.id, a.token).approved
    with pytest.raises(Unauthorized):
        await svc.join(r.id, "x", KEY(2), owner_token="wrong")


async def test_only_the_admin_approves_rejects_or_removes(svc):
    r = svc.create_room("", 1, "ip")
    a = await admit(svc, r, "a", KEY(1))
    b = await svc.join(r.id, "b", KEY(2))
    with pytest.raises(Unauthorized):
        await svc.approve(r.id, a.token, b.id)
    with pytest.raises(Unauthorized):
        svc.remove_member(r.id, "nope", b.id)
    with pytest.raises(MemberNotFound):
        await svc.approve(r.id, r.owner_token, 9999)
    svc.remove_member(r.id, r.owner_token, b.id)  # reject
    assert names(svc, r.id, approved=False) == []
    with pytest.raises(Unauthorized):
        svc.member_view(r.id, b.token)  # the rejected device is gone
    c = await admit(svc, r, "c", KEY(3))
    assert len(svc.relay.opened) == 1
    with pytest.raises(CannotKick):  # an accepted member is not kicked: end the room and make a new one
        svc.remove_member(r.id, r.owner_token, c.id)
    assert names(svc, r.id) == ["a", "c"] and len(svc.relay.opened) == 1


async def test_pending_members_count_toward_the_room_size(tmp_path, clock):
    s = service(tmp_path, clock, "pend.db", ROOM_MAX_MEMBERS="2")
    r = s.create_room("", 1, "ip")
    await admit(s, r, "a", KEY(1))
    await s.join(r.id, "b", KEY(2))
    with pytest.raises(RoomFull):
        await s.join(r.id, "c", KEY(3))


async def test_admin_view_shows_alias_address_and_location(svc):
    r = svc.create_room("", 1, "ip")
    a = await svc.join(r.id, "admin-pc", KEY(1), owner_token=r.owner_token, client_ip="203.0.113.7")
    b = await svc.join(r.id, "phone", KEY(2), client_ip="198.51.100.9")
    with pytest.raises(Unauthorized):
        svc.admin_view(r.id, a.token)
    view = svc.admin_view(r.id, r.owner_token)
    rows = [(m.name, m.approved, m.joined_ip, m.location) for m in view.members]
    assert rows == [("admin-pc", True, "203.0.113.7", "Testville, 203.0.113.7"), ("phone", False, "198.51.100.9", "Testville, 198.51.100.9")]
    assert [m.id for m in view.members] == [a.id, b.id]
    # the public view never carries addresses or places
    assert not hasattr(svc.room_view(r.id).members[0], "joined_ip")


def test_extend_resets_the_lifetime_from_now_for_the_admin_only(svc, clock):
    r = svc.create_room("", 4, "ip")
    clock.t += timedelta(hours=3)
    with pytest.raises(Unauthorized):
        svc.extend(r.id, "wrong", None)
    with pytest.raises(Invalid):
        svc.extend(r.id, r.owner_token, 5)
    assert svc.extend(r.id, r.owner_token, None) == clock.t + timedelta(hours=4)  # the room's own lifetime
    assert svc.extend(r.id, r.owner_token, 1) == clock.t + timedelta(hours=1)  # or a chosen one
    assert svc.room_view(r.id).expires_at == clock.t + timedelta(hours=1)
    assert svc.rooms_of_visitor("")  == []


async def test_room_view_reports_connection_and_transfer_per_member(svc):
    r = svc.create_room("", 1, "ip")
    a = await admit(svc, r, "a", KEY(1))
    b = await admit(svc, r, "b", KEY(2))
    c = await admit(svc, r, "c", KEY(3))
    ports = {tuple(sorted(v)): k for k, v in svc.relay.keys.items()}
    up, trying, idle = MemberStats(True, True, 0), MemberStats(False, True, 0), MemberStats(False, False, 0)
    svc.relay.stats[ports[tuple(sorted((KEY(1), KEY(2))))]] = {KEY(1): up._replace(bytes=1000), KEY(2): up._replace(bytes=50)}
    svc.relay.stats[ports[tuple(sorted((KEY(1), KEY(3))))]] = {KEY(1): up._replace(bytes=24), KEY(3): trying._replace(bytes=148)}
    view = {m.name: (m.active, m.handshaking, m.bytes) for m in svc.room_view(r.id).members}
    # summed over a member's pairs; c only sends handshakes that never complete (its keys do not match)
    assert view == {"a": (True, False, 1024), "b": (True, False, 50), "c": (False, True, 148)}
    svc.relay.stats[ports[tuple(sorted((KEY(1), KEY(3))))]] = {KEY(1): up._replace(bytes=24), KEY(3): idle}
    assert [(m.active, m.handshaking) for m in svc.room_view(r.id).members if m.name == "c"] == [(False, False)]
    d = await svc.join(r.id, "d", KEY(4))  # pending: no pairs, nothing to report
    assert [(m.name, m.active, m.bytes) for m in svc.room_view(r.id).members][-1] == ("d", False, 0)


async def test_released_ports_are_retired_until_the_room_ends(tmp_path, clock):
    s = service(tmp_path, clock, "retire.db", RELAY_PORT_RANGE="30000-30002")  # three ports in all
    r = s.create_room("", 1, "ip")
    a = await admit(s, r, "a", KEY(1))
    b = await admit(s, r, "b", KEY(2))
    first = set(s.relay.opened)
    s.leave(r.id, b.token)  # frees the (a, b) port: the device that left still points at it
    assert s.relay.opened == {}
    c = await admit(s, r, "c", KEY(3))
    assert not (set(s.relay.opened) & first)  # the (a, c) pair got a different port
    with pytest.raises(ServerFull):  # (a, d) and (c, d) need two ports; one unretired port is left
        await admit(s, r, "d", KEY(4))
    # a second service on the same database (a restart) still refuses the retired port
    s2 = RoomService(s.engine, s.settings, FakeRelay(), now=lambda: clock.t, locate=fake_locate)
    await s2.restore()
    s2.leave(r.id, c.token)  # a second retired port
    e = await s2.join(r.id, "e", KEY(5), owner_token=r.owner_token)  # (a, e) takes the one port left
    assert set(s2.relay.opened) == {30000, 30001, 30002} - first - {next(iter(set(s2.relay.closed)))}
    with pytest.raises(ServerFull):  # (a, f) and (e, f): nothing left, retired ports stay retired
        await s2.join(r.id, "f", KEY(6), owner_token=r.owner_token)
    s2.end_room(r.id, r.owner_token)
    r2 = s2.create_room("", 1, "ip")  # the room ended: its retired ports are free again
    await s2.join(r2.id, "p", KEY(7), owner_token=r2.owner_token)
    await s2.join(r2.id, "q", KEY(8), owner_token=r2.owner_token)
    assert len([p for p, room in s2.relay.opened.items() if room == r2.id]) == 1
