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
   "download", "copy", "leave", "config-text", "qr", "peer-list", "peers-legend", "owner", "end-room"]
    .forEach(function (id) { el[id] = document.getElementById(id); });

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
    configText = me.config_template.split(PLACEHOLDER).join(memory.privateKey);
    currentHash = me.peers_hash;
    el["config-text"].textContent = configText;
    el["peer-count"].textContent = "(covers " + me.peers.length + " peer" + (me.peers.length === 1 ? "" : "s") + ")";
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
    el.config.hidden = false;
  }

  function renderMembers(state) {
    el["member-count"].textContent = state.members.length;
    if (memory && memory.memberToken) return; // the joined view lists peers with relay markers instead
    el["peer-list"].innerHTML = "";
    state.members.forEach(function (m) {
      var li = document.createElement("li");
      li.innerHTML = "<span>·</span><span></span>";
      li.lastChild.textContent = m.name + " " + m.address;
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
    if (res.ok) renderMembers(await res.json());
    if (!(memory && memory.memberToken)) return;
    var me = await fetch(api + "/me", { headers: { Authorization: "Bearer " + memory.memberToken }, cache: "no-store" });
    if (me.status === 404) return showGone();
    if (me.status === 401) {
      // our membership is gone (left from another tab, or the key was lost): offer to join again
      save(null);
      el.config.hidden = true;
      el.join.hidden = false;
      return;
    }
    if (me.ok) renderConfig(await me.json());
  }

  async function join(name) {
    var pair = nacl.box.keyPair();
    var state = { privateKey: b64(pair.secretKey), publicKey: b64(pair.publicKey), name: name, downloadedHash: null };
    var res = await fetch(api + "/members", {
      method: "POST",
      headers: { "content-type": "application/json" },
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
      await fetch(api + "/me", { method: "DELETE", headers: { Authorization: "Bearer " + memory.memberToken } });
    }
    save(null);
    el.config.hidden = true;
    el.join.hidden = false;
    await refresh();
  }

  async function endRoom() {
    var token = ownerToken();
    if (!token) return;
    var res = await fetch(api, { method: "DELETE", headers: { Authorization: "Bearer " + token } });
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
    var name = new FormData(el["join-form"]).get("name");
    join(String(name || "").trim());
  });
  el.download.addEventListener("click", function (event) {
    event.preventDefault();
    var blob = new Blob([configText], { type: "application/octet-stream" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "mv" + shortId + ".conf";
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
    markDownloaded();
  });
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
  tick();
  var clockTimer = setInterval(tick, 1000);
  refresh();
  var pollTimer = setInterval(refresh, POLL_MS);
})();
