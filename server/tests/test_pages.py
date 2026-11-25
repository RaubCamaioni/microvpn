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
    # "/" is the prompt's `home`, the site's landing page, deliberately outside the mount
    return [u for u in urls if not u.startswith(PREFIX + "/") and u != PREFIX and u != "/"]


def test_index_is_just_the_title_the_create_form_and_the_rooms_tree(client):
    page = client.get("/").text
    assert "<h1>microvpn</h1>" in page and "end-to-end encrypted" in page.lower() and "share" in page.lower()
    assert "ends on its own" not in page and "how it works" not in page and "limits" not in page
    assert 'id="new-room"' in page and 'name="lifetime"' in page and "1 hour" in page and "24 hours" in page
    assert page.index('id="new-room"') < page.index("your rooms/")  # the form above the tree
    assert '<span class="dim connector last">└── </span><span class="dim">no rooms yet</span>' in page
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
    assert "wg pubkey &lt; private.key" in page and "Handshaking…" in page  # how to check the pair, and the symptom
    assert "[cli]" in page and "__PRIVATE_KEY__" in page  # where the exact commands are, and what they replace
    assert 'id="new-profile"' in page and 'id="profiles"' in page and "your profiles/" in page
    # the new-profile row is the last item of the tree (a └──), so the tree always has a row
    assert page.index('id="room-link"') < page.index('id="install"') < page.index('id="own-key"') < page.index('id="profiles"')
    assert page.index("your profiles/") < page.index('id="profile-list"') < page.index('id="new-profile"')
    form_row = page[page.index('id="new-profile"'):page.index('id="profile-form"')]
    assert 'class="dim connector last">└── ' in form_row and "no profiles yet" not in page
    # members are a tree too, with one row saying so until someone joins
    assert page.index('id="peers"') < page.index("members/") < page.index("no current connections")
    assert "a profile is one device" not in page
    assert 'id="peers-legend"' not in page and "relay sees" not in page
    assert 'data-tab=' not in page
    assert "join.sh" not in page and "| sh" not in page
    assert 'id="requests"' in page and "pending requests/" in page and "no pending requests" in page
    assert "locations are approximate" not in page and "accept connects" not in page
    assert "db-ip.com" in page  # the licence's attribution link, in the note at the bottom
    assert "end the room and make a new one" in page
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


def test_icons_are_linked_and_served(client):
    page = client.get("/").text
    for name, kind in [("favicon.svg", "image/svg+xml"), ("favicon.ico", "image/"), ("apple-touch-icon.png", "image/png")]:
        assert f"/static/{name}?v=" in page
        r = client.get(f"/static/{name}")
        assert r.status_code == 200 and r.headers["content-type"].startswith(kind)


def test_prompt_path_links_the_site_home_and_the_app_home(settings):
    with TestClient(create_app(settings, relay=FakeRelay()), root_path=PREFIX) as c:
        app = PREFIX.strip("/")
        assert f':/<a href="/">home</a>/<a href="{PREFIX}/">{app}</a><span' in c.get(PREFIX + "/").text
        room = c.post(PREFIX + "/rooms", json={"name": "", "lifetime_hours": 1}).json()
        page = c.get(PREFIX + f"/r/{room['id']}").text
        assert f'<a href="{PREFIX}/">{app}</a>/r/{room["id"][:8]}<span' in page


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


# -- accounts on the pages ----------------------------------------------------------------------

from tests.conftest import sign_in  # noqa: E402


def test_front_page_login_and_no_expiry_option(client, auth_client):
    plain = client.get("/").text  # login not configured: the page is as it always was
    assert "[sign in]" not in plain and "[sign out]" not in plain and "guest@microvpn" in plain
    page = auth_client.get("/").text
    assert '<a href="/auth/github">[sign in]</a>' in page and "no expiry" not in page and "guest@microvpn" in page
    sign_in(auth_client, 8, "stranger")
    page = auth_client.get("/").text
    assert "stranger@microvpn" in page and "[sign out]" in page and "[sign in]" not in page and "no expiry" not in page
    auth_client.post("/auth/logout")
    assert "guest@microvpn" in auth_client.get("/").text
    sign_in(auth_client, 7, "raub")
    assert '<option value="permanent">no expiry</option>' in auth_client.get("/").text


def test_signed_in_pages_follow_the_person_across_addresses(auth_client):
    sign_in(auth_client, 7, "raub")
    for ip in ("1.1.1.1", "2.2.2.2"):
        assert "raub@microvpn" in auth_client.get("/", headers={"X-Real-IP": ip}).text


def test_permanent_room_pages(auth_client):
    sign_in(auth_client, 7, "raub")
    rid = auth_client.post("/rooms", json={"name": "lab", "permanent": True}).json()["id"]
    timed = auth_client.post("/rooms", json={"name": "short", "lifetime_hours": 1}).json()["id"]
    room = auth_client.get(f"/r/{rid}").text
    assert 'data-permanent="1"' in room and 'id="extend-form"' not in room and 'id="end-room"' in room
    assert 'data-admin="1"' in room and "no expiry" in room
    other = auth_client.get(f"/r/{timed}").text
    assert 'data-permanent="0"' in other and 'id="extend-form"' in other
    front = auth_client.get("/").text
    assert f'data-extend="{timed}"' in front and f'data-extend="{rid}"' not in front and f'data-delete="{rid}"' in front
    assert "no expiry</span>" in front


