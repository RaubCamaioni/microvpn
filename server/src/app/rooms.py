"""Rooms, members and pairs: the rules of the service, over SQLite and the relay.

The service owns every decision the API and pages only expose: limits, addresses, pair ports,
expiry. Errors are RoomError subclasses carrying the HTTP status the API answers with.
"""

import asyncio
import hmac
import logging
import random
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Protocol

from sqlalchemy import Engine, func
from sqlmodel import Session, delete, select

from app import keys
from app.models import Member, Pair, Room, utcnow
from app.settings import Settings
from app.wgconfig import PeerView, peers_hash, render_config

logger = logging.getLogger("uvicorn.error")


class RoomError(Exception):
    status = 500
    detail = "error"

    def __init__(self, detail: str | None = None):
        if detail is not None:
            self.detail = detail
        super().__init__(self.detail)


class RoomGone(RoomError):
    status, detail = 404, "room ended or never existed"


class RoomFull(RoomError):
    status, detail = 409, "room is full"


class DuplicateKey(RoomError):
    status, detail = 409, "that public key is already in the room"


class Invalid(RoomError):
    status, detail = 400, "invalid request"


class RateLimited(RoomError):
    status, detail = 429, "too many rooms created from this address; try later"


class TooManyRooms(RoomError):
    status, detail = 429, "the server has reached its room limit; try later"


class ServerFull(RoomError):
    status, detail = 503, "no relay ports left; try later"


class Unauthorized(RoomError):
    status, detail = 401, "bad or missing token"


class RelayPorts(Protocol):
    """What the service needs from app.relay.Relay (tests substitute a double)."""

    async def open(self, port: int, room_id: str, keys: tuple[str, str]) -> bool: ...

    def close(self, port: int) -> None: ...

    def close_room(self, room_id: str) -> None: ...

    def slots_active(self, port: int, window: float = 30.0) -> int: ...

    def ports_in_use(self) -> int: ...


@dataclass(frozen=True)
class MemberPublic:
    name: str
    address: str
    public_key: str


@dataclass(frozen=True)
class RoomView:
    id: str
    name: str
    expires_at: datetime
    max_members: int
    members: list[MemberPublic]


@dataclass(frozen=True)
class MemberView:
    address: str
    config_template: str
    peers_hash: str
    peers: list[PeerView]


