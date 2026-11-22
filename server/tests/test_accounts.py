from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, select

from app.accounts import AccountService
from app.models import Account, AccountSession, create_db_and_tables, make_engine
from tests.conftest import make_settings


@pytest.fixture
def clock():
    class C:
        t = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        @classmethod
        def advance(cls, **delta):
            cls.t = cls.t + timedelta(**delta)

    return C


def service(tmp_path, clock, **settings_over):
    engine = make_engine(str(tmp_path / "a.db"))
    create_db_and_tables(engine)
    return AccountService(engine, make_settings(**settings_over), now=lambda: clock.t)


@pytest.fixture
def svc(tmp_path, clock):
    return service(tmp_path, clock)


def test_sign_in_upserts_by_github_id(svc):
    a, t1 = svc.sign_in(7, "old")
    b, t2 = svc.sign_in(7, "renamed")
    assert a.id == b.id and b.login == "renamed" and t1 != t2
    # two devices, both signed in to the one account
    assert svc.account_of(t1).id == a.id and svc.account_of(t2).login == "renamed"
    with Session(svc.engine) as s:
        assert len(s.exec(select(Account)).all()) == 1


def test_unknown_and_missing_tokens(svc):
    svc.sign_in(7, "a")
    assert svc.account_of(None) is None and svc.account_of("") is None and svc.account_of("nope") is None


def test_token_is_stored_hashed(svc):
    _, token = svc.sign_in(7, "a")
    with Session(svc.engine) as s:
        rows = s.exec(select(AccountSession)).all()
    assert len(rows) == 1 and rows[0].id != token and len(rows[0].id) == 64


def test_sign_out_ends_one_session(svc):
    _, t1 = svc.sign_in(7, "a")
    _, t2 = svc.sign_in(7, "a")
    svc.sign_out(t1)
    svc.sign_out(None)
    svc.sign_out("nope")
    assert svc.account_of(t1) is None and svc.account_of(t2) is not None


def test_session_expires_after_30_idle_days_and_is_swept(svc, clock):
    _, token = svc.sign_in(7, "a")
    clock.advance(days=29)
    assert svc.account_of(token)  # use renews it
    clock.advance(days=29)
    assert svc.account_of(token)
    assert svc.sweep() == 0
    clock.advance(days=31)
    assert svc.account_of(token) is None
    assert svc.sweep() == 1 and svc.sweep() == 0


def test_admin_is_by_numeric_id(tmp_path, clock):
    svc = service(tmp_path, clock, ADMIN_GITHUB_IDS="7")
    a, _ = svc.sign_in(7, "x")
    b, _ = svc.sign_in(8, "x")  # the same login proves nothing
    assert svc.is_admin(a) and not svc.is_admin(b) and not svc.is_admin(None)
