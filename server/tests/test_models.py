from datetime import timedelta

from sqlmodel import Session, select

from app.models import Member, Pair, Room, create_db_and_tables, make_engine, utcnow


def test_tables_round_trip(tmp_path):
    engine = make_engine(str(tmp_path / "t.db"))
    create_db_and_tables(engine)
    now = utcnow()
    with Session(engine) as s:
        s.add(
            Room(
                id="a" * 26,
                name="",
                subnet="10.1.2.0/24",
                created_at=now,
                expires_at=now + timedelta(hours=1),
                owner_token="o",
            )
        )
        s.add(Member(room_id="a" * 26, name="n", public_key="k", address="10.1.2.2", token="t", created_at=now))
        s.commit()
        m = s.exec(select(Member)).one()
        s.add(Pair(room_id="a" * 26, member_a_id=m.id, member_b_id=m.id, port=20000))
        s.commit()
        assert s.exec(select(Pair)).one().port == 20000
        # values come back aware, so they compare with utcnow() without surprises
        assert s.exec(select(Room)).one().expires_at.tzinfo is not None
    assert utcnow().tzinfo is not None


def test_startup_adds_columns_missing_from_an_older_database(tmp_path):
    from sqlalchemy import text

    engine = make_engine(str(tmp_path / "old.db"))
    with engine.begin() as conn:  # the member table of the first release: no approved / joined_ip / location
        conn.execute(text("CREATE TABLE room (id VARCHAR PRIMARY KEY, name VARCHAR, subnet VARCHAR, created_at DATETIME, expires_at DATETIME, owner_token VARCHAR)"))
        conn.execute(text("CREATE TABLE member (id INTEGER PRIMARY KEY, room_id VARCHAR, name VARCHAR, public_key VARCHAR, address VARCHAR, token VARCHAR, created_at DATETIME)"))
        conn.execute(text("INSERT INTO room VALUES ('r', '', '10.1.1.0/24', '2026-01-01 00:00:00+00:00', '2026-01-01 01:00:00+00:00', 'o')"))
        conn.execute(text("INSERT INTO member (room_id, name, public_key, address, token, created_at) VALUES ('r', 'n', 'k', '10.1.1.2', 't', '2026-01-01 00:00:00+00:00')"))
    create_db_and_tables(engine)
    with Session(engine) as s:
        m = s.exec(select(Member)).one()
        assert m.approved is False and m.joined_ip == "" and m.location == "" and m.visitor_id == ""
        old = s.exec(select(Room)).one()
        assert old.visitor_id == "" and old.lifetime_hours == 1
    create_db_and_tables(engine)  # idempotent


def test_a_public_key_or_address_cannot_repeat_within_a_room(tmp_path):
    import pytest
    from sqlalchemy.exc import IntegrityError

    engine = make_engine(str(tmp_path / "u.db"))
    create_db_and_tables(engine)
    now = utcnow()
    with Session(engine) as s:
        s.add(Room(id="b" * 26, name="", subnet="10.1.3.0/24", created_at=now, expires_at=now, owner_token="o"))
        s.add(Member(room_id="b" * 26, name="n", public_key="k", address="10.1.3.2", token="t1", created_at=now))
        s.commit()
        s.add(Member(room_id="b" * 26, name="m", public_key="k", address="10.1.3.3", token="t2", created_at=now))
        with pytest.raises(IntegrityError):
            s.commit()
        s.rollback()
        s.add(Member(room_id="b" * 26, name="m", public_key="k2", address="10.1.3.2", token="t3", created_at=now))
        with pytest.raises(IntegrityError):
            s.commit()


def test_old_database_gains_account_columns(tmp_path):
    """A deploy over the previous release's database: the room table gets the new columns, old rooms
    are not permanent and belong to no account, and the account tables exist."""
    from sqlalchemy import text

    from app.models import Account

    engine = make_engine(str(tmp_path / "old.db"))
    with engine.begin() as c:
        c.execute(
            text(
                "CREATE TABLE room (id VARCHAR PRIMARY KEY, name VARCHAR, subnet VARCHAR, created_at DATETIME,"
                " expires_at DATETIME, owner_token VARCHAR, visitor_id VARCHAR NOT NULL DEFAULT '',"
                " lifetime_hours INTEGER NOT NULL DEFAULT 1)"
            )
        )
        c.execute(
            text(
                "INSERT INTO room (id, name, subnet, created_at, expires_at, owner_token)"
                " VALUES ('r', '', '10.1.1.0/24', '2026-01-01 00:00:00+00:00', '2026-01-01 01:00:00+00:00', 't')"
            )
        )
    create_db_and_tables(engine)
    with Session(engine) as s:
        room = s.get(Room, "r")
        assert room.permanent is False and room.account_id is None
        s.add(Account(github_id=1, login="a", created_at=utcnow(), last_login=utcnow()))
        s.commit()
        assert s.exec(select(Account)).one().github_id == 1


def test_permanent_expiry_round_trips(tmp_path):
    from app.models import PERMANENT_EXPIRES

    engine = make_engine(str(tmp_path / "t.db"))
    create_db_and_tables(engine)
    with Session(engine) as s:
        s.add(Room(id="p", subnet="10.1.2.0/24", created_at=utcnow(), expires_at=PERMANENT_EXPIRES, owner_token="o", permanent=True))
        s.commit()
    with Session(engine) as s:
        room = s.get(Room, "p")
        assert room.permanent is True and room.expires_at == PERMANENT_EXPIRES and room.expires_at > utcnow()
        assert s.exec(select(Room).where(Room.expires_at > utcnow())).one().id == "p"
