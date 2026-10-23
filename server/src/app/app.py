"""The FastAPI application factory. Tests build apps with their own settings and a relay double."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders

from app.models import create_db_and_tables, make_engine
from app.relay import Relay
from app.rooms import RoomError, RoomService
from app.routes import api, pages
from app.settings import Settings
from app.templating import WEBAPP


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


def create_app(settings: Settings, relay=None) -> FastAPI:
    engine = make_engine(settings.DATABASE)
    relay = relay if relay is not None else Relay(settings)
    rooms = RoomService(engine, settings, relay)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        create_db_and_tables(engine)
        yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.relay = relay
    app.state.engine = engine
    app.state.rooms = rooms

    @app.exception_handler(RoomError)
    async def room_error(request: Request, exc: RoomError):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status)

    app.add_middleware(NoStoreMiddleware)
    app.mount("/static", StaticFiles(directory=WEBAPP / "static"), name="static")
    app.include_router(api.router)
    app.include_router(pages.router)
    return app
