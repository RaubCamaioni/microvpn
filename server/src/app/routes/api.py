"""The JSON API: rooms, members, a member's view and config, the join script, health."""

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/healthz")
def healthz(request: Request):
    rooms = getattr(request.app.state, "rooms", None)
    counts = rooms.counts() if rooms is not None else {"rooms": 0, "members": 0, "ports_in_use": 0}
    return {**counts, "relay": "ok"}
