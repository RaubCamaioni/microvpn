"""The JSON API: rooms, members, a member's view and config, the admin's view, health."""

from dataclasses import asdict

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from app.rooms import RoomService, Unauthorized

router = APIRouter()


class CreateRoom(BaseModel):
    name: str | None = None
    lifetime_hours: int


class JoinRoom(BaseModel):
    name: str
    public_key: str


def rooms(request: Request) -> RoomService:
    return request.app.state.rooms


def client_ip(request: Request) -> str:
    """nginx sets X-Real-IP; without a proxy the socket peer is the client."""
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")


def visitor(request: Request) -> str:
    """The browser behind the request's cookie (set by VisitorMiddleware); "" for skipped paths."""
    return getattr(request.state, "visitor_id", "") or ""


def bearer_or_none(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def bearer(request: Request) -> str:
    token = bearer_or_none(request)
    if token is None:
        raise Unauthorized()
    return token


@router.get("/healthz")
def healthz(request: Request):
    service = getattr(request.app.state, "rooms", None)
    counts = service.counts() if service is not None else {"rooms": 0, "members": 0, "ports_in_use": 0}
    return {**counts, "relay": "ok"}


@router.post("/rooms", status_code=201)
def create_room(body: CreateRoom, request: Request):
    room = rooms(request).create_room(body.name, body.lifetime_hours, client_ip(request), visitor_id=visitor(request))
    public_url = request.app.state.settings.PUBLIC_URL.rstrip("/")
    return {
        "id": room.id,
        "url": f"{public_url}/r/{room.id}",
        "owner_token": room.owner_token,
        "expires_at": room.expires_at.isoformat(),
    }


@router.get("/r/{room_id}/state")
def room_state(room_id: str, request: Request):
    view = rooms(request).room_view(room_id)
    return {
        "name": view.name,
        "expires_at": view.expires_at.isoformat(),
        "max_members": view.max_members,
        "members": [asdict(m) for m in view.members],
    }


@router.post("/r/{room_id}/members", status_code=201)
async def join_room(room_id: str, body: JoinRoom, request: Request):
    # the admin's own device (owner token, or the creating browser's cookie) is accepted at once; everyone else waits
    member = await rooms(request).join(
        room_id,
        body.name,
        body.public_key,
        owner_token=bearer_or_none(request),
        client_ip=client_ip(request),
        visitor_id=visitor(request),
    )
    return {"member_token": member.token, "address": member.address, "approved": member.approved}


# -- the admin: owner token in the Authorization header, or the cookie of the browser that created the room


@router.get("/r/{room_id}/admin")
def admin_view(room_id: str, request: Request):
    view = rooms(request).admin_view(room_id, bearer_or_none(request), visitor_id=visitor(request))
    return {"members": [{**asdict(m), "created_at": m.created_at.isoformat()} for m in view.members]}


@router.post("/r/{room_id}/members/{member_id}/approve", status_code=204)
async def approve_member(room_id: str, member_id: int, request: Request):
    await rooms(request).approve(room_id, bearer_or_none(request), member_id, visitor_id=visitor(request))
    return Response(status_code=204)


@router.delete("/r/{room_id}/members/{member_id}", status_code=204)
def remove_member(room_id: str, member_id: int, request: Request):
    rooms(request).remove_member(room_id, bearer_or_none(request), member_id, visitor_id=visitor(request))
    return Response(status_code=204)


@router.delete("/r/{room_id}", status_code=204)
def end_room(room_id: str, request: Request):
    rooms(request).end_room(room_id, bearer_or_none(request), visitor_id=visitor(request))
    return Response(status_code=204)


# -- a member: its own token


@router.get("/r/{room_id}/me")
def me(room_id: str, request: Request):
    view = rooms(request).member_view(room_id, bearer(request))
    return {
        "approved": view.approved,
        "address": view.address,
        "config_template": view.config_template,
        "peers_hash": view.peers_hash,
        "peers": [asdict(p) for p in view.peers],
    }


@router.get("/r/{room_id}/me.conf")
def me_conf(room_id: str, request: Request):
    view = rooms(request).member_view(room_id, bearer(request))
    return Response(view.config_template, media_type="text/plain; charset=utf-8")


@router.delete("/r/{room_id}/me", status_code=204)
def leave(room_id: str, request: Request):
    rooms(request).leave(room_id, bearer(request))
    return Response(status_code=204)
