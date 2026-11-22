"""Accounts and their sessions: who is signed in.

An account is a GitHub identity (its numeric id and login, nothing else). A session is one signed-in
browser, known by a cookie whose token is stored only as a hash. Sessions are not tied to an address,
unlike visitors: a phone changes networks and stays signed in.
"""

import hashlib
from datetime import datetime, timedelta
from http.cookies import SimpleCookie
from typing import Callable

from sqlalchemy import Engine
from sqlmodel import Session, delete, select

from app import keys
from app.models import Account, AccountSession, utcnow
from app.settings import Settings

SESSION_COOKIE = "microvpn_session"
SESSION_DAYS = 30


class AccountService:
    def __init__(self, engine: Engine, settings: Settings, now: Callable[[], datetime] = utcnow):
        self.engine = engine
        self.settings = settings
        self.now = now

    def _session(self) -> Session:
        return Session(self.engine, expire_on_commit=False)

    @staticmethod
    def _session_id(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def sign_in(self, github_id: int, login: str) -> tuple[Account, str]:
        """The account for a GitHub identity, made on its first sign-in, and a new session's cookie token."""
        now = self.now()
        token = keys.new_token()
        with self._session() as s:
            account = s.exec(select(Account).where(Account.github_id == github_id)).first()
            if account is None:
                account = Account(github_id=github_id, login=login, created_at=now, last_login=now)
            account.login = login  # a rename at GitHub shows here at the next sign-in
            account.last_login = now
            s.add(account)
            s.flush()
            s.add(AccountSession(id=self._session_id(token), account_id=account.id, created_at=now, last_seen=now))
            s.commit()
            return account, token

    def account_of(self, token: str | None) -> Account | None:
        """The account behind a session cookie; None when the token is unknown or the session has not
        been used for SESSION_DAYS."""
        return self.lookup(token)[0]

    def lookup(self, token: str | None) -> tuple[Account | None, bool]:
        """`account_of`, and whether this use renewed the session (at most hourly): the browser's cookie
        is then issued again, so both ends count SESSION_DAYS from the last use."""
        if not token:
            return None, False
        now = self.now()
        with self._session() as s:
            row = s.get(AccountSession, self._session_id(token))
            if row is None or now - row.last_seen > timedelta(days=SESSION_DAYS):
                return None, False
            renewed = now - row.last_seen > timedelta(hours=1)
            if renewed:
                row.last_seen = now
                s.add(row)
                s.commit()
            return s.get(Account, row.account_id), renewed

    def sign_out(self, token: str | None) -> None:
        """End this browser's session; the account's other sessions stay."""
        if not token:
            return
        with self._session() as s:
            s.exec(delete(AccountSession).where(AccountSession.id == self._session_id(token)))
            s.commit()

    def is_admin(self, account: Account | None) -> bool:
        return account is not None and account.github_id in self.settings.ADMIN_GITHUB_IDS

    def sweep(self) -> int:
        """Delete the sessions not used for SESSION_DAYS; returns how many."""
        cutoff = self.now() - timedelta(days=SESSION_DAYS)
        with self._session() as s:
            stale = s.exec(select(AccountSession).where(AccountSession.last_seen < cutoff)).all()
            for row in stale:
                s.delete(row)
            s.commit()
            return len(stale)


def session_token(request) -> str | None:
    """The session cookie's token on a request, or None."""
    cookie = SimpleCookie()
    try:
        cookie.load(request.headers.get("cookie", ""))
    except Exception:  # noqa: BLE001 - a malformed cookie header is no cookie
        return None
    return cookie[SESSION_COOKIE].value if SESSION_COOKIE in cookie else None


def current_account(request) -> Account | None:
    """The signed-in account of a request, looked up once and kept on `request.state`."""
    if not hasattr(request.state, "account"):
        accounts = getattr(request.app.state, "accounts", None)
        token = session_token(request)
        account, renewed = accounts.lookup(token) if accounts is not None else (None, False)
        request.state.account = account
        if account is not None and renewed:
            request.state.session_refresh = token  # SessionCookieMiddleware issues the cookie again
    return request.state.account


def session_cookie(settings: Settings, token: str) -> str:
    """The Set-Cookie value of a session: scoped to the app's path, unreadable by scripts, SESSION_DAYS long."""
    secure = "; Secure" if settings.PUBLIC_URL.lower().startswith("https://") else ""
    return (
        f"{SESSION_COOKIE}={token}; Path={settings.ROOT_PATH or '/'}; Max-Age={SESSION_DAYS * 86400}"
        f"; HttpOnly; SameSite=Lax{secure}"
    )
