"""The three tables: a room, its members, and the member pairs with their relay ports.

Datetimes are naive UTC throughout (SQLite drops timezones); use utcnow().
"""

from datetime import datetime, timezone

from sqlalchemy import Engine
from sqlmodel import Field, SQLModel, create_engine


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Room(SQLModel, table=True):
    id: str = Field(primary_key=True)
    name: str = ""
    subnet: str  # e.g. "10.37.201.0/24"
    created_at: datetime
    expires_at: datetime
    owner_token: str


class Member(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    room_id: str = Field(foreign_key="room.id", index=True)
    name: str
    public_key: str
    address: str
    token: str = Field(index=True)
    created_at: datetime


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
