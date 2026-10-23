// room page: generate the key here, join with the public key, assemble the config locally,
// show it as text, download, QR and the shell line, and keep the member list live.
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
  ["expires", "member-count", "copy-link", "join", "join-form", "join-error", "config", "peer-count", "stale",
   "download", "copy", "leave", "config-text", "qr", "room-qr", "peer-list", "peers-legend", "owner", "end-room",
   "add-apple", "platform-hint", "pending", "cancel-join", "requests", "request-list"]
    .forEach(function (id) { el[id] = document.getElementById(id); });

  function authHeaders(token) {
    return token ? { Authorization: "Bearer " + token } : {};
  }

  function link(text, onClick) {
    var a = document.createElement("a");
    a.href = "#";
    a.textContent = text;
    a.addEventListener("click", function (event) { event.preventDefault(); onClick(); });
    return a;
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
      "file in the app, on Android scan the code below with the app.",
  };

  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    var b = new Uint8Array(16);
    crypto.getRandomValues(b);
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    var h = Array.prototype.map.call(b, function (x) { return (x + 256).toString(16).slice(1); }).join("");
    return h.slice(0, 8) + "-" + h.slice(8, 12) + "-" + h.slice(12, 16) + "-" + h.slice(16, 20) + "-" + h.slice(20);
  }

  // -- browser state -------------------------------------------------------------------------
  function load() {
    try { return JSON.parse(localStorage.getItem(storageKey) || "null"); } catch (e) { return null; }
  }
  function save(state) {
    try {
      if (state) localStorage.setItem(storageKey, JSON.stringify(state));
      else localStorage.removeItem(storageKey);
    } catch (e) { /* private mode: the state lives for this page only */ }
    memory = state;
  }
  var memory = load();
  function ownerToken() {
    try { return localStorage.getItem(ownerKey); } catch (e) { return null; }
  }

  function b64(bytes) {
    var s = "";
    for (var i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
    return btoa(s);
  }

  // -- rendering -------------------------------------------------------------------------------
  var configText = "";
  var currentHash = null;

  function renderConfig(me) {
    // a member who joined with their own public key keeps the placeholder and fills it in themselves
    configText = memory.privateKey ? me.config_template.split(PLACEHOLDER).join(memory.privateKey) : me.config_template;
    currentHash = me.peers_hash;
    el["config-text"].textContent = configText;
    el["peer-count"].textContent = "(covers " + me.peers.length + " peer" + (me.peers.length === 1 ? "" : "s") + ")" +
      (memory.privateKey ? "" : " · replace " + PLACEHOLDER + " with your private key");
    el.stale.hidden = !(memory.downloadedHash && memory.downloadedHash !== me.peers_hash);
    try {
      var qr = qrcode(0, "M");
      qr.addData(configText);
      qr.make();
      // pixel-sized, not scalable: a 100%-wide SVG collapses inside the inline-block container
      el.qr.innerHTML = qr.createSvgTag({ cellSize: 3, margin: 2 });
    } catch (e) {
      el.qr.textContent = "config too large for a QR code; use download";
    }
    el["peer-list"].innerHTML = "";
    me.peers.forEach(function (p) {
      var li = document.createElement("li");
      li.dataset.key = p.public_key;
      var mark = document.createElement("span");
      mark.textContent = p.slots_active >= 2 ? "●" : p.slots_active === 1 ? "◐" : "○";
      mark.title = ["relay sees neither side", "relay sees one side", "relay sees both sides"][p.slots_active] || "";
      var text = document.createElement("span");
      text.textContent = p.name + " ";
      var addr = document.createElement("span");
      addr.className = "addr";
      addr.textContent = p.address;
      text.appendChild(addr);
      li.appendChild(mark);
      li.appendChild(text);
      el["peer-list"].appendChild(li);
    });
    el["peers-legend"].hidden = me.peers.length === 0;
    el.join.hidden = true;
    el.pending.hidden = true;
    el.config.hidden = false;
    el["add-apple"].hidden = !apple;
    el["platform-hint"].textContent = HINTS[apple || "other"];
  }

  function renderPending() {
    el.join.hidden = true;
    el.config.hidden = true;
    el.pending.hidden = false;
  }

  // -- the admin's view: who is asking, from where; accept, reject, remove --------------------------
  async function adminAction(method, path) {
    var res = await fetch(api + path, { method: method, headers: authHeaders(ownerToken()) });
    if (res.status === 404 && path === "") return showGone();
    await refresh();
  }

  function renderAdmin(view) {
    var pending = view.members.filter(function (m) { return !m.approved; });
    el.requests.hidden = pending.length === 0;
    el["request-list"].innerHTML = "";
    pending.forEach(function (m) {
      var li = document.createElement("li");
      var who = document.createElement("span");
      who.textContent = m.name + " ";
      var from = document.createElement("span");
      from.className = "addr";
      from.textContent = "from " + (m.joined_ip || "?") + (m.location ? " (" + m.location + ")" : "") + " ";
      who.appendChild(from);
      who.appendChild(link("[accept]", function () { adminAction("POST", "/members/" + m.id + "/approve"); }));
      who.appendChild(document.createTextNode(" "));
      who.appendChild(link("[reject]", function () { adminAction("DELETE", "/members/" + m.id); }));
      li.appendChild(document.createElement("span"));
      li.firstChild.textContent = "?";
      li.appendChild(who);
      el["request-list"].appendChild(li);
    });
    // a [remove] beside every accepted member in whichever list is showing
    var accepted = {};
    view.members.forEach(function (m) { if (m.approved) accepted[m.public_key] = m; });
    Array.prototype.forEach.call(el["peer-list"].querySelectorAll("li[data-key]"), function (li) {
      var m = accepted[li.dataset.key];
      if (!m || li.querySelector("a")) return;
      li.lastChild.appendChild(document.createTextNode(" "));
      li.lastChild.appendChild(link("[remove]", function () { adminAction("DELETE", "/members/" + m.id); }));
    });
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

  function addToWireGuard() {
    if (!apple || !configText) return;
    if (!memory.privateKey) {
      el["platform-hint"].textContent = "you joined with your own public key: fill the private key into the downloaded config instead.";
      return;
    }
    // stable identifiers per room and member: re-adding after members change replaces the profile
    if (!memory.profileUUID) { memory.profileUUID = uuid(); memory.tunnelUUID = uuid(); save(memory); }
    var title = document.querySelector("#room-header h1").textContent.trim();
    var profile = window.microvpnMobileconfig.build({
      platform: apple,
      name: "microvpn " + title,
      config: configText,
      remoteAddress: (configText.match(/Endpoint = ([^:\n]+)/) || [null, location.hostname])[1],
      identifier: "com.raubcamaioni.microvpn." + roomId,
      profileUUID: memory.profileUUID,
      tunnelUUID: memory.tunnelUUID,
    });
    saveBlob(profile, "mv" + shortId + ".mobileconfig", "application/x-apple-aspen-config");
    markDownloaded();
  }

  function renderRoomLinkQr() {
    try {
      var link = location.origin + location.pathname;
      var qr = qrcode(0, "M");
      qr.addData(link);
      qr.make();
      el["room-qr"].innerHTML = qr.createSvgTag({ cellSize: 4, margin: 2 });
    } catch (e) {
      el["room-qr"].textContent = location.href;
    }
  }

  var joinedView = false;

  function renderMembers(state) {
    var accepted = state.members.filter(function (m) { return m.approved; });
    el["member-count"].textContent = accepted.length;
    if (joinedView) return; // the joined view lists peers with relay markers instead
    el["peer-list"].innerHTML = "";
    state.members.forEach(function (m) {
      var li = document.createElement("li");
      li.dataset.key = m.public_key;
      li.innerHTML = "<span></span><span></span>";
      li.firstChild.textContent = m.approved ? "·" : "?";
      li.lastChild.textContent = m.name + " " + (m.approved ? m.address : "(waiting for the admin)");
      el["peer-list"].appendChild(li);
    });
    el["peers-legend"].hidden = true;
  }

  function showGone() {
    main.innerHTML = "<h1>this room ended or never existed</h1><p>Rooms end when their time is up or when their creator ends them.</p>";
    clearInterval(pollTimer);
    clearInterval(clockTimer);
    save(null);
  }

  function showJoinError(message) {
    el["join-error"].textContent = message;
    el["join-error"].hidden = false;
  }

  // -- server calls ----------------------------------------------------------------------------
  async function refresh() {
    var res = await fetch(api + "/state", { cache: "no-store" });
    if (res.status === 404) return showGone();
    var state = res.ok ? await res.json() : null;
    if (memory && memory.memberToken) {
      var me = await fetch(api + "/me", { headers: authHeaders(memory.memberToken), cache: "no-store" });
      if (me.status === 404) return showGone();
      if (me.status === 401) {
        // our membership is gone: rejected or removed by the admin, left from another tab, or the key was lost
        save(null);
        joinedView = false;
        el.config.hidden = true;
        el.pending.hidden = true;
        el.join.hidden = false;
        showJoinError("this device is no longer in the room (not accepted, removed, or left). Join again if you need to.");
      } else if (me.ok) {
        var view = await me.json();
        if (view.approved) {
          joinedView = true;
          renderConfig(view);
        } else {
          joinedView = false;
          renderPending();
        }
      }
    }
    if (state) renderMembers(state);
    if (ownerToken()) {
      var admin = await fetch(api + "/admin", { headers: authHeaders(ownerToken()), cache: "no-store" });
      if (admin.ok) renderAdmin(await admin.json());
    }
  }

  async function join(name, ownPublicKey) {
    var state;
    if (ownPublicKey) {
      state = { privateKey: null, publicKey: ownPublicKey, name: name, downloadedHash: null };
    } else {
      var pair = nacl.box.keyPair();
      state = { privateKey: b64(pair.secretKey), publicKey: b64(pair.publicKey), name: name, downloadedHash: null };
    }
    // the admin's own browser sends the owner token and is accepted at once
    var headers = { "content-type": "application/json" };
    if (ownerToken()) headers.Authorization = "Bearer " + ownerToken();
    var res = await fetch(api + "/members", {
      method: "POST",
      headers: headers,
      body: JSON.stringify({ name: name, public_key: state.publicKey }),
    });
    var json = await res.json().catch(function () { return {}; });
    if (res.status === 404) return showGone();
    if (!res.ok) return showJoinError(json.detail || ("error " + res.status));
    state.memberToken = json.member_token;
    state.address = json.address;
    save(state);
    el["join-error"].hidden = true;
    await refresh();
  }

  async function leave() {
    if (memory && memory.memberToken) {
      await fetch(api + "/me", { method: "DELETE", headers: authHeaders(memory.memberToken) });
    }
    save(null);
    joinedView = false;
    el.config.hidden = true;
    el.pending.hidden = true;
    el.join.hidden = false;
    await refresh();
  }

  async function endRoom() {
    var token = ownerToken();
    if (!token) return;
    var res = await fetch(api, { method: "DELETE", headers: authHeaders(token) });
    if (res.ok || res.status === 404) {
      try { localStorage.removeItem(ownerKey); } catch (e) { /* ignore */ }
      save(null);
      location.href = root + "/";
    }
  }

  // -- actions ---------------------------------------------------------------------------------
  function markDownloaded() {
    memory.downloadedHash = currentHash;
    save(memory);
    el.stale.hidden = true;
  }

  el["join-form"].addEventListener("submit", function (event) {
    event.preventDefault();
    var form = new FormData(el["join-form"]);
    join(String(form.get("name") || "").trim(), String(form.get("public_key") || "").trim());
  });
  el.download.addEventListener("click", function (event) {
    event.preventDefault();
    saveBlob(configText, "mv" + shortId + ".conf", "application/octet-stream");
    markDownloaded();
  });
  el["add-apple"].addEventListener("click", function (event) { event.preventDefault(); addToWireGuard(); });
  el.copy.addEventListener("click", function (event) {
    event.preventDefault();
    navigator.clipboard.writeText(configText).then(markDownloaded, function () {});
  });
  el["copy-link"].addEventListener("click", function (event) {
    event.preventDefault();
    navigator.clipboard.writeText(location.href).then(function () {
      el["copy-link"].textContent = "[copied]";
      setTimeout(function () { el["copy-link"].textContent = "[copy link]"; }, 1500);
    }, function () {});
  });
  el.leave.addEventListener("click", function (event) { event.preventDefault(); leave(); });
  el["cancel-join"].addEventListener("click", function (event) { event.preventDefault(); leave(); });
  el["end-room"].addEventListener("click", function (event) { event.preventDefault(); endRoom(); });

  // -- countdown -------------------------------------------------------------------------------
  var expiresAt = new Date(main.dataset.expiresAt);
  function tick() {
    var left = Math.max(0, Math.floor((expiresAt - Date.now()) / 1000));
    if (left === 0) { el.expires.textContent = "expired"; return; }
    var h = Math.floor(left / 3600), m = Math.floor((left % 3600) / 60), s = left % 60;
    el.expires.textContent = "expires in " + (h ? h + "h " : "") + (h || m ? m + "m " : "") + (h ? "" : s + "s");
  }

  // -- start -----------------------------------------------------------------------------------
  if (ownerToken()) el.owner.hidden = false;
  if (memory && memory.memberToken) { el.join.hidden = true; }
  renderRoomLinkQr();
  tick();
  var clockTimer = setInterval(tick, 1000);
  refresh();
  var pollTimer = setInterval(refresh, POLL_MS);
})();
