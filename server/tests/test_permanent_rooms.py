"""Rooms with no expiry, and an account as a room's admin (the service's rules)."""

from datetime import timedelta

import pytest

from app.models import PERMANENT_EXPIRES, Account, utcnow
from app.rooms import Invalid, NotEntitled, RoomService, SignInRequired, TooManyPermanent, Unauthorized
from tests.conftest import FakeRelay, fake_locate, make_settings
from tests.test_rooms import KEY, clock, service  # noqa: F401 - clock is a fixture

ADMIN = Account(id=1, github_id=7, login="raub", created_at=utcnow(), last_login=utcnow())
USER = Account(id=2, github_id=8, login="stranger", created_at=utcnow(), last_login=utcnow())


@pytest.fixture
def svc(tmp_path, clock):  # noqa: F811
    return service(tmp_path, clock, ADMIN_GITHUB_IDS="7", ROOM_CREATE_PER_IP_PER_HOUR="9")


def permanent(svc, account=ADMIN, name="lab"):
    return svc.create_room(name, None, "1.1.1.1", account=account, permanent=True)


def test_only_an_admin_may_hold_permanent_rooms(svc):
    assert svc.may_hold_permanent(ADMIN) and not svc.may_hold_permanent(USER) and not svc.may_hold_permanent(None)


def test_permanent_needs_an_entitled_account(svc):
    with pytest.raises(SignInRequired):
        svc.create_room("", None, "1.1.1.1", permanent=True)
    with pytest.raises(NotEntitled):
        svc.create_room("", None, "1.1.1.1", account=USER, permanent=True)
    room = permanent(svc)
    assert room.permanent and room.account_id == 1 and room.expires_at == PERMANENT_EXPIRES and room.lifetime_hours == 0
    # a lifetime sent along is ignored, not an error
    assert svc.create_room("", 4, "1.1.1.1", account=ADMIN, permanent=True).expires_at == PERMANENT_EXPIRES


def test_refused_attempts_do_not_use_up_the_address_allowance(tmp_path, clock):  # noqa: F811
    s = service(tmp_path, clock, ADMIN_GITHUB_IDS="7", ROOM_CREATE_PER_IP_PER_HOUR="1")
    for _ in range(3):
        with pytest.raises(NotEntitled):
            s.create_room("", None, "1.1.1.1", account=USER, permanent=True)
    permanent(s)


def test_permanent_room_limit_per_account(tmp_path, clock):  # noqa: F811
    s = service(tmp_path, clock, ADMIN_GITHUB_IDS="7,9", PERMANENT_ROOMS_PER_ACCOUNT="2", ROOM_CREATE_PER_IP_PER_HOUR="9")
    first = permanent(s)
    permanent(s)
    with pytest.raises(TooManyPermanent):
        permanent(s)
    # the limit is each account's own, and timed rooms do not count toward it
    permanent(s, Account(id=3, github_id=9, login="other", created_at=utcnow(), last_login=utcnow()))
    s.create_room("", 1, "1.1.1.1", account=ADMIN)
    s.end_room(first.id, None, account_id=1)
    permanent(s)


def test_timed_room_still_needs_a_lifetime(svc, clock):  # noqa: F811
    with pytest.raises(Invalid):
        svc.create_room("", None, "1.1.1.1")
    with pytest.raises(Invalid):
        svc.create_room("", None, "1.1.1.1", account=ADMIN)
    room = svc.create_room("", 1, "1.1.1.1", account=ADMIN)
    assert room.account_id == 1 and not room.permanent and room.expires_at == clock.t + timedelta(hours=1)
    assert svc.create_room("", 1, "1.1.1.1").account_id is None


def test_account_is_admin_of_its_room_from_any_browser(svc):
    room = permanent(svc)
    assert svc.is_admin(room.id, "", account_id=1)
    assert not svc.is_admin(room.id, "", account_id=2) and not svc.is_admin(room.id, "")
    assert svc.admin_view(room.id, None, account_id=1).members == []
    with pytest.raises(Unauthorized):
        svc.admin_view(room.id, None, account_id=2)
    with pytest.raises(Unauthorized):
        svc.end_room(room.id, None)
    # a room nobody was signed in for has no account admin, whoever asks
    anonymous = svc.create_room("", 1, "1.1.1.1")
    assert not svc.is_admin(anonymous.id, "", account_id=1)
    with pytest.raises(Unauthorized):
        svc.end_room(anonymous.id, None, account_id=None)


