import pytest

from app.relay import TokenBucket, packet_kind

LENGTHS = {1: 148, 2: 92, 3: 64, 4: 32}


def make_packet(kind, length=None):
    return bytes([kind, 0, 0, 0]) + b"\x01" * ((length or LENGTHS[kind]) - 4)


@pytest.mark.parametrize("kind", [1, 2, 3, 4])
def test_well_formed_packets_are_classified(kind):
    assert packet_kind(make_packet(kind)) == kind


def test_transport_data_may_be_longer_than_32():
    assert packet_kind(make_packet(4, 1500)) == 4


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\x01\x00\x00",
        bytes([5, 0, 0, 0]) + b"x" * 60,
        bytes([1, 1, 0, 0]) + b"x" * 144,
        make_packet(1, 147),
        make_packet(2, 93),
        make_packet(3, 60),
        make_packet(4, 31),
        b"GET / HTTP/1.1\r\n" * 4,
    ],
)
def test_malformed_packets_are_rejected(data):
    assert packet_kind(data) is None


def test_token_bucket_refills_over_time():
    t = [0.0]
    b = TokenBucket(rate_bytes_per_s=1000, burst_bytes=1000, clock=lambda: t[0])
    assert b.take(600) and b.take(400) and not b.take(1)
    t[0] = 0.5
    assert b.take(500) and not b.take(1)
    t[0] = 10.0
    assert b.take(1000) and not b.take(1)  # the burst caps what accumulates


# --- forwarding, with real UDP sockets on localhost ---------------------------------------------
# A slot is claimed only by a handshake whose mac1 was computed with one of the pair's public keys,
# exactly as a real WireGuard client computes it; junk and spoofed packets never claim a slot.

import asyncio  # noqa: E402
import base64  # noqa: E402
import hashlib  # noqa: E402
import socket  # noqa: E402

from app.relay import Relay, mac1_valid  # noqa: E402
from app.settings import Settings  # noqa: E402

KA = base64.b64encode(bytes([1] * 32)).decode()
KB = base64.b64encode(bytes([2] * 32)).decode()
KX = base64.b64encode(bytes([9] * 32)).decode()  # not in the pair


def handshake(kind, pubkey_b64, filler=0x01):
    """A handshake message (type 1 or 2) whose mac1 is valid for the receiver whose public key is
    `pubkey_b64`: mac1 = keyed_blake2s(key=blake2s("mac1----" || pubkey), message[:-32])[:16],
    followed by an all-zero mac2 (no cookie)."""
    body = bytes([kind, 0, 0, 0]) + bytes([filler]) * (LENGTHS[kind] - 4 - 32)
    key = hashlib.blake2s(b"mac1----" + base64.b64decode(pubkey_b64), digest_size=32).digest()
    mac1 = hashlib.blake2s(body, digest_size=16, key=key).digest()
    return body + mac1 + bytes(16)


def test_mac1_valid_accepts_either_key_of_the_pair_and_rejects_others():
    assert mac1_valid(handshake(1, KA), (KA, KB)) and mac1_valid(handshake(2, KB), (KA, KB))
    assert not mac1_valid(handshake(1, KX), (KA, KB))
    assert not mac1_valid(make_packet(1), (KA, KB))  # no mac1 at all


def udp_client():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    s.setblocking(False)
    return s


async def recv(sock, timeout=1.0):
    return await asyncio.wait_for(asyncio.get_running_loop().sock_recv(sock, 2048), timeout)


def relay_settings(**over):
    base = {"DATABASE": "x", "RELAY_BIND": "127.0.0.1", "RELAY_PORT_RANGE": "30000-30099"}
    return Settings.from_environment({**base, **over})


