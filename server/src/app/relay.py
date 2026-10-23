"""The blind UDP relay.

One datagram endpoint per member pair. Each port learns at most two addresses (slots) from the
packets that reach it and forwards whatever one slot sends to the other, unchanged. The server
has no WireGuard keys, so everything that passes is ciphertext it cannot read. See the spec's
"The relay" section for the slot and rate rules implemented here.
"""

import asyncio
import base64
import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from typing import Callable

from app.settings import Settings

logger = logging.getLogger("uvicorn.error")

# a pair's two public keys, base64 as in the configs
PairKeys = tuple[str, str]

HANDSHAKE_INITIATION = 1
HANDSHAKE_RESPONSE = 2
COOKIE_REPLY = 3
TRANSPORT_DATA = 4

# exact lengths of the fixed-size WireGuard messages; transport data is 32 bytes or more
_EXACT_LENGTHS = {HANDSHAKE_INITIATION: 148, HANDSHAKE_RESPONSE: 92, COOKIE_REPLY: 64}
_MIN_LENGTH = 32


def packet_kind(data: bytes) -> int | None:
    """The WireGuard message type of `data`, or None when it is not shaped like WireGuard:
    at least 32 bytes, type byte 1..4, the three reserved bytes zero, the length right for the type."""
    if len(data) < _MIN_LENGTH:
        return None
    kind = data[0]
    if kind not in (HANDSHAKE_INITIATION, HANDSHAKE_RESPONSE, COOKIE_REPLY, TRANSPORT_DATA):
        return None
    if data[1] != 0 or data[2] != 0 or data[3] != 0:
        return None
    exact = _EXACT_LENGTHS.get(kind)
    if exact is not None and len(data) != exact:
        return None
    return kind


def _mac1_key(pubkey_b64: str) -> bytes:
    return hashlib.blake2s(b"mac1----" + base64.b64decode(pubkey_b64), digest_size=32).digest()


def mac1_receiver(data: bytes, keys: PairKeys) -> int | None:
    """Which of the pair's members a handshake message is addressed to (0 or 1), or None when its
    mac1 matches neither: mac1 = keyed_blake2s(blake2s("mac1----" || receiver_pubkey), msg[:-32])[:16],
    as a real WireGuard peer computes it. Only a client that has a member's public key, which the room
    link gives, can produce it: junk never claims a slot. The sender is the other member."""
    if packet_kind(data) not in (HANDSHAKE_INITIATION, HANDSHAKE_RESPONSE):
        return None
    body, mac1 = data[:-32], data[-32:-16]
    for index, key in enumerate(keys):
        try:
            expected = hashlib.blake2s(body, digest_size=16, key=_mac1_key(key)).digest()
        except (ValueError, TypeError):
            continue
        if hmac.compare_digest(expected, mac1):
            return index
    return None


def mac1_valid(data: bytes, keys: PairKeys) -> bool:
    return mac1_receiver(data, keys) is not None


class TokenBucket:
    """Bytes per second with a burst: take(n) is True and spends n tokens when they are available."""

    def __init__(self, rate_bytes_per_s: float, burst_bytes: float, clock: Callable[[], float] = time.monotonic):
        self.rate = float(rate_bytes_per_s)
        self.burst = float(burst_bytes)
        self.clock = clock
        self.tokens = self.burst
        self.updated = clock()

    def take(self, n: int) -> bool:
        now = self.clock()
        if now > self.updated:
            self.tokens = min(self.burst, self.tokens + (now - self.updated) * self.rate)
            self.updated = now
        if n <= self.tokens:
            self.tokens -= n
            return True
        return False


@dataclass
class Slot:
    addr: tuple
    last_seen: float
    member: int  # 0 or 1: which of the pair's members sends from this address


