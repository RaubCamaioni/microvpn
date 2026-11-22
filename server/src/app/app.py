"""The FastAPI application factory. Tests build apps with their own settings and a relay double."""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from http.cookies import SimpleCookie

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers, MutableHeaders

from app.accounts import AccountService, session_cookie
from app.geo import Geo
from app.github import HttpGitHub
from app.models import create_db_and_tables, make_engine
from app.relay import Relay
from app.rooms import VISITOR_COOKIE_DAYS, RoomError, RoomService
from app.routes import api, auth, pages
from app.settings import Settings
from app.templating import WEBAPP

logger = logging.getLogger("uvicorn.error")


VISITOR_COOKIE = "microvpn_visitor"


class VisitorMiddleware:
    """The browser identity. Every page and API request gets a visitor id in `request.state`: the one
    behind the `microvpn_visitor` cookie when that cookie is known and was issued to the request's
    address, else "". A browser is recorded only when it creates or joins a room: the route then
    leaves the new cookie token in `request.state.visitor_token` and it is set on the response.
    Static files and healthz are skipped."""

    def __init__(self, app, rooms: RoomService, settings: Settings):
        self.app = app
        self.rooms = rooms
        self.settings = settings
        secure = settings.PUBLIC_URL.lower().startswith("https://")
        self.cookie_attrs = (
            f"; Path={settings.ROOT_PATH or '/'}; Max-Age={VISITOR_COOKIE_DAYS * 86400}; HttpOnly; SameSite=Lax"
            + ("; Secure" if secure else "")
        )

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or path.startswith("/static/") or path == "/healthz":
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get("cookie", ""))
        except Exception:  # noqa: BLE001 - a malformed cookie header is no cookie
            cookie = SimpleCookie()
        token = cookie[VISITOR_COOKIE].value if VISITOR_COOKIE in cookie else None
        client_ip = headers.get("x-real-ip") or (scope.get("client") or ("unknown",))[0]
        state = scope.setdefault("state", {})
        state["visitor_id"] = await asyncio.to_thread(self.rooms.visitor, token, client_ip) if token else ""

        async def send_with_cookie(message):
            new_token = state.get("visitor_token")
            if message["type"] == "http.response.start" and new_token:
                message.setdefault("headers", [])
                MutableHeaders(raw=message["headers"]).append("set-cookie", f"{VISITOR_COOKIE}={new_token}{self.cookie_attrs}")
            await send(message)

        await self.app(scope, receive, send_with_cookie)


class SessionCookieMiddleware:
    """A session in use keeps its cookie: when a request renewed its session (app.accounts.current_account
    leaves the token in `request.state.session_refresh`), the cookie is set again with a fresh lifetime."""

    def __init__(self, app, settings: Settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        state = scope.setdefault("state", {})

        async def send_with_cookie(message):
            token = state.get("session_refresh")
            if message["type"] == "http.response.start" and token:
                message.setdefault("headers", [])
                MutableHeaders(raw=message["headers"]).append("set-cookie", session_cookie(self.settings, token))
            await send(message)

        await self.app(scope, receive, send_with_cookie)


class OwnSiteMiddleware:
    """Only this site's own pages may make a browser act. Cookies name the admin of a room, and a page
    on another site, or on another name under the same domain, can make a browser send them. Browsers
    say where a request comes from in Sec-Fetch-Site; anything but a read that comes from elsewhere is
    refused. A client that is no browser sends no such header and holds no one's cookies."""

    SAFE = {"GET", "HEAD", "OPTIONS"}
    ELSEWHERE = {"cross-site", "same-site"}

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] not in self.SAFE:
            if (Headers(scope=scope).get("sec-fetch-site") or "").lower() in self.ELSEWHERE:
                response = JSONResponse({"detail": "request from another site refused"}, status_code=403)
                return await response(scope, receive, send)
        await self.app(scope, receive, send)


class NoStoreMiddleware:
    """Every response is `Cache-Control: no-store`: room state changes by the second and nothing is
    worth caching. Pure ASGI so streaming responses are untouched."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_no_store(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                MutableHeaders(raw=message["headers"])["cache-control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_no_store)


def create_app(settings: Settings, relay=None, locate=None, github=None) -> FastAPI:
    engine = make_engine(settings.DATABASE)
    relay = relay if relay is not None else Relay(settings)
    locate = locate if locate is not None else Geo(settings.GEOIP_DB).locate
    rooms = RoomService(engine, settings, relay, locate=locate)
    accounts = AccountService(engine, settings)

    async def sweeper(app: FastAPI):
        """Every SWEEP_SECONDS: end the rooms whose time is up. An error is logged, never fatal."""
        while True:
            await asyncio.sleep(settings.SWEEP_SECONDS)
            try:
                removed = await asyncio.to_thread(rooms.sweep)
                await asyncio.to_thread(accounts.sweep)
                if removed:
                    logger.info("sweep: ended %d expired room(s)", removed)
            except Exception:  # noqa: BLE001 - the sweeper must survive anything
                logger.exception("sweep failed")
            app.state.sweeps += 1

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        create_db_and_tables(engine)
        reopened = await rooms.restore()
        logger.info("restore: %d relay port(s) re-bound", reopened)
        app.state.sweeps = 0
        task = asyncio.create_task(sweeper(app))
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.relay = relay
    app.state.engine = engine
    app.state.rooms = rooms
    app.state.accounts = accounts
    app.state.github = github if github is not None else HttpGitHub(settings)

    @app.exception_handler(RoomError)
    async def room_error(request: Request, exc: RoomError):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status)

    app.add_middleware(SessionCookieMiddleware, settings=settings)
    app.add_middleware(OwnSiteMiddleware)
    app.add_middleware(NoStoreMiddleware)
    app.add_middleware(VisitorMiddleware, rooms=rooms, settings=settings)
    app.mount("/static", StaticFiles(directory=WEBAPP / "static"), name="static")
    app.include_router(api.router)
    if settings.github_enabled:  # without the OAuth app there is no login: these paths answer 404
        app.include_router(auth.router)
    app.include_router(pages.router)
    return app
