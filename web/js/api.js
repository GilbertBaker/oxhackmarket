// Fetch wrapper for the /api backend (COLLAB 9). Every call resolves to the
// parsed JSON body or rejects with {status, error}.
// MOCK mode answers from web/js/mock.js instead: ?mock=1 turns it on (sticky
// via localStorage.mock), ?mock=0 turns it off, and file:// always uses it.
(function () {
  "use strict";
  const params = new URLSearchParams(location.search);
  try {
    if (params.get("mock") === "1") localStorage.setItem("mock", "1");
    if (params.get("mock") === "0") localStorage.removeItem("mock");
  } catch (e) { /* storage blocked: fall back to the query string */ }

  function isMock() {
    if (location.protocol === "file:") return true;
    try { return localStorage.getItem("mock") === "1"; } catch (e) { return params.get("mock") === "1"; }
  }

  async function request(method, path, body) {
    if (isMock()) {
      if (!window.MOCK) throw { status: 500, error: "mock.js is not loaded" };
      return window.MOCK.handle(method, path, body);
    }
    const opts = { method, credentials: "same-origin", headers: { Accept: "application/json" } };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    let res;
    try {
      res = await fetch(path, opts);
    } catch (e) {
      throw { status: 0, error: "Can't reach the server" };
    }
    let data = null;
    try { data = await res.json(); } catch (e) { /* empty or non-JSON body */ }
    if (!res.ok) throw { status: res.status, error: (data && data.error) || res.statusText || "Request failed" };
    return data;
  }

  window.api = {
    get: (path) => request("GET", path),
    post: (path, body) => request("POST", path, body === undefined ? {} : body),
    del: (path) => request("DELETE", path),
    isMock,
  };
})();
