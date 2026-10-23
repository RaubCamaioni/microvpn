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
    assert "join.sh" not in page and "| sh" not in page  # the curl | sh path is gone
    assert 'name="public_key"' in page and "wg genkey | tee private.key | wg pubkey" in page
    assert "Import tunnel(s) from file" in page and "Scan from QR code" in page
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


def test_room_page_has_the_room_link_qr_and_the_apple_profile_action(client):
    room = client.post("/rooms", json={"name": "", "lifetime_hours": 1}).json()
    page = client.get(f"/r/{room['id']}").text
    assert 'id="room-qr"' in page and 'id="add-apple"' in page and "static/mobileconfig.js" in page
    assert "scan to open this room" in page


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