async def test_forwards_between_the_two_learned_addresses():
    relay = Relay(relay_settings())
    assert await relay.open(30001, "room", (KA, KB)) and relay.is_open(30001) and relay.ports_in_use() == 1
    a, b = udp_client(), udp_client()
    a.sendto(handshake(1, KB), ("127.0.0.1", 30001))  # a, for b's key: claims slot 1, nobody to forward to
    b.sendto(handshake(2, KA), ("127.0.0.1", 30001))  # b, for a's key: claims slot 2, forwarded to a
    assert await recv(a) == handshake(2, KA)
    a.sendto(make_packet(4, 100), ("127.0.0.1", 30001))
    assert await recv(b) == make_packet(4, 100)
    assert relay.slots_active(30001) == 2 and relay.slots_active(30002) == 0
    relay.close(30001)
    await asyncio.sleep(0)  # the close runs on the loop
    assert not relay.is_open(30001) and relay.ports_in_use() == 0


async def test_open_returns_false_for_an_already_open_port():
    relay = Relay(relay_settings())
    assert await relay.open(30009, "room", (KA, KB))
    assert await relay.open(30009, "other-room", (KA, KX)) is False  # never hand one port to two pairs
    relay.close(30009)


async def test_unknown_addresses_cannot_claim_a_slot_without_a_valid_mac1():
    relay = Relay(relay_settings())
    await relay.open(30002, "room", (KA, KB))
    a, b, c = udp_client(), udp_client(), udp_client()
    c.sendto(make_packet(4, 64), ("127.0.0.1", 30002))  # data from nobody: no slot
    c.sendto(make_packet(1), ("127.0.0.1", 30002))  # handshake-shaped junk: no slot
    c.sendto(handshake(1, KX), ("127.0.0.1", 30002))  # handshake for a key outside the pair: no slot
    await asyncio.sleep(0.05)
    assert relay.slots_active(30002) == 0
    a.sendto(handshake(1, KB), ("127.0.0.1", 30002))
    b.sendto(handshake(2, KA), ("127.0.0.1", 30002))
    await recv(a)
    b.sendto(b"junk", ("127.0.0.1", 30002))  # malformed: dropped
    a.sendto(make_packet(4), ("127.0.0.1", 30002))
    assert await recv(b) == make_packet(4)
    with pytest.raises(asyncio.TimeoutError):
        await recv(c, 0.3)
    relay.close(30002)


async def test_handshake_from_a_new_address_replaces_the_stale_slot():
    t = [100.0]
    relay = Relay(relay_settings(), clock=lambda: t[0])
    await relay.open(30003, "room", (KA, KB))
    a, b, a2, x = udp_client(), udp_client(), udp_client(), udp_client()
    a.sendto(handshake(1, KB), ("127.0.0.1", 30003))
    t[0] = 101.0
    b.sendto(handshake(2, KA), ("127.0.0.1", 30003))
    await recv(a)
    t[0] = 140.0
    x.sendto(make_packet(1), ("127.0.0.1", 30003))  # handshake-shaped junk: both slots keep their owners
    await asyncio.sleep(0.05)
    a2.sendto(handshake(1, KB), ("127.0.0.1", 30003))  # a roamed: the least recently seen slot (a) is replaced
    assert await recv(b) == handshake(1, KB)
    b.sendto(make_packet(4), ("127.0.0.1", 30003))
    assert await recv(a2) == make_packet(4)
    with pytest.raises(asyncio.TimeoutError):
        await recv(x, 0.3)
    assert relay.slots_active(30003, window=30.0) == 2  # both seen within 30 s of t=140
    t[0] = 200.0
    assert relay.slots_active(30003, window=30.0) == 0
    relay.close(30003)


