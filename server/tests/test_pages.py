import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.app import create_app
from tests.conftest import FakeRelay

PREFIX = "/microvpn"
STATIC = Path(__file__).resolve().parents[1] / "src/app/webapp/static"


def unprefixed(page):
    urls = re.findall(r'(?:href|src|action)="(/[^"]*)"', page)
    return [u for u in urls if not u.startswith(PREFIX + "/") and u != PREFIX]


def test_index_renders_the_form_and_the_notice(client):
    page = client.get("/").text
    assert 'name="lifetime"' in page and "<option" in page and "1 hour" in page and "24 hours" in page
    assert "never" in page and "private key" in page.lower() and "8 members" in page


def test_room_page_and_gone_page(client):
    room = client.post("/rooms", json={"name": "game night", "lifetime_hours": 4}).json()
    page = client.get(f"/r/{room['id']}").text
    assert "game night" in page and f'data-room-id="{room["id"]}"' in page and 'data-max-members="8"' in page
    assert f"/r/{room['id']}/join.sh | sh -s --" in page
    assert "vendor/nacl-fast.min.js" in page and "vendor/qrcode.js" in page
    gone = client.get("/r/" + "z" * 26)
    assert gone.status_code == 404 and "ended or never existed" in gone.text


def test_every_page_url_carries_the_root_path(settings):
    with TestClient(create_app(settings, relay=FakeRelay()), root_path=PREFIX) as c:
        assert unprefixed(c.get(PREFIX + "/").text) == []
        room = c.post(PREFIX + "/rooms", json={"name": "", "lifetime_hours": 1}).json()
        page = c.get(PREFIX + f"/r/{room['id']}").text
        assert unprefixed(page) == [] and f'data-root-path="{PREFIX}"' in page
        assert room["url"] == settings.PUBLIC_URL + "/r/" + room["id"]


def test_version_tag_on_every_page(client):
    assert re.search(r"v\d+\.\d+\.\d+\+([0-9a-f]{3,7}|dev)", client.get("/").text)


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node for a syntax check")
@pytest.mark.parametrize("name", ["index.js", "room.js", "vendor/nacl-fast.min.js", "vendor/qrcode.js"])
def test_scripts_parse(name):
    subprocess.run(["node", "--check", str(STATIC / name)], check=True)


def test_vendored_libraries_are_present():
    nacl = (STATIC / "vendor/nacl-fast.min.js").read_text(encoding="utf-8")
    assert "nacl" in nacl and "box" in nacl and len(nacl) > 20000
    qr = (STATIC / "vendor/qrcode.js").read_text(encoding="utf-8")
    assert "QR Code Generator for JavaScript" in qr[:2000] and "createSvgTag" in qr
