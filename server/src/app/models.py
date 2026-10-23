"""The three tables: a room, its members, and the member pairs with their relay ports.

Datetimes are timezone-aware UTC throughout (SQLModel >= 0.0.48 stores them with their offset and
rejects naive values); use utcnow().
"""

from datetime import datetime, timezone

from sqlalchemy import Engine, UniqueConstraint, text
from sqlmodel import Field, SQLModel, create_engine


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Visitor(SQLModel, table=True):
    """A browser: the hash of its cookie token and the address the cookie was issued to."""

    id: str = Field(primary_key=True)  # sha256 of the cookie token
    ip: str
    created_at: datetime
    last_seen: datetime


class Room(SQLModel, table=True):
    id: str = Field(primary_key=True)
    name: str = ""
    subnet: str  # e.g. "10.37.201.0/24"
    created_at: datetime
    expires_at: datetime
    owner_token: str
    # the browser that created the room: its admin, alongside the owner token
    visitor_id: str = Field(default="", index=True)
    # the lifetime chosen at creation (hours); [extend] starts it again from now
    lifetime_hours: int = 1


class Member(SQLModel, table=True):
    # the service checks both before inserting; the constraints are the backstop
    __table_args__ = (UniqueConstraint("room_id", "public_key"), UniqueConstraint("room_id", "address"))

    id: int | None = Field(default=None, primary_key=True)
    room_id: str = Field(foreign_key="room.id", index=True)
    name: str
    public_key: str
    address: str
    token: str = Field(index=True)
    created_at: datetime
    # False until the room's admin (its creator) accepts the device; pending members have no pairs
    approved: bool = False
    # where the join came from, shown to the admin only
    joined_ip: str = ""
    location: str = ""
    # the browser that joined, so its front page can list the room
    visitor_id: str = Field(default="", index=True)


class Pair(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    room_id: str = Field(index=True)
    member_a_id: int
    member_b_id: int
    port: int = Field(unique=True)


def make_engine(database: str) -> Engine:
    return create_engine(f"sqlite:///{database}", connect_args={"check_same_thread": False})


def create_db_and_tables(engine: Engine) -> None:
    SQLModel.metadata.create_all(engine)
    _add_missing_columns(engine)


# columns added after the first release, with the value an older row gets: SQLite's create_all never
# alters a table that exists, so a deploy over an existing database adds them here
_ADDED_COLUMNS = {
    "room": [
        ("visitor_id", "VARCHAR NOT NULL DEFAULT ''"),
        ("lifetime_hours", "INTEGER NOT NULL DEFAULT 1"),
    ],
    "member": [
        ("approved", "BOOLEAN NOT NULL DEFAULT 0"),
        ("joined_ip", "VARCHAR NOT NULL DEFAULT ''"),
        ("location", "VARCHAR NOT NULL DEFAULT ''"),
        ("visitor_id", "VARCHAR NOT NULL DEFAULT ''"),
    ],
}


def _add_missing_columns(engine: Engine) -> None:
    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            present = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            for name, definition in columns:
                if name not in present:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))