async def test_room_bucket_drops_over_budget_and_close_room_frees_ports():
    relay = Relay(relay_settings(ROOM_MBPS="0.008"))  # 1000 bytes per second, 1000-byte burst
    await relay.open(30004, "r1", (KA, KB))
    await relay.open(30005, "r1", (KA, KX))
    await relay.open(30006, "r2", (KB, KX))
    a, b = udp_client(), udp_client()
    a.sendto(handshake(1, KB), ("127.0.0.1", 30004))
    b.sendto(handshake(2, KA), ("127.0.0.1", 30004))
    await recv(a)
    a.sendto(make_packet(4, 900), ("127.0.0.1", 30004))
    assert await recv(b) == make_packet(4, 900)
    a.sendto(make_packet(4, 900), ("127.0.0.1", 30004))  # bucket has ~8 bytes left: dropped
    with pytest.raises(asyncio.TimeoutError):
        await recv(b, 0.3)
    relay.close_room("r1")
    await asyncio.sleep(0)
    assert not relay.is_open(30004) and not relay.is_open(30005) and relay.is_open(30006)
    relay.close_room("r2")


async def test_open_returns_false_when_the_port_is_taken():
    holder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    holder.bind(("127.0.0.1", 30007))
    relay = Relay(relay_settings())
    assert await relay.open(30007, "room", (KA, KB)) is False and not relay.is_open(30007)
    holder.close()


async def test_close_from_another_thread_releases_the_port_on_the_loop():
    relay = Relay(relay_settings())
    assert await relay.open(30008, "room", (KA, KB))
    await asyncio.to_thread(relay.close, 30008)  # sync endpoints and the sweeper run in worker threads
    await asyncio.sleep(0.05)
    assert not relay.is_open(30008)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 30008))  # free again
    s.close()


async def test_member_stats_attribute_traffic_to_the_member_that_sent_it():
    t = [100.0]
    relay = Relay(relay_settings(), clock=lambda: t[0])
    await relay.open(30010, "room", (KA, KB))
    assert relay.member_stats(30010) == {KA: (False, 0), KB: (False, 0)}
    a, b = udp_client(), udp_client()
    a.sendto(handshake(1, KB), ("127.0.0.1", 30010))  # mac1 for B's key: the sender is A
    await asyncio.sleep(0.05)
    assert relay.member_stats(30010) == {KA: (True, 148), KB: (False, 0)}
    b.sendto(handshake(2, KA), ("127.0.0.1", 30010))  # mac1 for A's key: the sender is B
    await recv(a)
    a.sendto(make_packet(4, 200), ("127.0.0.1", 30010))
    await recv(b)
    stats = relay.member_stats(30010)
    assert stats[KA] == (True, 348) and stats[KB] == (True, 92)
    t[0] = 150.0  # 50 s later nobody has sent: both idle, the counts stay
    assert relay.member_stats(30010, window=40.0) == {KA: (False, 348), KB: (False, 92)}
    assert relay.member_stats(30099) == {}
    relay.close(30010)


async def test_takeover_replaces_only_the_claimed_members_slot():
    t = [100.0]
    relay = Relay(relay_settings(), clock=lambda: t[0])
    await relay.open(30011, "room", (KA, KB))
    a, b, x = udp_client(), udp_client(), udp_client()
    b.sendto(handshake(2, KA), ("127.0.0.1", 30011))  # B claims its slot first
    t[0] = 101.0
    a.sendto(handshake(1, KB), ("127.0.0.1", 30011))  # then A: B's slot is now the stalest
    await recv(b)
    t[0] = 102.0
    x.sendto(handshake(1, KB), ("127.0.0.1", 30011))  # x claims to be A (addressed to B): it may replace A's slot only
    assert await recv(b) == handshake(1, KB)
    b.sendto(make_packet(4, 64), ("127.0.0.1", 30011))  # B's reply goes to the claimed A, which is now x
    assert await recv(x) == make_packet(4, 64)
    a.sendto(make_packet(4, 80), ("127.0.0.1", 30011))  # the real A lost its slot: dropped until it handshakes again
    with pytest.raises(asyncio.TimeoutError):
        await recv(b, 0.3)
    stats = relay.member_stats(30011)
    assert stats[KB][0] is True  # B was never displaced
    relay.close(30011)
