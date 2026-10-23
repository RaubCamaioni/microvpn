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
    location.href = root + "/r/" + json.id;
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

  // "ends 21:30 UTC" becomes "ends in 58m" in the reader's clock
  document.querySelectorAll("[data-expires-at]").forEach(function (el) {
    var left = Math.max(0, Math.floor((new Date(el.dataset.expiresAt) - Date.now()) / 60000));
    el.textContent = left >= 60 ? "in " + Math.floor(left / 60) + "h " + (left % 60) + "m" : "in " + left + "m";
  });
})();
