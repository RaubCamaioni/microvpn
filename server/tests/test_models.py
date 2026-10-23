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
        assert m.approved is False and m.joined_ip == "" and m.location == ""
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
