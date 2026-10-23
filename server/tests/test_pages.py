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


def test_index_is_just_the_title_the_create_form_and_the_rooms_tree(client):
    page = client.get("/").text
    assert "<h1>microvpn</h1>" in page and "end-to-end encrypted" in page.lower() and "share" in page.lower()
    assert "ends on its own" not in page and "how it works" not in page and "limits" not in page
    assert 'id="new-room"' in page and 'name="lifetime"' in page and "1 hour" in page and "24 hours" in page
    assert page.index('id="new-room"') < page.index("your rooms/")  # the form above the tree
    assert "└── no rooms yet" in page
    a = client.post("/rooms", json={"name": "game night", "lifetime_hours": 1}).json()
    b = client.post("/rooms", json={"name": "", "lifetime_hours": 4}).json()
    page = client.get("/").text
    assert "no rooms yet" not in page
    assert page.count("├── ") == 1 and page.count("└── ") == 1  # tree connectors: every branch but the last, then the last
    assert "game night" in page and f"room {b['id'][:8]}" in page
    assert f'data-delete="{a["id"]}"' in page and f'data-extend="{a["id"]}"' in page


def test_room_page_and_gone_page(client):
    room = client.post("/rooms", json={"name": "game night", "lifetime_hours": 4}).json()
    page = client.get(f"/r/{room['id']}").text
    assert "game night" in page and f'data-room-id="{room["id"]}"' in page and 'data-max-members="8"' in page
    # the link at the top with copy and a QR button that opens the overlay
    assert f'id="room-link"' in page and f"/r/{room['id']}" in page
    assert 'id="copy-link"' in page and 'id="link-qr"' in page and 'id="overlay"' in page and 'id="overlay-close"' in page
    # top: install steps and own-key steps, both folded away, then the new-profile form, then the profiles tree
    assert "install WireGuard" in page and "Import tunnel(s) from file" in page
    assert "wg genkey | tee private.key | wg pubkey" in page and 'name="public_key"' in page
    assert 'id="new-profile"' in page and 'id="profiles"' in page and "your profiles/" in page
    assert page.index('id="room-link"') < page.index('id="install"') < page.index('id="own-key"') < page.index('id="new-profile"') < page.index('id="profiles"')
    assert 'data-tab=' not in page
    assert "join.sh" not in page and "| sh" not in page
    assert 'id="requests"' in page and "accept" in page.lower()
    assert "approximate location" in page and "db-ip.com" in page
    assert 'id="extend-form"' in page and 'name="lifetime"' in page
    assert 'id="admin-bar"' in page and "[ADMIN]" in page and 'id="expires"' in page
    assert page.index('class="title"') < page.index('id="expires"') < page.index('id="member-count"') < page.index('id="room-link"')
    assert "this browser created the room" not in page
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
@pytest.mark.parametrize("name", ["index.js", "room.js", "mobileconfig.js", "vendor/nacl-fast.min.js", "vendor/qrcode.js"])
def test_scripts_parse(name):
    subprocess.run(["node", "--check", str(STATIC / name)], check=True)


def test_room_page_never_prints_a_config_and_keeps_the_apple_builder(client):
    room = client.post("/rooms", json={"name": "", "lifetime_hours": 1}).json()
    page = client.get(f"/r/{room['id']}").text
    assert 'id="config-text"' not in page and 'id="config"' not in page  # actions live on the profile rows
    assert 'id="profile-list"' in page and "static/mobileconfig.js" in page and 'id="platform-hint"' in page


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_mobileconfig_builder_produces_a_valid_wireguard_profile(tmp_path):
    import json
    import plistlib

    config = "[Interface]\nPrivateKey = k\nAddress = 10.1.2.3/24\n\n[Peer]\n# a<b&c\nPublicKey = p\nEndpoint = relay.test:20001\n"
    opts = {
        "platform": "macos",
        "name": "game <night> & co",
        "config": config,
        "remoteAddress": "relay.test",
        "identifier": "com.raubcamaioni.microvpn.abc",
        "profileUUID": "11111111-2222-3333-4444-555555555555",
        "tunnelUUID": "66666666-7777-8888-9999-000000000000",
    }
    script = f"const m = require({json.dumps(str(STATIC / 'mobileconfig.js'))}); process.stdout.write(m.build({json.dumps(opts)}));"
    out = subprocess.run(["node", "-e", script], check=True, capture_output=True).stdout
    profile = plistlib.loads(out)
    assert profile["PayloadType"] == "Configuration" and profile["PayloadIdentifier"] == opts["identifier"]
    assert profile["PayloadUUID"] == opts["profileUUID"] and profile["PayloadDisplayName"] == "game <night> & co"
    (tunnel,) = profile["PayloadContent"]
    assert tunnel["PayloadType"] == "com.apple.vpn.managed" and tunnel["VPNType"] == "VPN"
    assert tunnel["VPNSubType"] == "com.wireguard.macos" and tunnel["PayloadUUID"] == opts["tunnelUUID"]
    assert tunnel["PayloadIdentifier"] == opts["identifier"] + ".tunnel"
    assert tunnel["UserDefinedName"] == "game <night> & co"
    assert tunnel["VendorConfig"]["WgQuickConfig"] == config  # escaping round-trips the config exactly
    assert tunnel["VPN"] == {"RemoteAddress": "relay.test", "AuthenticationMethod": "Password"}
    ios = subprocess.run(
        ["node", "-e", script.replace('"platform": "macos"', '"platform": "ios"')], check=True, capture_output=True
    ).stdout
    assert plistlib.loads(ios)["PayloadContent"][0]["VPNSubType"] == "com.wireguard.ios"


def test_vendored_libraries_are_present():
    nacl = (STATIC / "vendor/nacl-fast.min.js").read_text(encoding="utf-8")
    assert "nacl" in nacl and "box" in nacl and len(nacl) > 20000
    qr = (STATIC / "vendor/qrcode.js").read_text(encoding="utf-8")
    assert "QR Code Generator for JavaScript" in qr[:2000] and "createSvgTag" in qr
