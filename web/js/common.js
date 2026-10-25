// Shared helpers for every page: navbar, formatting, match parsing, polling,
// live countdowns and Chart.js setup. Exposed as window.App.
(function () {
  "use strict";
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  // ------------------------------------------------------------ formatting
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  }
  function pence(p) {
    const n = Math.round(Number(p) || 0);
    const s = (Math.abs(n) / 100).toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return (n < 0 ? "-£" : "£") + s;
  }
  const signedPence = (p) => (p > 0 ? "+" : "") + pence(p);
  const pct = (p) => (p == null ? "–" : Math.round(p) + "%");
  const yesNo = (p) => `YES ${p}p · NO ${100 - p}p`;
  function compact(n) {
    n = Number(n) || 0;
    if (n >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, "") + "m";
    if (n >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, "") + "k";
    return String(n);
  }
  const isoNoMs = (ms) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z");

  function parts(iso) {
    const ms = new Date(iso).getTime() - Date.now();
    if (!(ms > 0)) return null;
    const s = Math.floor(ms / 1000);
    return { d: Math.floor(s / 86400), h: Math.floor((s % 86400) / 3600), m: Math.floor((s % 3600) / 60), s: s % 60 };
  }
  const pad = (n) => String(n).padStart(2, "0");
  function countdown(iso) {
    const p = parts(iso);
    if (!p) return "Started";
    if (p.d > 0) return `${p.d}d ${pad(p.h)}h ${pad(p.m)}m`;
    if (p.h > 0) return `${p.h}h ${pad(p.m)}m ${pad(p.s)}s`;
    return `${p.m}m ${pad(p.s)}s`;
  }
  function rel(iso) {
    const diff = (Date.now() - new Date(iso).getTime()) / 1000;
    const a = Math.abs(diff);
    const v = a < 60 ? null : a < 3600 ? Math.round(a / 60) + "m" : a < 86400 ? Math.round(a / 3600) + "h" : Math.round(a / 86400) + "d";
    if (!v) return "just now";
    return diff >= 0 ? v + " ago" : "in " + v;
  }
  const dateTime = (iso) => new Date(iso).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  const kickoff = (iso) => new Date(iso).toLocaleString("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  const clock = (iso) => new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  function timeLabel(iso, bucket) {
    return bucket === "1h" ? dateTime(iso) : clock(iso);
  }
  function dayKey(iso) {
    const d = new Date(iso);
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }
  function dayLabel(iso) {
    const today = dayKey(Date.now());
    const k = dayKey(iso);
    if (k === today) return "Today";
    if (k === dayKey(Date.now() + 86400e3)) return "Tomorrow";
    if (k === dayKey(Date.now() - 86400e3)) return "Yesterday";
    return new Date(iso).toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
  }
  function changeHtml(c) {
    c = Math.round(c || 0);
    if (c > 0) return `<span class="chg up" title="Up ${c} points in 24h">▲ ${c}</span>`;
    if (c < 0) return `<span class="chg down" title="Down ${-c} points in 24h">▼ ${-c}</span>`;
    return `<span class="chg flat" title="No change in 24h">– 0</span>`;
  }
  const qs = (name) => new URLSearchParams(location.search).get(name);
  const errText = (e) => (e && (e.error || e.message)) || "Something went wrong";
  const msg = (html, kind) => `<div class="msg msg-${kind || "info"}">${html}</div>`;
  const SOURCES = { cherwell: "Cherwell", oxstu: "Oxford Student", isis: "The Isis", ous: "OU Sport", union: "Oxford Union" };
  const sourceName = (k) => SOURCES[k] || k || "";

  // ------------------------------------------------------------ matches
  // A Cuppers match is a race: position "<Competition>: <Home> vs <Away>",
  // one candidate market per outcome ("<Home>", "Draw", "<Away>").
  function parseMatch(position) {
    const text = String(position || "");
    let m = text.match(/^(.*?):\s*(.+?)\s+(?:vs\.?|v\.?)\s+(.+)$/i);
    if (m) return { competition: m[1].trim(), home: m[2].trim(), away: m[3].trim(), title: `${m[2].trim()} vs ${m[3].trim()}` };
    m = text.match(/^(.+?)\s+(?:vs\.?|v\.?)\s+(.+)$/i);
    if (m) return { competition: "", home: m[1].trim(), away: m[2].trim(), title: `${m[1].trim()} vs ${m[2].trim()}` };
    return { competition: "", home: null, away: null, title: text };
  }
  const isDraw = (name) => /^draw$/i.test(String(name || "").trim());
  // Outcomes in fixture order (home, draw, away); anything else after, by price.
  function orderOutcomes(race, cands) {
    cands = (cands || race.candidates || []).slice();
    const name = (c) => c.candidate;
    const m = parseMatch(race.position);
    const home = cands.find((c) => m.home && name(c) === m.home);
    const away = cands.find((c) => m.away && name(c) === m.away);
    if (!home || !away) return cands.sort((a, b) => (b.price || 0) - (a.price || 0));
    const draw = cands.find((c) => isDraw(name(c)));
    const rest = cands.filter((c) => c !== home && c !== away && c !== draw).sort((a, b) => (b.price || 0) - (a.price || 0));
    return [home, draw, away, ...rest].filter(Boolean);
  }
  // Market kinds (race.kind): "match" (Cuppers, the default), "binary" (one YES/NO question,
  // a single market named "Yes") and "multi" (named outcomes). Pass the race when you have it.
  const isBinary = (name, race) => (race && race.kind ? race.kind === "binary" : name === "Yes");
  function outcomeLabel(name, race, short) {
    if (isBinary(name, race)) return "Yes";
    if (race && race.kind === "multi") return name;
    if (isDraw(name)) return "Draw";
    return short ? name : `${name} win`;
  }
  function outcomeNoLabel(name, race) {
    if (isBinary(name, race)) return "No";
    if (race && race.kind === "multi") return `Not ${name}`;
    return isDraw(name) ? "Not a draw" : `${name} doesn't win`;
  }
  const raceTitle = (r) => (!r ? "" : r.kind && r.kind !== "match" ? r.position : parseMatch(r.position).title);

  const SPORTS = [
    ["Football", "⚽", /football|soccer|futsal/i], ["Rugby", "🏉", /rugby/i], ["Hockey", "🏑", /hockey/i],
    ["Netball", "🏐", /netball/i], ["Lacrosse", "🥍", /lacrosse/i], ["Rowing", "🚣", /rowing|regatta|torpids|eights/i],
    ["Cricket", "🏏", /cricket/i], ["Basketball", "🏀", /basketball/i], ["Tennis", "🎾", /tennis/i],
    ["Badminton", "🏸", /badminton/i], ["Volleyball", "🏐", /volleyball/i], ["Ultimate", "🥏", /ultimate|frisbee/i],
    ["Squash", "🎾", /squash/i], ["Darts", "🎯", /darts/i], ["Pool", "🎱", /pool|snooker/i], ["Chess", "♟️", /chess/i],
  ];
  // Category icons for non-match markets; categories, sports and their patterns come from
  // /api/meta (the reference tables) once loaded, the lists above are the offline fallback.
  const CAT_ICONS = { news: "📰", union: "🏛️", university: "🎓", other: "❓", cuppers: "🏆" };
  function sportOf(position, race) {
    if (race && race.kind && race.kind !== "match") {
      const cat = (App.meta && App.meta.categories.find((c) => c.slug === race.category)) || null;
      return { name: cat ? cat.name : race.category || "Other", icon: CAT_ICONS[race.category] || "❓" };
    }
    for (const s of SPORTS) if (s[2].test(position || "")) return { name: s[0], icon: s[1] };
    return { name: "Other", icon: "🏆" };
  }
  let metaPromise = null;
  function loadMeta(force) {
    if (!metaPromise || force) {
      metaPromise = api.get("/api/meta").then((m) => {
        App.meta = m;
        const sports = (m.sports || []).map((s) => {
          try { return [s.name, s.icon, new RegExp(s.pattern, "i")]; } catch (e) { return null; }
        }).filter(Boolean);
        if (sports.length) SPORTS.splice(0, SPORTS.length, ...sports);
        return m;
      }, (e) => { metaPromise = null; throw e; });
    }
    return metaPromise;
  }

  // A position in the outcome's own terms (YES or NO shares).
  function positionView(p) {
    const shares = Math.abs(p.qty);
    const yes = p.qty > 0;
    const outPrice = yes ? p.price : 100 - p.price;
    const cost = yes ? p.net_cost : shares * 100 + p.net_cost;
    const value = shares * outPrice;
    return { label: yes ? "YES" : "NO", shares, outPrice, cost, value, pnl: value - cost, avg: shares ? cost / shares : 0 };
  }

  // ------------------------------------------------------------ polling / countdowns
  function poll(fn, ms, runNow) {
    let busy = false;
    const run = async () => {
      if (document.hidden || busy) return;
      busy = true;
      try { await fn(); } catch (e) { console.warn("Refresh failed:", errText(e)); } finally { busy = false; }
    };
    if (runNow !== false) run();
    const id = setInterval(run, ms);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) run(); });
    return { stop: () => clearInterval(id), now: run };
  }

  function tickCountdowns() {
    $$("[data-countdown]").forEach((el) => { el.textContent = countdown(el.dataset.countdown); });
    $$("[data-countdown-units]").forEach((el) => {
      const iso = el.dataset.countdownUnits;
      const p = parts(iso);
      el.setAttribute("aria-label", p ? countdown(iso) : "Started");
      el.innerHTML = p
        ? [["d", "Days"], ["h", "Hrs"], ["m", "Min"], ["s", "Sec"]]
          .map(([k, l]) => `<div class="cd-unit"><b class="num">${pad(p[k])}</b><span>${l}</span></div>`).join("")
        : `<div class="cd-unit"><b>Live</b><span>now</span></div>`;
    });
  }
  setInterval(tickCountdowns, 1000);

  // ------------------------------------------------------------ navbar
  const App = { user: null, ready: Promise.resolve(null) };
  const LINKS = [
    ["markets", "index.html", "Matches"], ["timemachine", "timemachine.html", "Time Machine"],
    ["leaderboard", "leaderboard.html", "Leaderboard"], ["portfolio", "portfolio.html", "Portfolio"],
  ];
  function initNav(active) {
    const host = $("#nav");
    if (host) {
      host.innerHTML = `<header class="topbar"><div class="wrap topbar-inner">
        <a class="brand" href="index.html" aria-label="OxMarket home"><span class="brand-mark" aria-hidden="true">OX</span><span>OxMarket</span><span class="brand-sub">Cuppers</span></a>
        <nav class="nav-links" aria-label="Main">${LINKS.map(([k, href, text]) =>
          `<a href="${href}"${k === active ? ' class="active" aria-current="page"' : ""}>${text}</a>`).join("")}
          <a href="admin.html" class="nav-admin${active === "admin" ? " active" : ""}" hidden>Admin</a></nav>
        <div class="nav-user" id="nav-user"></div></div></header>`;
    }
    setUser(null, true);
    App.ready = api.get("/api/me").then((d) => d.user, () => null).then((u) => { setUser(u); return u; });
    tickCountdowns();
    return App.ready;
  }
  function setUser(u, loading) {
    App.user = u || null;
    const box = $("#nav-user");
    if (!box) return;
    const mock = api.isMock()
      ? `<span class="mock-pill" title="Showing example data. Open any page with ?mock=0 to use the live backend.">Mock data</span>` : "";
    const admin = $(".nav-admin");
    if (admin) admin.hidden = !(u && u.is_admin);
    if (loading) { box.innerHTML = mock; return; }
    if (!u) {
      box.innerHTML = mock + `<a class="btn btn-ghost btn-sm" href="login.html">Log in</a><a class="btn btn-primary btn-sm" href="login.html#register">Sign up</a>`;
      return;
    }
    box.innerHTML = mock + `<a class="nav-cash" href="portfolio.html" title="Available to trade: ${pence(u.available)}"><span class="lbl">Cash</span><b class="num">${pence(u.cash)}</b></a>
      <span class="avatar" aria-hidden="true">${esc(u.username.charAt(0).toUpperCase())}</span><span class="nav-name">${esc(u.username)}</span>
      <button class="btn btn-ghost btn-sm" type="button" id="logout-btn">Log out</button>`;
    $("#logout-btn").addEventListener("click", async () => {
      try { await api.post("/api/logout"); } catch (e) { /* already logged out */ }
      location.href = "index.html";
    });
  }
  async function refreshUser() {
    try { const d = await api.get("/api/me"); setUser(d.user); } catch (e) { setUser(null); }
    return App.user;
  }
  const loginHref = () => "login.html?next=" + encodeURIComponent((location.pathname.split("/").pop() || "index.html") + location.search);

  // ------------------------------------------------------------ charts
  const PALETTE = ["#0b2a66", "#e07a2d", "#2f7ae5", "#1e9b59", "#8b5cf6", "#d6423b", "#0e9f9f", "#b8860b", "#64748b", "#db2777"];
  function lineChart(canvas, opts) {
    opts = opts || {};
    if (!window.Chart) {
      const p = document.createElement("p");
      p.className = "empty";
      p.textContent = "Chart unavailable (Chart.js didn't load).";
      canvas.replaceWith(p);
      return null;
    }
    const fmt = (v) => (opts.money ? pence(v).replace(/\.00$/, "") : Math.round(v) + "%");
    return new Chart(canvas, {
      type: "line",
      data: { labels: [], datasets: [] },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: !!opts.legend, position: "bottom", labels: { boxWidth: 8, boxHeight: 8, usePointStyle: true, color: "#56627a", font: { size: 12 } } },
          tooltip: { callbacks: { label: (c) => ` ${c.dataset.label}: ${c.parsed.y == null ? "–" : fmt(c.parsed.y)}` } },
        },
        scales: {
          x: { grid: { display: false }, border: { display: false }, ticks: { maxTicksLimit: 6, autoSkip: true, maxRotation: 0, color: "#8992a5" } },
          y: {
            position: "right", min: opts.money ? undefined : 0, max: opts.money ? undefined : 100,
            grid: { color: "#edf0f5" }, border: { display: false },
            ticks: { color: "#8992a5", maxTicksLimit: 6, callback: fmt },
          },
        },
        elements: { point: { radius: 0, hoverRadius: 4 }, line: { borderWidth: 2, tension: 0.2 } },
      },
    });
  }
  function setChartData(chart, labels, datasets) {
    if (!chart) return;
    chart.data.labels = labels;
    chart.data.datasets = datasets.map((d) => ({
      label: d.label, data: d.data, borderColor: d.color, backgroundColor: d.fill ? d.color + "14" : d.color,
      fill: !!d.fill, spanGaps: true, pointBackgroundColor: d.color,
    }));
    chart.update("none");
  }
  function historySince(bucket) {
    if (bucket === "1m") return isoNoMs(Date.now() - 3 * 3600e3);
    if (bucket === "5m") return isoNoMs(Date.now() - 2 * 86400e3);
    return null;
  }

  window.App = Object.assign(App, {
    $, $$, esc, pence, signedPence, pct, yesNo, compact, isoNoMs, countdown, rel, dateTime, kickoff, clock, timeLabel,
    dayKey, dayLabel, changeHtml, qs, errText, msg, sourceName, parseMatch, isDraw, orderOutcomes, outcomeLabel,
    outcomeNoLabel, raceTitle, isBinary, loadMeta, sportOf, positionView, poll, initNav, setUser, refreshUser, loginHref, PALETTE, lineChart,
    setChartData, historySince, tickCountdowns,
  });
})();
