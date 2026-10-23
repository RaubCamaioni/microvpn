// front page: create a room, remember the owner token in this browser, go to the room
(function () {
  "use strict";
  var root = document.body.dataset.rootPath || "";
  var form = document.getElementById("create");
  var error = document.getElementById("error");

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    error.hidden = true;
    var data = new FormData(form);
    var body = { name: data.get("name") || null, lifetime_hours: Number(data.get("lifetime")) };
    var res, json;
    try {
      res = await fetch(root + "/rooms", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      json = await res.json().catch(function () { return {}; });
    } catch (e) {
      error.textContent = "network error: " + e;
      error.hidden = false;
      return;
    }
    if (!res.ok) {
      error.textContent = json.detail || ("error " + res.status);
      error.hidden = false;
      return;
    }
    try { localStorage.setItem("microvpn:" + json.id + ":owner", json.owner_token); } catch (e) { /* private mode */ }
    location.reload();  // the room appears in the tree; the reader opens it when ready
  });

  // [delete] beside a room this browser created: the cookie is the authority, no token needed
  document.querySelectorAll("a[data-delete]").forEach(function (a) {
    a.addEventListener("click", async function (event) {
      event.preventDefault();
      var id = a.dataset.delete;
      var res = await fetch(root + "/r/" + id, { method: "DELETE" });
      if (res.ok || res.status === 404) {
        try { localStorage.removeItem("microvpn:" + id + ":owner"); localStorage.removeItem("microvpn:" + id); } catch (e) { /* ignore */ }
        location.reload();
      } else {
        a.textContent = "[delete failed]";
      }
    });
  });

  // [extend] beside a room this browser created: starts its lifetime again from now
  document.querySelectorAll("a[data-extend]").forEach(function (a) {
    a.addEventListener("click", async function (event) {
      event.preventDefault();
      var res = await fetch(root + "/r/" + a.dataset.extend + "/extend", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: "{}",
      });
      if (res.ok) {
        var json = await res.json();
        var clock = a.parentNode.querySelector("[data-expires-at]");
        if (clock) clock.dataset.expiresAt = json.expires_at;
      } else {
        a.textContent = "[extend failed]";
      }
    });
  });

  // "ends 21:30 UTC" becomes a live "ends in 58m 12s" in the reader's clock
  function tick() {
    document.querySelectorAll("[data-expires-at]").forEach(function (el) {
      var left = Math.max(0, Math.floor((new Date(el.dataset.expiresAt) - Date.now()) / 1000));
      var h = Math.floor(left / 3600), m = Math.floor((left % 3600) / 60), s = left % 60;
      el.textContent = left === 0 ? "now" : "in " + (h ? h + "h " : "") + m + "m " + s + "s";
    });
  }
  tick();
  setInterval(tick, 1000);
})();
