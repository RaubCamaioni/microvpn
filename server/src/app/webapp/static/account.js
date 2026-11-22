// every page, when signed in: [sign out] ends this browser's session and shows the page signed out
(function () {
  "use strict";
  var link = document.getElementById("sign-out");
  if (!link) return;
  link.addEventListener("click", async function (event) {
    event.preventDefault();
    var root = document.body.dataset.rootPath || "";
    try { await fetch(root + "/auth/logout", { method: "POST" }); } catch (e) { /* the reload shows the truth */ }
    location.reload();
  });
})();
