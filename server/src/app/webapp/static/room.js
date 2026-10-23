// room page: this browser holds a list of profiles (devices) in the room. Each profile's key is made
// here, or brought as a public key; the config is assembled locally and shown for the selected
// profile as text, download, QR and (on Apple devices) a configuration profile. The admin's browser
// accepts, rejects and removes devices, and extends or ends the room.
(function () {
  "use strict";
  var main = document.getElementById("room");
  var roomId = main.dataset.roomId;
  var root = main.dataset.rootPath || "";
  var shortId = main.dataset.short;
  var api = root + "/r/" + roomId;
  var storageKey = "microvpn:" + roomId;
  var ownerKey = storageKey + ":owner";
  var PLACEHOLDER = "__PRIVATE_KEY__";
  var POLL_MS = 5000;

  var el = {};
  ["expires", "member-count", "copy-link", "link-qr", "room-link", "profile-form", "profile-error", "profile-list",
   "platform-hint", "peer-list", "end-room", "requests", "request-list", "extend-form", "admin-bar",
   "overlay", "overlay-close", "overlay-qr", "overlay-caption"]
    .forEach(function (id) { el[id] = document.getElementById(id); });

  // -- browser state: the profiles of this room --------------------------------------------------
  function load() {
    var state = null;
    try { state = JSON.parse(localStorage.getItem(storageKey) || "null"); } catch (e) { state = null; }
    if (state && state.memberToken && !state.profiles) {
      // the earlier one-profile shape
      state = { profiles: [state], selected: 0 };
    }
    if (!state || !Array.isArray(state.profiles)) state = { profiles: [], selected: 0 };
    return state;
  }
  function save() {
    try {
      if (memory.profiles.length) localStorage.setItem(storageKey, JSON.stringify(memory));
      else localStorage.removeItem(storageKey);
    } catch (e) { /* private mode: the state lives for this page only */ }
  }
  var memory = load();
  function selected() {
    return memory.profiles[memory.selected] || null;
  }
  function ownerToken() {
    try { return localStorage.getItem(ownerKey); } catch (e) { return null; }
  }
  // the admin: the owner token this browser kept, or the server recognising the creating browser's cookie
  function isAdmin() {
    return Boolean(ownerToken()) || main.dataset.admin === "1";
  }
  function authHeaders(token) {
    return token ? { Authorization: "Bearer " + token } : {};
  }
  function b64(bytes) {
    var s = "";
    for (var i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
    return btoa(s);
  }
  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    var b = new Uint8Array(16);
    crypto.getRandomValues(b);
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    var h = Array.prototype.map.call(b, function (x) { return (x + 256).toString(16).slice(1); }).join("");
    return h.slice(0, 8) + "-" + h.slice(8, 12) + "-" + h.slice(12, 16) + "-" + h.slice(16, 20) + "-" + h.slice(20);
  }
  function link(text, onClick, title) {
    var a = document.createElement("a");
    a.href = "#";
    a.textContent = text;
    if (title) a.title = title;
    a.addEventListener("click", function (event) { event.preventDefault(); onClick(); });
    return a;
  }
  function span(text, className) {
    var s = document.createElement("span");
    s.textContent = text;
    if (className) s.className = className;
    return s;
  }

  // -- platform: only Apple devices can take a tunnel from the browser (a configuration profile) ---
  function applePlatform() {
    var ua = navigator.userAgent || "";
    var platform = navigator.platform || "";
    if (/iPhone|iPad|iPod/.test(ua)) return "ios";
    if (/Mac/.test(platform) && navigator.maxTouchPoints > 1) return "ios"; // iPadOS reports itself as a Mac
    if (/Mac/.test(platform)) return "macos";
    return null;
  }
  var apple = applePlatform();
  var HINTS = {
    ios: "iPhone or iPad: [add to WireGuard] saves a profile; open it, then Settings → Profile Downloaded → Install. " +
      "Or [download] and open the file in WireGuard.",
    macos: "Mac: [add to WireGuard] saves a profile; open it, then System Settings → Privacy & Security → Profiles → Install. " +
      "WireGuard from the App Store must be installed and opened once.",
    other: "WireGuard for Windows and Android cannot be opened from a browser: on Windows import the downloaded " +
      "file in the app, on Android scan [QR] with the app.",
  };

  // -- the QR overlay ----------------------------------------------------------------------------
  function showQr(text, caption) {
    try {
      var qr = qrcode(0, "M");
      qr.addData(text);
      qr.make();
      el["overlay-qr"].innerHTML = qr.createSvgTag({ cellSize: 8, margin: 2 });
    } catch (e) {
      el["overlay-qr"].textContent = "too large for a QR code";
    }
    el["overlay-caption"].textContent = caption;
    el.overlay.hidden = false;
  }
  function hideQr() { el.overlay.hidden = true; }
  el["overlay-close"].addEventListener("click", function (event) { event.preventDefault(); hideQr(); });
  el.overlay.addEventListener("click", function (event) { if (event.target === el.overlay) hideQr(); });
  document.addEventListener("keydown", function (event) { if (event.key === "Escape") hideQr(); });

  function roomLink() {
    return el["room-link"].textContent.trim() || (location.origin + location.pathname);
  }

  // -- what the server says about each profile (by member token), refreshed by the poll ------------
  var views = {};  // token -> /me response
  var lastSignature = null;

  function configTextOf(profile) {
    var view = views[profile.memberToken];
    if (!view || !view.approved) return "";
    return profile.privateKey ? view.config_template.split(PLACEHOLDER).join(profile.privateKey) : view.config_template;
  }

  // -- rendering -------------------------------------------------------------------------------
  function renderProfiles() {
    el["profile-list"].innerHTML = "";
    el["platform-hint"].textContent = memory.profiles.length ? HINTS[apple || "other"] : "";
    if (!memory.profiles.length) {
      var none = document.createElement("div");
      none.className = "branch dim";
      none.textContent = "└── no profiles yet";
      el["profile-list"].appendChild(none);
      return;
    }
    memory.profiles.forEach(function (p, i) {
      var view = views[p.memberToken];
      var row = document.createElement("div");
      row.className = "branch" + (i === memory.selected ? " selected" : "");
      row.appendChild(span(i === memory.profiles.length - 1 ? "└── " : "├── ", "dim connector"));
      row.appendChild(link(p.name, function () { memory.selected = i; save(); render(); }, "show this profile's peers in the members list"));
      if (!view) {
        row.appendChild(span("· …", "dim"));
      } else if (!view.approved) {
        row.appendChild(span("· waiting for the admin", "dim"));
      } else {
        var peers = view.peers.length + " peer" + (view.peers.length === 1 ? "" : "s");
        row.appendChild(span("· " + p.address + " · " + peers + (p.privateKey ? "" : " · key kept elsewhere"), "dim"));
        if (p.downloadedHash && p.downloadedHash !== view.peers_hash) {
          row.appendChild(span("(members changed: import again)", "stale"));
        }
        row.appendChild(link("[download]", function () { downloadProfile(p); }, "the .conf file for the WireGuard app"));
        row.appendChild(link("[copy]", function () { copyProfile(p); }, "the config text to the clipboard"));
        row.appendChild(link("[QR]", function () { memory.selected = i; save(); render(); showProfileQr(p); }, "scan with the phone app"));
        if (apple && p.privateKey) row.appendChild(link("[add to WireGuard]", function () { addToWireGuard(p); }));
      }
      row.appendChild(link("[remove]", function () { removeProfile(i); }, "leave the room with this profile"));
      el["profile-list"].appendChild(row);
    });
  }

  function formatBytes(n) {
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    if (n < 1024 * 1024 * 1024) return (n / (1024 * 1024)).toFixed(1) + " MB";
    return (n / (1024 * 1024 * 1024)).toFixed(2) + " GB";
  }

  function renderMembers(state) {
    var accepted = state.members.filter(function (m) { return m.approved; });
    el["member-count"].textContent = accepted.length;
    el["peer-list"].innerHTML = "";
    state.members.forEach(function (m) {
      var li = document.createElement("li");
      li.dataset.key = m.public_key;
      li.className = m.approved && m.active ? "connected" : "";
      var text = span(m.name + " ");
      text.appendChild(span(m.approved ? m.address + " " : "(waiting for the admin) ", "addr"));
      if (m.approved) {
        if (accepted.length < 2) {
          text.appendChild(span("waiting for a peer", "dim"));
        } else {
          text.appendChild(span(m.active ? "Connected" : "Disconnected", m.active ? "ok" : "bad"));
          text.appendChild(span(" · " + formatBytes(m.bytes), "dim"));
        }
      }
      li.appendChild(text);
      el["peer-list"].appendChild(li);
    });
  }

  function renderAdmin(admin) {
    var pending = admin.members.filter(function (m) { return !m.approved; });
    el.requests.hidden = pending.length === 0;
    el["request-list"].innerHTML = "";
    pending.forEach(function (m) {
      var li = document.createElement("li");
      var who = span(m.name + " ");
      who.appendChild(span("from " + (m.joined_ip || "?") + (m.location ? " (" + m.location + ")" : "") + " ", "addr"));
      who.appendChild(link("[accept]", function () { adminAction("POST", "/members/" + m.id + "/approve"); }));
      who.appendChild(document.createTextNode(" "));
      who.appendChild(link("[reject]", function () { adminAction("DELETE", "/members/" + m.id); }));
      li.appendChild(span("?"));
      li.appendChild(who);
      el["request-list"].appendChild(li);
    });
    // a [remove] beside every accepted member in the members list
    var accepted = {};
    admin.members.forEach(function (m) { if (m.approved) accepted[m.public_key] = m; });
    Array.prototype.forEach.call(el["peer-list"].querySelectorAll("li[data-key]"), function (li) {
      var m = accepted[li.dataset.key];
      if (!m || li.querySelector("a")) return;
      li.lastChild.appendChild(document.createTextNode(" "));
      li.lastChild.appendChild(link("[remove]", function () { adminAction("DELETE", "/members/" + m.id); }));
    });
  }

  var lastState = null, lastAdmin = null;
  function render() {
    renderProfiles();
    if (lastState) renderMembers(lastState);
    if (lastAdmin) renderAdmin(lastAdmin);
  }

  function showGone() {
    main.innerHTML = "<h1>this room ended or never existed</h1><p>Rooms end when their time is up or when their admin ends them.</p>";
    clearInterval(pollTimer);
    clearInterval(clockTimer);
    memory.profiles = [];
    save();
  }

  function showProfileError(message) {
    el["profile-error"].textContent = message;
    el["profile-error"].hidden = false;
  }

  // -- server calls ----------------------------------------------------------------------------
  async function refresh() {
    var res = await fetch(api + "/state", { cache: "no-store" });
    if (res.status === 404) return showGone();
    var state = res.ok ? await res.json() : null;
    var dropped = [];
    for (var i = 0; i < memory.profiles.length; i++) {
      var p = memory.profiles[i];
      var me = await fetch(api + "/me", { headers: authHeaders(p.memberToken), cache: "no-store" });
      if (me.status === 404) return showGone();
      if (me.status === 401) { dropped.push(i); continue; }  // rejected or removed by the admin, or left elsewhere
      if (me.ok) views[p.memberToken] = await me.json();
    }
    if (dropped.length) {
      var names = dropped.map(function (i) { return memory.profiles[i].name; });
      memory.profiles = memory.profiles.filter(function (_, i) { return dropped.indexOf(i) < 0; });
      memory.selected = Math.min(memory.selected, Math.max(0, memory.profiles.length - 1));
      save();
      showProfileError("no longer in the room: " + names.join(", ") + " (not accepted, removed, or left).");
    }
    var admin = null;
    if (isAdmin()) {
      var res2 = await fetch(api + "/admin", { headers: authHeaders(ownerToken()), cache: "no-store" });
      if (res2.ok) admin = await res2.json();
    }
    // rebuild the page only when something changed, so links stay where the reader's cursor is
    var signature = JSON.stringify([state, memory.profiles.map(function (p) { return views[p.memberToken]; }), admin, memory.selected]);
    if (signature === lastSignature) return;
    lastSignature = signature;
    lastState = state;
    lastAdmin = admin;
    render();
  }

  async function createProfile(name, ownPublicKey) {
    var p = { name: name, privateKey: null, publicKey: ownPublicKey, downloadedHash: null };
    if (!ownPublicKey) {
      var pair = nacl.box.keyPair();
      p.privateKey = b64(pair.secretKey);
      p.publicKey = b64(pair.publicKey);
    }
    // the admin's own browser sends the owner token and is accepted at once (the cookie does the same)
    var headers = Object.assign({ "content-type": "application/json" }, authHeaders(ownerToken()));
    var res = await fetch(api + "/members", {
      method: "POST",
      headers: headers,
      body: JSON.stringify({ name: name, public_key: p.publicKey }),
    });
    var json = await res.json().catch(function () { return {}; });
    if (res.status === 404) return showGone();
    if (!res.ok) return showProfileError(json.detail || ("error " + res.status));
    p.memberToken = json.member_token;
    p.address = json.address;
    memory.profiles.push(p);
    memory.selected = memory.profiles.length - 1;
    save();
    el["profile-error"].hidden = true;
    el["profile-form"].reset();
    lastSignature = null;
    await refresh();
  }

  async function removeProfile(i) {
    var p = memory.profiles[i];
    if (!p) return;
    await fetch(api + "/me", { method: "DELETE", headers: authHeaders(p.memberToken) });
    delete views[p.memberToken];
    memory.profiles.splice(i, 1);
    memory.selected = Math.min(memory.selected, Math.max(0, memory.profiles.length - 1));
    save();
    lastSignature = null;
    await refresh();
  }

  async function adminAction(method, path) {
    var res = await fetch(api + path, { method: method, headers: authHeaders(ownerToken()) });
    if (res.status === 404 && path === "") return showGone();
    lastSignature = null;
    await refresh();
  }

  async function endRoom() {
    if (!isAdmin()) return;
    var res = await fetch(api, { method: "DELETE", headers: authHeaders(ownerToken()) });
    if (res.ok || res.status === 404) {
      try { localStorage.removeItem(ownerKey); } catch (e) { /* ignore */ }
      memory.profiles = [];
      save();
      location.href = root + "/";
    }
  }

  // -- actions on the selected profile ---------------------------------------------------------
  function markDownloaded(p) {
    var view = views[p.memberToken];
    if (view) { p.downloadedHash = view.peers_hash; save(); }
    lastSignature = null;
    renderProfiles();
  }
  function saveBlob(text, filename, type) {
    var blob = new Blob([text], { type: type });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  }
  function fileTag(p) {
    return "mv" + shortId + "-" + p.name.replace(/[^A-Za-z0-9_=+.-]/g, "-").slice(0, 12);
  }
  function downloadProfile(p) {
    var text = configTextOf(p);
    if (!text) return;
    saveBlob(text, fileTag(p) + ".conf", "application/octet-stream");
    markDownloaded(p);
  }
  function copyProfile(p) {
    var text = configTextOf(p);
    if (!text) return;
    navigator.clipboard.writeText(text).then(function () { markDownloaded(p); }, function () {});
  }
  function showProfileQr(p) {
    var text = configTextOf(p);
    if (!text) return;
    showQr(text, "Scan with the WireGuard app: " + p.name);
    markDownloaded(p);
  }
  function addToWireGuard(p) {
    var text = configTextOf(p);
    if (!apple || !text || !p.privateKey) return;
    // stable identifiers per profile: re-adding after members change replaces the installed profile
    if (!p.profileUUID) { p.profileUUID = uuid(); p.tunnelUUID = uuid(); save(); }
    var title = document.querySelector("#room-header .title").textContent.trim();
    var profile = window.microvpnMobileconfig.build({
      platform: apple,
      name: "microvpn " + title + " " + p.name,
      config: text,
      remoteAddress: (text.match(/Endpoint = ([^:\n]+)/) || [null, location.hostname])[1],
      identifier: "com.raubcamaioni.microvpn." + roomId + "." + p.memberToken.slice(0, 8),
      profileUUID: p.profileUUID,
      tunnelUUID: p.tunnelUUID,
    });
    saveBlob(profile, fileTag(p) + ".mobileconfig", "application/x-apple-aspen-config");
    markDownloaded(p);
  }

  el["profile-form"].addEventListener("submit", function (event) {
    event.preventDefault();
    var form = new FormData(el["profile-form"]);
    createProfile(String(form.get("name") || "").trim(), String(form.get("public_key") || "").trim());
  });
  el["copy-link"].addEventListener("click", function (event) {
    event.preventDefault();
    navigator.clipboard.writeText(roomLink()).then(function () {
      el["copy-link"].textContent = "[copied]";
      setTimeout(function () { el["copy-link"].textContent = "[copy link]"; }, 1500);
    }, function () {});
  });
  el["link-qr"].addEventListener("click", function (event) {
    event.preventDefault();
    showQr(roomLink(), "Scan to Join");
  });
  el["end-room"].addEventListener("click", function (event) { event.preventDefault(); endRoom(); });

  // -- countdown and extending -----------------------------------------------------------------
  var expiresAt = new Date(main.dataset.expiresAt);
  function tick() {
    var left = Math.max(0, Math.floor((expiresAt - Date.now()) / 1000));
    if (left === 0) { el.expires.textContent = "expired"; return; }
    var h = Math.floor(left / 3600), m = Math.floor((left % 3600) / 60), s = left % 60;
    el.expires.textContent = "expires in " + (h ? h + "h " : "") + m + "m " + s + "s";
  }
  async function extend(hours) {
    var res = await fetch(api + "/extend", {
      method: "POST",
      headers: Object.assign({ "content-type": "application/json" }, authHeaders(ownerToken())),
      body: JSON.stringify(hours ? { lifetime_hours: hours } : {}),
    });
    if (res.status === 404) return showGone();
    if (res.ok) {
      expiresAt = new Date((await res.json()).expires_at);
      tick();
    }
  }
  el["extend-form"].addEventListener("submit", function (event) {
    event.preventDefault();
    extend(Number(new FormData(el["extend-form"]).get("lifetime")));
  });

  // -- start -----------------------------------------------------------------------------------
  if (isAdmin()) el["admin-bar"].hidden = false;
  renderProfiles();
  tick();
  var clockTimer = setInterval(tick, 1000);
  refresh();
  var pollTimer = setInterval(refresh, POLL_MS);
})();
