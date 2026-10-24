// Login / register tabs. On success, go to ?next= (same-site page) or the home page.
(function () {
  "use strict";
  const { $, esc, qs, errText, msg, initNav } = App;
  let mode = location.hash === "#register" ? "register" : "login";
  initNav("");

  function setMode(m) {
    mode = m;
    $("#tab-login").setAttribute("aria-selected", String(m === "login"));
    $("#tab-register").setAttribute("aria-selected", String(m === "register"));
    $("#auth-title").textContent = m === "login" ? "Welcome back" : "Create an account";
    $("#auth-sub").textContent = m === "login" ? "Log in to trade Cuppers matches." : "Start with £1,000 of play money.";
    $("#auth-submit").textContent = m === "login" ? "Log in" : "Sign up";
    $("#password").autocomplete = m === "login" ? "current-password" : "new-password";
    $("#auth-hint").hidden = m === "login";
    $("#auth-msg").innerHTML = "";
  }
  $("#tab-login").addEventListener("click", () => setMode("login"));
  $("#tab-register").addEventListener("click", () => setMode("register"));
  setMode(mode);

  function nextUrl() {
    const n = qs("next") || "";
    // Only relative page links, never another origin.
    return /^[a-z]+\.html(\?[^#]*)?$/i.test(n) ? n : "index.html";
  }

  $("#auth-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const username = $("#username").value.trim(), password = $("#password").value;
    if (!/^[A-Za-z0-9_]{3,20}$/.test(username)) { $("#auth-msg").innerHTML = msg("Username must be 3–20 letters, digits or _.", "err"); return; }
    if (password.length < 6) { $("#auth-msg").innerHTML = msg("Password must be at least 6 characters.", "err"); return; }
    const btn = $("#auth-submit");
    btn.disabled = true;
    try {
      await api.post(mode === "login" ? "/api/login" : "/api/register", { username, password });
      location.href = nextUrl();
    } catch (err) {
      $("#auth-msg").innerHTML = msg(esc(errText(err)), "err");
      btn.disabled = false;
    }
  });
})();
