"""The real GitHub client against a stand-in transport: what it sends, and that every way GitHub can
fail becomes a GitHubError (which the callback route answers with a 502)."""

import httpx
import pytest

from app.github import GitHubError, HttpGitHub
from tests.conftest import make_settings

SETTINGS = make_settings(GITHUB_CLIENT_ID="cid", GITHUB_CLIENT_SECRET="csecret", PUBLIC_URL="https://example.test/microvpn")


def github(handler) -> HttpGitHub:
    return HttpGitHub(SETTINGS, transport=httpx.MockTransport(handler))


async def test_identity_exchanges_the_code_and_asks_who_it_is():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url == "https://github.com/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "tok", "token_type": "bearer"})
        return httpx.Response(200, json={"id": 7, "login": "raub", "name": "ignored"})

    assert await github(handler).identity("the-code") == (7, "raub")
    token_call, user_call = seen
    form = dict(pair.split("=") for pair in token_call.content.decode().split("&"))
    assert token_call.method == "POST" and token_call.headers["accept"] == "application/json"
    assert form["client_id"] == "cid" and form["client_secret"] == "csecret" and form["code"] == "the-code"
    assert form["redirect_uri"] == "https%3A%2F%2Fexample.test%2Fmicrovpn%2Fauth%2Fgithub%2Fcallback"
    assert str(user_call.url) == "https://api.github.com/user" and user_call.headers["authorization"] == "Bearer tok"


def _timeout(request):
    raise httpx.ConnectTimeout("no route", request=request)


@pytest.mark.parametrize(
    "token_answer",
    [
        lambda r: httpx.Response(200, json={"error": "bad_verification_code"}),  # a used or made-up code
        lambda r: httpx.Response(500, text="oops"),
        lambda r: httpx.Response(200, text="<html>not json</html>"),
        lambda r: httpx.Response(200, json=["not", "an", "object"]),
        _timeout,
    ],
)
async def test_a_failed_exchange_is_a_github_error(token_answer):
    with pytest.raises(GitHubError):
        await github(token_answer).identity("code")


@pytest.mark.parametrize(
    "user_answer",
    [
        lambda r: httpx.Response(401, json={"message": "Bad credentials"}),
        lambda r: httpx.Response(200, json={"login": "no-id"}),
        lambda r: httpx.Response(200, json={"id": "not-a-number", "login": "x"}),
        lambda r: httpx.Response(200, text="nope"),
    ],
)
async def test_a_failed_identity_is_a_github_error(user_answer):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "github.com":
            return httpx.Response(200, json={"access_token": "tok"})
        return user_answer(request)

    with pytest.raises(GitHubError):
        await github(handler).identity("code")
