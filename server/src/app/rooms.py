"""Rooms, members and pairs: the rules of the service, over SQLite and the relay.

The service owns every decision the API and pages only expose: limits, addresses, pair ports,
expiry. Errors are RoomError subclasses carrying the HTTP status the API answers with.
"""

import asyncio
import hashlib
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
from app.models import Member, Pair, Room, Visitor, utcnow

VISITOR_COOKIE_DAYS = 30
VISITOR_FORGET_DAYS = 60
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


class MemberNotFound(RoomError):
    status, detail = 404, "no such member in this room"


class RelayPorts(Protocol):
    """What the service needs from app.relay.Relay (tests substitute a double)."""

    async def open(self, port: int, room_id: str, keys: tuple[str, str]) -> bool: ...

    def close(self, port: int) -> None: ...

    def close_room(self, room_id: str) -> None: ...

    def slots_active(self, port: int, window: float = 30.0) -> int: ...

    def member_stats(self, port: int, window: float = 40.0) -> dict[str, tuple[bool, int]]: ...

    def ports_in_use(self) -> int: ...


@dataclass(frozen=True)
class MemberPublic:
    id: int
    name: str
    address: str
    public_key: str
    approved: bool
    active: bool = False  # its tunnel sent something through the relay in the last 40 s
    bytes: int = 0  # ciphertext bytes it has sent through the relay in this room


@dataclass(frozen=True)
class AdminMemberView:
    id: int
    name: str
    address: str
    public_key: str
    approved: bool
    joined_ip: str
    location: str
    created_at: datetime


@dataclass(frozen=True)
class AdminView:
    members: list[AdminMemberView]


@dataclass(frozen=True)
class MyRoom:
    """A live room as the front page lists it for the browser that created or joined it."""

    id: str
    name: str
    expires_at: datetime
    is_admin: bool
    devices: list[str]  # the names this browser joined as
    lifetime_hours: int = 1


@dataclass(frozen=True)
class RoomView:
    id: str
    name: str
    expires_at: datetime
    max_members: int
    members: list[MemberPublic]


@dataclass(frozen=True)
class MemberView:
    approved: bool
    address: str
    config_template: str  # "" while the member waits for the admin
    peers_hash: str
    peers: list[PeerView]


