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

import asyncio  # noqa: E402
import socket  # noqa: E402

from app.relay import Relay  # noqa: E402
from app.settings import Settings  # noqa: E402


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
    assert await relay.open(30001, "room") and relay.is_open(30001) and relay.ports_in_use() == 1
    a, b = udp_client(), udp_client()
    a.sendto(make_packet(1), ("127.0.0.1", 30001))  # first address: claims slot 1, nobody to forward to
    b.sendto(make_packet(2), ("127.0.0.1", 30001))  # second: claims slot 2, forwarded to a
    assert await recv(a) == make_packet(2)
    a.sendto(make_packet(4, 100), ("127.0.0.1", 30001))
    assert await recv(b) == make_packet(4, 100)
    assert relay.slots_active(30001) == 2 and relay.slots_active(30002) == 0
    relay.close(30001)
    assert not relay.is_open(30001) and relay.ports_in_use() == 0


async def test_malformed_and_third_party_data_are_dropped():
    relay = Relay(relay_settings())
    await relay.open(30002, "room")
    a, b, c = udp_client(), udp_client(), udp_client()
    a.sendto(make_packet(1), ("127.0.0.1", 30002))
    b.sendto(make_packet(2), ("127.0.0.1", 30002))
    await recv(a)
    c.sendto(make_packet(4, 64), ("127.0.0.1", 30002))  # unknown address, data packet: dropped, slots unchanged
    b.sendto(b"junk", ("127.0.0.1", 30002))  # malformed: dropped
    a.sendto(make_packet(4), ("127.0.0.1", 30002))
    assert await recv(b) == make_packet(4)
    with pytest.raises(asyncio.TimeoutError):
        await recv(c, 0.3)
    relay.close(30002)


async def test_handshake_from_a_new_address_replaces_the_stale_slot():
    t = [100.0]
    relay = Relay(relay_settings(), clock=lambda: t[0])
    await relay.open(30003, "room")
    a, b, a2 = udp_client(), udp_client(), udp_client()
    a.sendto(make_packet(1), ("127.0.0.1", 30003))
    t[0] = 101.0
    b.sendto(make_packet(2), ("127.0.0.1", 30003))
    await recv(a)
    t[0] = 140.0
    a2.sendto(make_packet(1), ("127.0.0.1", 30003))  # a roamed: least recently seen slot (a) is replaced
    assert await recv(b) == make_packet(1)
    b.sendto(make_packet(4), ("127.0.0.1", 30003))
    assert await recv(a2) == make_packet(4)
    assert relay.slots_active(30003, window=30.0) == 2  # both seen within 30 s of t=140
    t[0] = 200.0
    assert relay.slots_active(30003, window=30.0) == 0
    relay.close(30003)


async def test_room_bucket_drops_over_budget_and_close_room_frees_ports():
    relay = Relay(relay_settings(ROOM_MBPS="0.008"))  # 1000 bytes per second, 1000-byte burst
    await relay.open(30004, "r1")
    await relay.open(30005, "r1")
    await relay.open(30006, "r2")
    a, b = udp_client(), udp_client()
    a.sendto(make_packet(1), ("127.0.0.1", 30004))
    b.sendto(make_packet(2), ("127.0.0.1", 30004))
    await recv(a)
    a.sendto(make_packet(4, 900), ("127.0.0.1", 30004))
    assert await recv(b) == make_packet(4, 900)
    a.sendto(make_packet(4, 900), ("127.0.0.1", 30004))  # bucket has ~8 bytes left: dropped
    with pytest.raises(asyncio.TimeoutError):
        await recv(b, 0.3)
    relay.close_room("r1")
    assert not relay.is_open(30004) and not relay.is_open(30005) and relay.is_open(30006)
    relay.close_room("r2")


async def test_open_returns_false_when_the_port_is_taken():
    holder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    holder.bind(("127.0.0.1", 30007))
    relay = Relay(relay_settings())
    assert await relay.open(30007, "room") is False and not relay.is_open(30007)
    holder.close()
