"""The two HTML pages: the front page with the create form, and a room."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.rooms import RoomGone
from app.templating import TEMPLATES

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    return TEMPLATES.TemplateResponse(request, "index.html", {"settings": request.app.state.settings})


@router.get("/r/{room_id}", response_class=HTMLResponse)
def room_page(room_id: str, request: Request):
    try:
        room = request.app.state.rooms.room_view(room_id)
    except RoomGone:
        return TEMPLATES.TemplateResponse(request, "gone.html", {}, status_code=404)
    context = {"room": room, "settings": request.app.state.settings, "cwd": f"~/r/{room.id[:8]}"}
    return TEMPLATES.TemplateResponse(request, "room.html", context)
