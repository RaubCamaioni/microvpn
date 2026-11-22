"""Signing in and out. Included only when the GitHub OAuth app is configured (see create_app)."""

import hmac
from http.cookies import SimpleCookie

from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse, RedirectResponse

from app import keys
from app.accounts import SESSION_COOKIE, session_cookie, session_token
from app.github import GitHubError, authorize_url

router = APIRouter()

STATE_COOKIE = "microvpn_oauth_state"
STATE_SECONDS = 600


def _cookie_args(request: Request) -> dict:
    settings = request.app.state.settings
    return {
        "path": settings.ROOT_PATH or "/",
        "httponly": True,
        "samesite": "lax",
        "secure": settings.PUBLIC_URL.lower().startswith("https://"),
    }


def _front_page(request: Request) -> RedirectResponse:
    """Every sign-in ends on the front page: there is no caller-chosen target to abuse."""
    return RedirectResponse(f"{request.app.state.settings.ROOT_PATH}/", status_code=302)


def _state_cookie(request: Request) -> str:
    cookie = SimpleCookie()
    try:
        cookie.load(request.headers.get("cookie", ""))
    except Exception:  # noqa: BLE001 - a malformed cookie header is no cookie
        return ""
    return cookie[STATE_COOKIE].value if STATE_COOKIE in cookie else ""


@router.get("/auth/github")
def start(request: Request):
    state = keys.new_token()
    response = RedirectResponse(authorize_url(request.app.state.settings, state), status_code=302)
    response.set_cookie(STATE_COOKIE, state, max_age=STATE_SECONDS, **_cookie_args(request))
    return response


@router.get("/auth/github/callback")
async def callback(request: Request, code: str = "", state: str = ""):
    args = _cookie_args(request)
    expected = _state_cookie(request)
    # a cancel at GitHub comes back with error= and no code; a state that is not ours is someone else's link
    if not code or not state or not expected or not hmac.compare_digest(state.encode(), expected.encode()):
        response = _front_page(request)
        response.delete_cookie(STATE_COOKIE, **args)
        return response
    try:
        github_id, login = await request.app.state.github.identity(code)
    except GitHubError:
        response = PlainTextResponse("GitHub sign-in failed; try again", status_code=502)
        response.delete_cookie(STATE_COOKIE, **args)
        return response
    _, token = request.app.state.accounts.sign_in(github_id, login)
    response = _front_page(request)
    response.delete_cookie(STATE_COOKIE, **args)
    response.headers.append("set-cookie", session_cookie(request.app.state.settings, token))
    return response


@router.post("/auth/logout", status_code=204)
def logout(request: Request):
    request.app.state.accounts.sign_out(session_token(request))
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, **_cookie_args(request))
    return response