class RoomService:
    def __init__(self, engine: Engine, settings: Settings, relay: RelayPorts, now: Callable[[], datetime] = utcnow):
        self.engine = engine
        self.settings = settings
        self.relay = relay
        self.now = now
        # room creations per client address in the last hour; in memory, reset by a restart
        self._creations: dict[str, deque[datetime]] = {}
        # joins are serialised: each one reads the room, binds ports, then writes in one transaction
        self._join_lock = asyncio.Lock()

    def _session(self) -> Session:
        return Session(self.engine, expire_on_commit=False)

    # -- rooms --------------------------------------------------------------------------------

    def create_room(self, name: str | None, lifetime_hours: int, client_ip: str) -> Room:
        clean = keys.clean_room_name(name)
        if clean is None:
            raise Invalid(f"room name: up to {keys.ROOM_NAME_MAX} printable characters")
        if lifetime_hours not in self.settings.ROOM_LIFETIMES:
            choices = ", ".join(str(h) for h in self.settings.ROOM_LIFETIMES)
            raise Invalid(f"lifetime must be one of {choices} hours")
        now = self.now()
        self._check_creation_rate(client_ip, now)
        with self._session() as s:
            live = s.exec(select(Room).where(Room.expires_at > now)).all()
            if len(live) >= self.settings.ROOM_MAX_LIVE:
                raise TooManyRooms()
            room = Room(
                id=keys.new_room_id(),
                name=clean,
                subnet=self._free_subnet({r.subnet for r in live}),
                created_at=now,
                expires_at=now + timedelta(hours=lifetime_hours),
                owner_token=keys.new_token(),
            )
            s.add(room)
            s.commit()
        self._creations.setdefault(client_ip, deque()).append(now)
        return room

    def _check_creation_rate(self, client_ip: str, now: datetime) -> None:
        window = self._creations.setdefault(client_ip, deque())
        while window and window[0] <= now - timedelta(hours=1):
            window.popleft()
        if len(window) >= self.settings.ROOM_CREATE_PER_IP_PER_HOUR:
            raise RateLimited()

    @staticmethod
    def _free_subnet(used: set[str]) -> str:
        for _ in range(1000):
            subnet = f"10.{random.randrange(256)}.{random.randrange(256)}.0/24"
            if subnet not in used:
                return subnet
        raise TooManyRooms()

    def room_view(self, room_id: str) -> RoomView:
        with self._session() as s:
            room = self._live_room(s, room_id)
            members = self._members(s, room)
            return RoomView(
                id=room.id,
                name=room.name,
                expires_at=room.expires_at,
                max_members=self.settings.ROOM_MAX_MEMBERS,
                members=[MemberPublic(m.name, m.address, m.public_key) for m in members],
            )

    def end_room(self, room_id: str, owner_token: str) -> None:
        with self._session() as s:
            room = self._live_room(s, room_id)
            if not hmac.compare_digest(room.owner_token, owner_token or ""):
                raise Unauthorized()
            self._delete_room(s, room)
            s.commit()

    def sweep(self) -> int:
        """Delete the rooms whose time is up and close their ports; returns how many."""
        with self._session() as s:
            expired = s.exec(select(Room).where(Room.expires_at <= self.now())).all()
            for room in expired:
                self._delete_room(s, room)
            s.commit()
            return len(expired)

    async def restore(self) -> int:
        """At startup: sweep, then re-open the relay port of every surviving pair; returns how many opened."""
        self.sweep()
        with self._session() as s:
            rows = s.exec(
                select(Pair.port, Pair.room_id, Member.public_key, Pair.member_b_id)
                .join(Member, Member.id == Pair.member_a_id)
                .join(Room, Room.id == Pair.room_id)
            ).all()
            keys_b = {m.id: m.public_key for m in s.exec(select(Member)).all()}
        opened = 0
        for port, room_id, key_a, member_b_id in rows:
            if await self.relay.open(port, room_id, (key_a, keys_b[member_b_id])):
                opened += 1
            else:
                logger.warning("restore: pair port %d of room %s could not be re-bound", port, room_id[:8])
        return opened

    def counts(self) -> dict:
        now = self.now()
        with self._session() as s:
            rooms = s.exec(select(func.count()).select_from(Room).where(Room.expires_at > now)).one()
            members = s.exec(
                select(func.count()).select_from(Member).join(Room, Room.id == Member.room_id).where(Room.expires_at > now)
            ).one()
        return {"rooms": rooms, "members": members, "ports_in_use": self.relay.ports_in_use()}

    # -- members ------------------------------------------------------------------------------

    async def join(self, room_id: str, name: str, public_key: str) -> Member:
        clean = keys.clean_device_name(name)
        if clean is None:
            raise Invalid(f"device name: 1 to {keys.DEVICE_NAME_MAX} printable characters")
        if not keys.is_public_key(public_key):
            raise Invalid("public key: 44 base64 characters encoding 32 bytes")
        async with self._join_lock:
            # 1. read: the room, its members, the ports in use (no write lock held across awaits)
            with self._session() as s:
                room = self._live_room(s, room_id)
                others = self._members(s, room)
                if len(others) >= self.settings.ROOM_MAX_MEMBERS:
                    raise RoomFull()
                if any(m.public_key == public_key for m in others):
                    raise DuplicateKey()
                address = self._free_address(room, others)
                used_ports = set(s.exec(select(Pair.port)).all())
            # 2. bind one relay port per pair; on any failure give every port back
            opened: list[tuple[int, int]] = []  # (other member id, port)
            try:
                for other in others:
                    port = await self._open_free_port(room.id, used_ports, (public_key, other.public_key))
                    opened.append((other.id, port))
                    used_ports.add(port)
                # 3. write: the member and its pairs in one short transaction
                with self._session() as s:
                    member = Member(
                        room_id=room.id,
                        name=clean,
                        public_key=public_key,
                        address=address,
                        token=keys.new_token(),
                        created_at=self.now(),
                    )
                    s.add(member)
                    s.flush()  # assigns member.id for the pairs
                    for other_id, port in opened:
                        a, b = sorted((member.id, other_id))
                        s.add(Pair(room_id=room.id, member_a_id=a, member_b_id=b, port=port))
                    s.commit()
            except BaseException:
                for _, port in opened:
                    self.relay.close(port)
                raise
        return member

    async def _open_free_port(self, room_id: str, used: set[int], pair_keys: tuple[str, str]) -> int:
        lo, hi = self.settings.RELAY_PORT_RANGE
        free = set(range(lo, hi + 1)) - used
        while free:
            port = random.choice(tuple(free))
            if await self.relay.open(port, room_id, pair_keys):
                return port
            free.discard(port)
        raise ServerFull()

    def _free_address(self, room: Room, members: list[Member]) -> str:
        base = room.subnet[: -len("0/24")]  # "10.x.y."
        taken = {m.address for m in members}
        for host in range(2, 2 + self.settings.ROOM_MAX_MEMBERS):
            if f"{base}{host}" not in taken:
                return f"{base}{host}"
        raise RoomFull()

    def member_view(self, room_id: str, token: str) -> MemberView:
        with self._session() as s:
            room = self._live_room(s, room_id)
            me = self._member_by_token(s, room, token)
            others = {m.id: m for m in self._members(s, room) if m.id != me.id}
            port_by_other: dict[int, int] = {}
            for pair in self._pairs_of(s, room, me):
                other_id = pair.member_b_id if pair.member_a_id == me.id else pair.member_a_id
                port_by_other[other_id] = pair.port
            peers = [
                PeerView(o.name, o.public_key, o.address, port_by_other[oid], self.relay.slots_active(port_by_other[oid]))
                for oid, o in others.items()
                if oid in port_by_other
            ]
            peers.sort(key=lambda p: (p.name, p.public_key))
            return MemberView(
                address=me.address,
                config_template=render_config(me.address, peers, self.settings.RELAY_HOST),
                peers_hash=peers_hash(peers),
                peers=peers,
            )

    def leave(self, room_id: str, token: str) -> None:
        with self._session() as s:
            room = self._live_room(s, room_id)
            me = self._member_by_token(s, room, token)
            for pair in self._pairs_of(s, room, me):
                self.relay.close(pair.port)
                s.delete(pair)
            s.delete(me)
            s.commit()

    # -- helpers ------------------------------------------------------------------------------

    def _live_room(self, s: Session, room_id: str) -> Room:
        if not keys.is_room_id(room_id):
            raise RoomGone()
        room = s.get(Room, room_id)
        if room is None or room.expires_at <= self.now():
            raise RoomGone()
        return room

    @staticmethod
    def _members(s: Session, room: Room) -> list[Member]:
        return list(s.exec(select(Member).where(Member.room_id == room.id).order_by(Member.id)).all())

    @staticmethod
    def _member_by_token(s: Session, room: Room, token: str) -> Member:
        if not token:
            raise Unauthorized()
        member = s.exec(select(Member).where(Member.room_id == room.id, Member.token == token)).first()
        if member is None:
            raise Unauthorized()
        return member

    @staticmethod
    def _pairs_of(s: Session, room: Room, me: Member) -> list[Pair]:
        return list(
            s.exec(
                select(Pair).where(Pair.room_id == room.id, (Pair.member_a_id == me.id) | (Pair.member_b_id == me.id))
            ).all()
        )

    def _delete_room(self, s: Session, room: Room) -> None:
        self.relay.close_room(room.id)
        s.exec(delete(Pair).where(Pair.room_id == room.id))
        s.exec(delete(Member).where(Member.room_id == room.id))
        s.delete(room)