class PairPort(asyncio.DatagramProtocol):
    """One relay port: two learned addresses, packets from one forwarded to the other."""

    def __init__(
        self,
        room_id: str,
        keys: PairKeys,
        room_bucket: TokenBucket,
        global_bucket: TokenBucket,
        clock: Callable[[], float],
    ):
        self.room_id = room_id
        self.keys = keys
        self.room_bucket = room_bucket
        self.global_bucket = global_bucket
        self.clock = clock
        self.slots: list[Slot] = []
        self.bytes_from = [0, 0]  # ciphertext bytes each member has sent through this port
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple):
        kind = packet_kind(data)
        if kind is None:
            return
        now = self.clock()
        sender = next((s for s in self.slots if s.addr == addr), None)
        if sender is not None:
            sender.last_seen = now
        else:
            receiver = mac1_receiver(data, self.keys)
            if receiver is None:
                return  # an address that holds no slot claims one only with a handshake for this pair
            member = 1 - receiver
            if len(self.slots) < 2:
                sender = Slot(addr, now, member)
                self.slots.append(sender)
            else:
                # a genuine handshake from a new address: the client roamed; it takes over the stalest slot
                sender = min(self.slots, key=lambda s: s.last_seen)
                sender.addr, sender.last_seen, sender.member = addr, now, member
        self.bytes_from[sender.member] += len(data)
        other = next((s for s in self.slots if s is not sender), None)
        if other is None or self.transport is None:
            return
        if self.room_bucket.take(len(data)) and self.global_bucket.take(len(data)):
            self.transport.sendto(data, other.addr)

    def active_slots(self, window: float) -> int:
        now = self.clock()
        return sum(1 for s in self.slots if now - s.last_seen <= window)

    def member_stats(self, window: float) -> dict[str, tuple[bool, int]]:
        """Per public key: whether that member sent anything within `window` seconds, and its bytes so far."""
        now = self.clock()
        stats = {}
        for index, key in enumerate(self.keys):
            active = any(s.member == index and now - s.last_seen <= window for s in self.slots)
            stats[key] = (active, self.bytes_from[index])
        return stats


class Relay:
    """Opens and closes pair ports and keeps the per-room and server-wide buckets."""

    def __init__(self, settings: Settings, clock: Callable[[], float] = time.monotonic):
        self.settings = settings
        self.clock = clock
        self._ports: dict[int, tuple[asyncio.DatagramTransport, PairPort]] = {}
        self._room_buckets: dict[str, TokenBucket] = {}
        self._global_bucket = self._bucket(settings.RELAY_TOTAL_MBPS)
        self._loop: asyncio.AbstractEventLoop | None = None

    def _bucket(self, mbps: float) -> TokenBucket:
        rate = mbps * 1e6 / 8  # bytes per second, with a burst of one second
        return TokenBucket(rate_bytes_per_s=rate, burst_bytes=rate, clock=self.clock)

    def _room_bucket(self, room_id: str) -> TokenBucket:
        if room_id not in self._room_buckets:
            self._room_buckets[room_id] = self._bucket(self.settings.ROOM_MBPS)
        return self._room_buckets[room_id]

    async def open(self, port: int, room_id: str, keys: PairKeys) -> bool:
        """Bind the port for the pair of `room_id` whose members hold `keys`. False when the port is
        already in use, by this relay or anything else (with a warning for the latter)."""
        if port in self._ports:
            return False
        loop = asyncio.get_running_loop()
        self._loop = loop
        try:
            transport, protocol = await loop.create_datagram_endpoint(
                lambda: PairPort(room_id, keys, self._room_bucket(room_id), self._global_bucket, self.clock),
                local_addr=(self.settings.RELAY_BIND, port),
            )
        except OSError as e:
            logger.warning("relay: cannot bind udp port %d: %s", port, e)
            return False
        self._ports[port] = (transport, protocol)
        return True

    def close(self, port: int) -> None:
        """Forget the port now and close its transport on the event loop: callers include the sweeper
        and sync endpoints, which run in worker threads, and transports are not thread-safe."""
        entry = self._ports.pop(port, None)
        if entry is None:
            return
        transport = entry[0]
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(transport.close)
        else:
            transport.close()

    def close_room(self, room_id: str) -> None:
        for port in [p for p, (_, proto) in self._ports.items() if proto.room_id == room_id]:
            self.close(port)
        self._room_buckets.pop(room_id, None)

    def is_open(self, port: int) -> bool:
        return port in self._ports

    def slots_active(self, port: int, window: float = 30.0) -> int:
        entry = self._ports.get(port)
        return entry[1].active_slots(window) if entry else 0

    def member_stats(self, port: int, window: float = 40.0) -> dict[str, tuple[bool, int]]:
        """{public key: (sent within the window, bytes sent)} for the pair on `port`; {} for an unknown port.
        The window covers WireGuard's 25-second keepalive, so an active tunnel is always 'sending'."""
        entry = self._ports.get(port)
        return entry[1].member_stats(window) if entry else {}

    def ports_in_use(self) -> int:
        return len(self._ports)
