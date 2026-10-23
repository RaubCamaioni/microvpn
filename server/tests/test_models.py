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