class RoomService:
    def __init__(
        self,
        engine: Engine,
        settings: Settings,
        relay: RelayPorts,
        now: Callable[[], datetime] = utcnow,
        locate: Callable[[str], str] | None = None,
    ):
        self.engine = engine
        self.settings = settings
        self.relay = relay
        self.now = now
        self.locate = locate or (lambda ip: "")
        # room creations per client address in the last hour; in memory, reset by a restart
        self._creations: dict[str, deque[datetime]] = {}
        # joins are serialised: each one reads the room, binds ports, then writes in one transaction
        self._join_lock = asyncio.Lock()

    def _session(self) -> Session:
        return Session(self.engine, expire_on_commit=False)

    # -- visitors: the browser identity behind the cookie ------------------------------------------

    @staticmethod
    def visitor_id_of(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def visitor(self, token: str | None, client_ip: str) -> tuple[str, str | None]:
        """The visitor id for a cookie token, when the token is known and was issued to this address;
        otherwise a fresh visitor. Returns (visitor id, new token to set or None)."""
        now = self.now()
        with self._session() as s:
            if token:
                row = s.get(Visitor, self.visitor_id_of(token))
                if row is not None and row.ip == client_ip:
                    if now - row.last_seen > timedelta(hours=1):
                        row.last_seen = now
                        s.add(row)
                        s.commit()
                    return row.id, None
            new_token = keys.new_token()
            row = Visitor(id=self.visitor_id_of(new_token), ip=client_ip, created_at=now, last_seen=now)
            s.add(row)
            s.commit()
            return row.id, new_token

    def rooms_of_visitor(self, visitor_id: str) -> list[MyRoom]:
        """Live rooms this browser created or joined, newest first."""
        if not visitor_id:
            return []
        now = self.now()
        with self._session() as s:
            created = {r.id: r for r in s.exec(select(Room).where(Room.visitor_id == visitor_id, Room.expires_at > now)).all()}
            joined = s.exec(
                select(Member, Room)
                .join(Room, Room.id == Member.room_id)
                .where(Member.visitor_id == visitor_id, Room.expires_at > now)
            ).all()
            rooms: dict[str, Room] = dict(created)
            devices: dict[str, list[str]] = {}
            for member, room in joined:
                rooms[room.id] = room
                devices.setdefault(room.id, []).append(member.name)
            return [
                MyRoom(r.id, r.name, r.expires_at, r.visitor_id == visitor_id, devices.get(r.id, []), r.lifetime_hours)
                for r in sorted(rooms.values(), key=lambda r: r.created_at, reverse=True)
            ]

    def is_admin(self, room_id: str, visitor_id: str) -> bool:
        if not visitor_id or not keys.is_room_id(room_id):
            return False
        with self._session() as s:
            room = s.get(Room, room_id)
            return room is not None and room.visitor_id == visitor_id

    # -- rooms --------------------------------------------------------------------------------

    def create_room(self, name: str | None, lifetime_hours: int, client_ip: str, visitor_id: str = "") -> Room:
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
                visitor_id=visitor_id or "",
                lifetime_hours=lifetime_hours,
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
            pairs = s.exec(select(Pair).where(Pair.room_id == room.id)).all()
        # a member is connected when it sent through any of its pairs lately; bytes sum over its pairs
        active: dict[str, bool] = {}
        sent: dict[str, int] = {}
        for pair in pairs:
            for key, (is_active, count) in self.relay.member_stats(pair.port).items():
                active[key] = active.get(key, False) or is_active
                sent[key] = sent.get(key, 0) + count
        return RoomView(
            id=room.id,
            name=room.name,
            expires_at=room.expires_at,
            max_members=self.settings.ROOM_MAX_MEMBERS,
            members=[
                MemberPublic(m.id, m.name, m.address, m.public_key, m.approved, active.get(m.public_key, False), sent.get(m.public_key, 0))
                for m in members
            ],
        )

    def extend(self, room_id: str, owner_token: str | None, lifetime_hours: int | None, visitor_id: str = "") -> datetime:
        """Start the room's lifetime again from now: its own, or a chosen one. Admin only."""
        with self._session() as s:
            room = self._admin_room(s, room_id, owner_token, visitor_id)
            hours = lifetime_hours if lifetime_hours is not None else room.lifetime_hours
            if hours not in self.settings.ROOM_LIFETIMES:
                choices = ", ".join(str(h) for h in self.settings.ROOM_LIFETIMES)
                raise Invalid(f"lifetime must be one of {choices} hours")
            room.expires_at = self.now() + timedelta(hours=hours)
            room.lifetime_hours = hours
            s.add(room)
            s.commit()
            return room.expires_at

    def end_room(self, room_id: str, owner_token: str | None, visitor_id: str = "") -> None:
        with self._session() as s:
            room = self._admin_room(s, room_id, owner_token, visitor_id)
            self._delete_room(s, room)
            s.commit()

    # -- the admin: the room's creator, by owner token or by the creating browser's cookie ----------

    def admin_view(self, room_id: str, owner_token: str | None, visitor_id: str = "") -> AdminView:
        """Every device of the room with where it joined from: only the admin sees this."""
        with self._session() as s:
            room = self._admin_room(s, room_id, owner_token, visitor_id)
            return AdminView(
                members=[
                    AdminMemberView(m.id, m.name, m.address, m.public_key, m.approved, m.joined_ip, m.location, m.created_at)
                    for m in self._members(s, room)
                ]
            )

    async def approve(self, room_id: str, owner_token: str | None, member_id: int, visitor_id: str = "") -> None:
        """Accept a pending device: it gets a pair, and a relay port, with every accepted member."""
        with self._session() as s:
            room = self._admin_room(s, room_id, owner_token, visitor_id)
            member = s.get(Member, member_id)
            if member is None or member.room_id != room.id:
                raise MemberNotFound()
        async with self._join_lock:
            await self._connect_locked(room_id, member_id)

    def remove_member(self, room_id: str, owner_token: str | None, member_id: int, visitor_id: str = "") -> None:
        """Reject a pending device or kick an accepted one: its pairs close and its row goes."""
        with self._session() as s:
            room = self._admin_room(s, room_id, owner_token, visitor_id)
            member = s.get(Member, member_id)
            if member is None or member.room_id != room.id:
                raise MemberNotFound()
            self._drop_member(s, room, member)
            s.commit()

    def sweep(self) -> int:
        """Delete the rooms whose time is up and close their ports; returns how many."""
        now = self.now()
        with self._session() as s:
            expired = s.exec(select(Room).where(Room.expires_at <= now)).all()
            for room in expired:
                self._delete_room(s, room)
            # browsers not seen for a long time are forgotten with their cookies
            s.exec(delete(Visitor).where(Visitor.last_seen < now - timedelta(days=VISITOR_FORGET_DAYS)))
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

    async def join(
        self,
        room_id: str,
        name: str,
        public_key: str,
        owner_token: str | None = None,
        client_ip: str = "",
        visitor_id: str = "",
    ) -> Member:
        """Add a device to the room. It waits for the admin unless the join comes from the admin (the
        owner token, or the creating browser's cookie), in which case it is accepted at once. The
        address it joined from and its approximate location are kept for the admin's decision."""
        clean = keys.clean_device_name(name)
        if clean is None:
            raise Invalid(f"device name: 1 to {keys.DEVICE_NAME_MAX} printable characters")
        if not keys.is_public_key(public_key):
            raise Invalid("public key: 44 base64 characters encoding 32 bytes")
        async with self._join_lock:
            with self._session() as s:
                if owner_token is not None:
                    room = self._admin_room(s, room_id, owner_token, visitor_id)
                    as_admin = True
                else:
                    room = self._live_room(s, room_id)
                    as_admin = bool(visitor_id) and room.visitor_id == visitor_id
                members = self._members(s, room)
                if len(members) >= self.settings.ROOM_MAX_MEMBERS:  # pending devices hold a place too
                    raise RoomFull()
                if any(m.public_key == public_key for m in members):
                    raise DuplicateKey()
                member = Member(
                    room_id=room.id,
                    name=clean,
                    public_key=public_key,
                    address=self._free_address(room, members),
                    token=keys.new_token(),
                    created_at=self.now(),
                    joined_ip=client_ip or "",
                    location=self.locate(client_ip) if client_ip else "",
                    visitor_id=visitor_id or "",
                )
                s.add(member)
                s.commit()
            if as_admin:
                await self._connect_locked(room.id, member.id)
                member.approved = True
        return member

    async def _connect_locked(self, room_id: str, member_id: int) -> None:
        """Accept a member (caller holds the join lock): bind one relay port per accepted peer, then
        write the pairs and the flag in one short transaction; any failure gives the ports back."""
        with self._session() as s:
            room = self._live_room(s, room_id)
            member = s.get(Member, member_id)
            if member is None or member.room_id != room.id:
                raise MemberNotFound()
            if member.approved:
                return
            others = [m for m in self._members(s, room) if m.approved and m.id != member.id]
            used_ports = set(s.exec(select(Pair.port)).all())
        opened: list[tuple[int, int]] = []  # (other member id, port)
        try:
            for other in others:
                port = await self._open_free_port(room.id, used_ports, (member.public_key, other.public_key))
                opened.append((other.id, port))
                used_ports.add(port)
            with self._session() as s:
                for other_id, port in opened:
                    a, b = sorted((member.id, other_id))
                    s.add(Pair(room_id=room.id, member_a_id=a, member_b_id=b, port=port))
                row = s.get(Member, member.id)
                if row is None:
                    raise MemberNotFound()  # removed while its ports were being bound
                row.approved = True
                s.add(row)
                s.commit()
        except BaseException:
            for _, port in opened:
                self.relay.close(port)
            raise

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
            if not me.approved:
                return MemberView(approved=False, address=me.address, config_template="", peers_hash=peers_hash([]), peers=[])
            others = {m.id: m for m in self._members(s, room) if m.id != me.id and m.approved}
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
                approved=True,
                address=me.address,
                config_template=render_config(me.address, peers, self.settings.RELAY_HOST),
                peers_hash=peers_hash(peers),
                peers=peers,
            )

    def leave(self, room_id: str, token: str) -> None:
        with self._session() as s:
            room = self._live_room(s, room_id)
            me = self._member_by_token(s, room, token)
            self._drop_member(s, room, me)
            s.commit()

    def _drop_member(self, s: Session, room: Room, member: Member) -> None:
        for pair in self._pairs_of(s, room, member):
            self.relay.close(pair.port)
            s.delete(pair)
        s.delete(member)

    # -- helpers ------------------------------------------------------------------------------

    def _live_room(self, s: Session, room_id: str) -> Room:
        if not keys.is_room_id(room_id):
            raise RoomGone()
        room = s.get(Room, room_id)
        if room is None or room.expires_at <= self.now():
            raise RoomGone()
        return room

    def _admin_room(self, s: Session, room_id: str, owner_token: str | None, visitor_id: str = "") -> Room:
        """The room, if the caller is its admin: the owner token, or the cookie of the browser that created it."""
        room = self._live_room(s, room_id)
        by_token = owner_token is not None and hmac.compare_digest(room.owner_token, owner_token)
        by_cookie = bool(visitor_id) and bool(room.visitor_id) and room.visitor_id == visitor_id
        if not (by_token or by_cookie):
            raise Unauthorized()
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