def test_account_rooms_show_on_a_browser_that_never_made_them(auth_app):
    with TestClient(auth_app) as home, TestClient(auth_app) as phone:
        sign_in(home, 7, "raub")
        rid = home.post("/rooms", json={"name": "lab", "permanent": True}).json()["id"]
        assert rid not in phone.get("/").text
        sign_in(phone, 7, "raub")
        assert f'/r/{rid}"' in phone.get("/").text and 'data-admin="1"' in phone.get(f"/r/{rid}").text


def test_login_links_carry_the_prefix(tmp_path):
    from tests.conftest import FakeGitHub, fake_locate, make_settings

    settings = make_settings(
        DATABASE=str(tmp_path / "p.db"), ROOT_PATH=PREFIX, GITHUB_CLIENT_ID="cid", GITHUB_CLIENT_SECRET="cs"
    )
    app = create_app(settings, relay=FakeRelay(), locate=fake_locate, github=FakeGitHub())
    with TestClient(app, root_path=PREFIX) as c:
        page = c.get("/").text
        assert f'href="{PREFIX}/auth/github"' in page and unprefixed(page) == []

# A browser small enough for room.js to start in and draw one poll: elements that remember their children.
ROOM_JS_HARNESS = r"""
function element() {
  var e = { dataset: {}, children: [], style: {}, hidden: false, textContent: "", className: "",
    classList: { add: function () {}, remove: function () {}, toggle: function () {} },
    appendChild: function (c) { e.children.push(c); return c; },
    addEventListener: function () {}, setAttribute: function () {}, querySelector: function () { return element(); },
    focus: function () {}, remove: function () {} };
  Object.defineProperty(e, "innerHTML", { set: function () { e.children = []; }, get: function () { return ""; } });
  return e;
}
function text(e) { return e.textContent + e.children.map(text).join(""); }
var byId = {};
global.document = { getElementById: function (id) { return byId[id] || (byId[id] = element()); },
  createElement: element, addEventListener: function () {}, querySelector: function () { return element(); }, body: element() };
global.window = global; global.location = { origin: "https://vpn.test", pathname: "/r/abc", hostname: "vpn.test" };
global.navigator = { userAgent: "", platform: "", maxTouchPoints: 0 };
global.localStorage = { getItem: function () { return null; }, setItem: function () {}, removeItem: function () {} };
global.setInterval = function () { return 0; };
var room = document.getElementById("room");
room.dataset = { roomId: "abc", rootPath: "", short: "abc", admin: "1", permanent: "1", expiresAt: "9999-12-31T00:00:00+00:00" };
var replies = { "/r/abc/state": STATE, "/r/abc/admin": ADMIN };
global.fetch = function (url) {
  return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(replies[url]); } });
};
process.on("unhandledRejection", function (e) { console.error(e); process.exit(1); });
require(SCRIPT);
setTimeout(function () {
  process.stdout.write(JSON.stringify({
    members: byId["peer-list"].children.map(text), requests: byId["request-list"].children.map(text) }));
}, 50);
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_room_script_draws_members_and_the_admins_pending_requests(tmp_path):
    """The room page with members in it: accepted ones with their state, waiting ones as requests to the admin.
    (A variable named like the function's argument once made the member list throw, and with it everything
    drawn after it: the admin saw "no pending requests" while devices waited.)"""
    import json

    def member(i, name, approved, **more):
        base = {"id": i, "name": name, "address": f"10.1.2.{i + 1}", "public_key": f"key{i}", "approved": approved}
        return {**base, "active": False, "bytes": 0, "handshaking": False, **more}

    state = {
        "name": "home",
        "expires_at": "9999-12-31T00:00:00+00:00",
        "permanent": True,
        "max_members": 8,
        "members": [member(1, "laptop", True, active=True, bytes=2048), member(2, "desk", True), member(3, "phone", False)],
    }
    admin = {"members": [{**m, "joined_ip": "192.0.2.7", "location": "", "created_at": "2026-01-01T00:00:00+00:00"} for m in state["members"]]}
    script = (
        f"var STATE = {json.dumps(state)}, ADMIN = {json.dumps(admin)}, SCRIPT = {json.dumps(str(STATIC / 'room.js'))};"
        + ROOM_JS_HARNESS
    )
    out = json.loads(subprocess.run(["node", "-e", script], check=True, capture_output=True).stdout)
    assert len(out["members"]) == 3
    assert "laptop" in out["members"][0] and "Connected" in out["members"][0] and "2.0 KB" in out["members"][0]
    assert "desk" in out["members"][1] and "Disconnected" in out["members"][1]
    assert "phone" in out["members"][2] and "waiting for the admin" in out["members"][2]
    assert len(out["requests"]) == 1 and "phone" in out["requests"][0] and "[accept]" in out["requests"][0]