async def test_owner_account_joins_at_once_others_wait(svc):
    room = permanent(svc)
    mine = await svc.join(room.id, "pc", KEY(1), account_id=1)
    assert mine.approved is True
    theirs = await svc.join(room.id, "phone", KEY(2), account_id=2)
    nobody = await svc.join(room.id, "tablet", KEY(3))
    assert theirs.approved is False and nobody.approved is False
    with pytest.raises(Unauthorized):
        await svc.approve(room.id, None, theirs.id, account_id=2)
    await svc.approve(room.id, None, theirs.id, account_id=1)
    assert [m.name for m in svc.room_view(room.id).members if m.approved] == ["pc", "phone"]
    svc.remove_member(room.id, None, nobody.id, account_id=1)


def test_permanent_room_is_not_extended(svc):
    room = permanent(svc)
    with pytest.raises(Invalid):
        svc.extend(room.id, None, None, account_id=1)
    with pytest.raises(Invalid):
        svc.extend(room.id, None, 24, account_id=1)
    assert svc.room_view(room.id).expires_at == PERMANENT_EXPIRES and svc.room_view(room.id).permanent
    timed = svc.create_room("", 1, "1.1.1.1", account=ADMIN)
    svc.extend(timed.id, None, 4, account_id=1)
    assert not svc.room_view(timed.id).permanent


async def test_permanent_room_survives_sweeps_and_restart(svc, clock):  # noqa: F811
    room = permanent(svc)
    await svc.join(room.id, "pc", KEY(1), account_id=1)
    await svc.join(room.id, "laptop", KEY(2), account_id=1)
    timed = svc.create_room("", 1, "1.1.1.1")
    port = next(iter(svc.relay.opened))
    clock.t += timedelta(days=4000)
    assert svc.sweep() == 1  # the timed room only
    assert [m.name for m in svc.room_view(room.id).members] == ["pc", "laptop"] and port in svc.relay.opened
    assert svc.counts()["rooms"] == 1 and timed.id != room.id
    relay2 = FakeRelay()
    restarted = RoomService(svc.engine, svc.settings, relay2, now=lambda: clock.t, locate=fake_locate)
    assert await restarted.restore() == 1 and relay2.opened == {port: room.id}


def test_account_rooms_listed_without_the_cookie(svc):
    room = permanent(svc)
    timed = svc.create_room("", 1, "1.1.1.1", account=ADMIN)
    svc.create_room("", 1, "1.1.1.1")
    listed = {r.id: r for r in svc.rooms_of_visitor("", account_id=1)}
    assert set(listed) == {room.id, timed.id}
    assert listed[room.id].is_admin and listed[room.id].permanent and not listed[timed.id].permanent
    assert svc.rooms_of_visitor("", account_id=2) == [] and svc.rooms_of_visitor("") == []


def test_dropped_admin_keeps_and_can_end_its_rooms(svc, clock):  # noqa: F811
    room = permanent(svc)
    later = RoomService(svc.engine, make_settings(ROOM_CREATE_PER_IP_PER_HOUR="9"), FakeRelay(), now=lambda: clock.t)
    with pytest.raises(NotEntitled):
        later.create_room("", None, "1.1.1.1", account=ADMIN, permanent=True)
    assert later.room_view(room.id).permanent and later.is_admin(room.id, "", account_id=1)
    later.end_room(room.id, None, account_id=1)
    assert later.counts()["rooms"] == 0


def test_an_account_room_has_no_other_admin(svc):
    """Made while signed in, a room is administered by the account and nothing else: not the owner
    token, not the cookie of the browser it was made in. Signing out of that browser lets go of it."""
    token = "t" * 22
    room = svc.create_room("lab", None, "1.1.1.1", visitor_token=token, account=ADMIN, permanent=True)
    browser = svc.visitor(token, "1.1.1.1")
    assert browser  # the browser is still recorded, for its own anonymous rooms
    assert not svc.is_admin(room.id, browser)
    with pytest.raises(Unauthorized):
        svc.admin_view(room.id, room.owner_token)
    with pytest.raises(Unauthorized):
        svc.end_room(room.id, room.owner_token, visitor_id=browser)
    assert svc.rooms_of_visitor(browser) == []
    svc.end_room(room.id, room.owner_token, visitor_id=browser, account_id=1)
    # an anonymous room keeps both of its admins
    anonymous = svc.create_room("", 1, "1.1.1.1", visitor_id=browser)
    assert svc.is_admin(anonymous.id, browser)
    svc.admin_view(anonymous.id, anonymous.owner_token)
