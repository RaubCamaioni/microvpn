"""GitHub as the identity provider: the OAuth web flow, with no scopes.

All the app learns is the account's numeric id and login. The access token is used for that one
question and dropped.
"""

from urllib.parse import urlencode

import httpx

from app.settings import Settings

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_URL = "https://api.github.com/user"


class GitHubError(Exception):
    """GitHub could not be reached, refused the code, or answered something unexpected."""


def callback_url(settings: Settings) -> str:
    return settings.PUBLIC_URL.rstrip("/") + "/auth/github/callback"


def authorize_url(settings: Settings, state: str) -> str:
    query = {"client_id": settings.GITHUB_CLIENT_ID, "redirect_uri": callback_url(settings), "state": state}
    return f"{AUTHORIZE_URL}?{urlencode(query)}"


class HttpGitHub:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def identity(self, code: str) -> tuple[int, str]:
        """Exchange a callback's code for who signed in: (numeric id, login)."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as http:
                answer = await http.post(
                    TOKEN_URL,
                    headers={"Accept": "application/json"},
                    data={
                        "client_id": self.settings.GITHUB_CLIENT_ID,
                        "client_secret": self.settings.GITHUB_CLIENT_SECRET,
                        "code": code,
                        "redirect_uri": callback_url(self.settings),
                    },
                )
                answer.raise_for_status()
                token = answer.json().get("access_token")
                if not token:  # GitHub answers 200 with an "error" field for a bad or used code
                    raise GitHubError("no access token")
                user = await http.get(
                    USER_URL, headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
                )
                user.raise_for_status()
                body = user.json()
                return int(body["id"]), str(body["login"])
        except GitHubError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as e:
            raise GitHubError(str(e)) from e
