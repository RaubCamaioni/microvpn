"""Jinja templates with the globals every page uses: root_path, the release version and the commit."""

import hashlib
import os
from pathlib import Path

from fastapi.templating import Jinja2Templates

WEBAPP = Path(__file__).parent / "webapp"


def _root_path(request) -> dict:
    """Every template gets `root_path`, the prefix the app is mounted at, to build its URLs with."""
    return {"root_path": request.scope.get("root_path") or ""}


TEMPLATES = Jinja2Templates(directory=WEBAPP / "templates", context_processors=[_root_path])


def _static_version() -> str:
    """Content hash of the stylesheet: browsers re-fetch /static/style.css?v=... after a release."""
    try:
        return hashlib.sha256((WEBAPP / "static" / "style.css").read_bytes()).hexdigest()[:12]
    except OSError:
        return "0"


def _commit_sha() -> str:
    """The commit the running image was built from: MICROVPN_COMMIT, else the file the image build
    writes (containers/Containerfile), else "dev" outside the image."""
    if os.environ.get("MICROVPN_COMMIT"):
        return os.environ["MICROVPN_COMMIT"]
    try:
        return Path("/etc/microvpn-commit").read_text().strip() or "dev"
    except OSError:
        return "dev"


def _app_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("microvpn")
    except PackageNotFoundError:
        return "0.0.0"


TEMPLATES.env.globals["static_version"] = _static_version()
TEMPLATES.env.globals["commit_sha"] = _commit_sha()
TEMPLATES.env.globals["app_version"] = _app_version()
