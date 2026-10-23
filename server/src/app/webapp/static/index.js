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
})();
