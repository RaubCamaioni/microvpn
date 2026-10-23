"""The blind UDP relay.

One datagram endpoint per member pair. Each port learns at most two addresses (slots) from the
packets that reach it and forwards whatever one slot sends to the other, unchanged. The server
has no WireGuard keys, so everything that passes is ciphertext it cannot read. See the spec's
"The relay" section for the slot and rate rules implemented here.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Callable

from app.settings import Settings

logger = logging.getLogger("uvicorn.error")

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


class PairPort(asyncio.DatagramProtocol):
    """One relay port: two learned addresses, packets from one forwarded to the other."""

    def __init__(self, room_id: str, room_bucket: TokenBucket, global_bucket: TokenBucket, clock: Callable[[], float]):
        self.room_id = room_id
        self.room_bucket = room_bucket
        self.global_bucket = global_bucket
        self.clock = clock
        self.slots: list[Slot] = []
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
        elif len(self.slots) < 2:
            sender = Slot(addr, now)
            self.slots.append(sender)
        elif kind in (HANDSHAKE_INITIATION, HANDSHAKE_RESPONSE):
            # a handshake from a new address: the client roamed; it takes over the stalest slot
            sender = min(self.slots, key=lambda s: s.last_seen)
            sender.addr, sender.last_seen = addr, now
        else:
            return  # data from an address that holds no slot: dropped
        other = next((s for s in self.slots if s is not sender), None)
        if other is None or self.transport is None:
            return
        if self.room_bucket.take(len(data)) and self.global_bucket.take(len(data)):
            self.transport.sendto(data, other.addr)

    def active_slots(self, window: float) -> int:
        now = self.clock()
        return sum(1 for s in self.slots if now - s.last_seen <= window)


class Relay:
    """Opens and closes pair ports and keeps the per-room and server-wide buckets."""

    def __init__(self, settings: Settings, clock: Callable[[], float] = time.monotonic):
        self.settings = settings
        self.clock = clock
        self._ports: dict[int, tuple[asyncio.DatagramTransport, PairPort]] = {}
        self._room_buckets: dict[str, TokenBucket] = {}
        self._global_bucket = self._bucket(settings.RELAY_TOTAL_MBPS)

    def _bucket(self, mbps: float) -> TokenBucket:
        rate = mbps * 1e6 / 8  # bytes per second, with a burst of one second
        return TokenBucket(rate_bytes_per_s=rate, burst_bytes=rate, clock=self.clock)

    def _room_bucket(self, room_id: str) -> TokenBucket:
        if room_id not in self._room_buckets:
            self._room_buckets[room_id] = self._bucket(self.settings.ROOM_MBPS)
        return self._room_buckets[room_id]

    async def open(self, port: int, room_id: str) -> bool:
        """Bind the port for a pair of `room_id`; False (and a warning) when the bind fails."""
        if port in self._ports:
            return True
        loop = asyncio.get_running_loop()
        try:
            transport, protocol = await loop.create_datagram_endpoint(
                lambda: PairPort(room_id, self._room_bucket(room_id), self._global_bucket, self.clock),
                local_addr=(self.settings.RELAY_BIND, port),
            )
        except OSError as e:
            logger.warning("relay: cannot bind udp port %d: %s", port, e)
            return False
        self._ports[port] = (transport, protocol)
        return True

    def close(self, port: int) -> None:
        entry = self._ports.pop(port, None)
        if entry is not None:
            entry[0].close()

    def close_room(self, room_id: str) -> None:
        for port in [p for p, (_, proto) in self._ports.items() if proto.room_id == room_id]:
            self.close(port)
        self._room_buckets.pop(room_id, None)

    def is_open(self, port: int) -> bool:
        return port in self._ports

    def slots_active(self, port: int, window: float = 30.0) -> int:
        entry = self._ports.get(port)
        return entry[1].active_slots(window) if entry else 0

    def ports_in_use(self) -> int:
        return len(self._ports)
