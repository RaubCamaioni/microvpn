"""The two HTML pages: the front page with this browser's rooms and the create form, and a room."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.rooms import RoomGone
from app.templating import TEMPLATES

router = APIRouter()


def visitor(request: Request) -> str:
    return getattr(request.state, "visitor_id", "") or ""


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    my_rooms = request.app.state.rooms.rooms_of_visitor(visitor(request))
    context = {"settings": request.app.state.settings, "my_rooms": my_rooms}
    return TEMPLATES.TemplateResponse(request, "index.html", context)


@router.get("/r/{room_id}", response_class=HTMLResponse)
def room_page(room_id: str, request: Request):
    service = request.app.state.rooms
    try:
        room = service.room_view(room_id)
    except RoomGone:
        return TEMPLATES.TemplateResponse(request, "gone.html", {}, status_code=404)
    context = {
        "room": room,
        "settings": request.app.state.settings,
        "cwd": f"~/r/{room.id[:8]}",
        "is_admin": service.is_admin(room_id, visitor(request)),
    }
    return TEMPLATES.TemplateResponse(request, "room.html", context)
