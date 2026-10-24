// Home: the term's Cuppers fixtures grouped by day, one card per match.
(function () {
  "use strict";
  const { $, $$, esc, pct, changeHtml, compact, countdown, kickoff, clock, rel, dayKey, dayLabel, parseMatch,
    orderOutcomes, isDraw, sportOf, sourceName, poll, initNav } = App;

  let all = [], status = "open", sport = "All", query = "";
  initNav("markets");

  $$("#status-tabs button").forEach((b) => b.addEventListener("click", () => {
    status = b.dataset.status;
    $$("#status-tabs button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    render();
  }));
  $("#search").addEventListener("input", (e) => { query = e.target.value.trim().toLowerCase(); render(); });
  $("#sport-pills").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-sport]");
    if (!b) return;
    sport = b.dataset.sport;
    render();
  });

  const compOf = (r) => parseMatch(r.position).competition || r.position;
  const roundOf = (r) => (r.description || "").split(" · ")[0];
  const sportName = (r) => sportOf(compOf(r)).name;

  async function loadRaces() {
    try {
      const d = await api.get("/api/races?status=all");
      all = d.races || [];
    } catch (e) {
      if (!all.length) $("#fixtures").innerHTML = `<div class="empty card">Couldn't load matches: ${esc(App.errText(e))}</div>`;
      throw e;
    }
    render();
    renderHero();
    renderMovers();
  }

  function renderHero() {
    const open = all.filter((r) => r.status === "open").sort((a, b) => Date.parse(a.closes_at) - Date.parse(b.closes_at));
    const next = open[0];
    const cd = $("#hero-countdown");
    if (next) {
      cd.dataset.countdownUnits = next.closes_at;
      $("#hero-next").innerHTML = `Next kick-off: <a href="race.html?id=${next.id}">${esc(parseMatch(next.position).title)}</a>, ${esc(kickoff(next.closes_at))}`;
    } else {
      delete cd.dataset.countdownUnits;
      cd.innerHTML = "";
      $("#hero-next").textContent = "No upcoming matches posted yet.";
    }
    App.tickCountdowns();
    const term = (next || all[0] || {}).election;
    if (term) $("#hero-title").textContent = `${term} Cuppers`;
    ["open", "closed", "resolved"].forEach((s) => {
      const n = all.filter((r) => r.status === s).length;
      $("#count-" + s).textContent = n ? String(n) : "";
    });
  }

  function renderPills(list) {
    const sports = Array.from(new Set(list.map(sportName))).sort();
    if (sport !== "All" && !sports.includes(sport)) sport = "All";
    $("#sport-pills").innerHTML = ["All", ...sports].map((s) => {
      const icon = s === "All" ? "" : sportOf(s).icon + " ";
      return `<button type="button" class="pill" data-sport="${esc(s)}" aria-pressed="${s === sport}">${icon}${esc(s)}</button>`;
    }).join("");
  }

  function render() {
    const inStatus = all.filter((r) => r.status === status);
    renderPills(inStatus);
    const list = inStatus.filter((r) => (sport === "All" || sportName(r) === sport) &&
      (!query || r.position.toLowerCase().includes(query) || (r.candidates || []).some((c) => c.candidate.toLowerCase().includes(query))));
    const host = $("#fixtures");
    if (!list.length) {
      const what = { open: "No upcoming matches", closed: "No matches awaiting a result", resolved: "No results yet" }[status];
      host.innerHTML = `<div class="empty card">${what}${query || sport !== "All" ? " match your filters" : ""}.</div>`;
      return;
    }
    list.sort((a, b) => (status === "resolved" ? -1 : 1) * (Date.parse(a.closes_at) - Date.parse(b.closes_at)));
    const groups = [];
    list.forEach((r) => {
      const k = dayKey(r.closes_at);
      if (!groups.length || groups[groups.length - 1].k !== k) groups.push({ k, iso: r.closes_at, races: [] });
      groups[groups.length - 1].races.push(r);
    });
    host.innerHTML = groups.map((g) => `<div class="day-head"><h2>${esc(dayLabel(g.iso))}</h2><span class="count">${g.races.length} ${g.races.length === 1 ? "match" : "matches"}</span></div>
      <div class="race-grid">${g.races.map(card).join("")}</div>`).join("");
  }

  function card(r) {
    const m = parseMatch(r.position);
    const s = sportOf(compOf(r));
    const outs = orderOutcomes(r);
    const resolved = r.status === "resolved", open = r.status === "open";
    const winners = new Set(r.winners || []);
    const fav = resolved ? null : outs.reduce((a, c) => (!a || c.price > a.price ? c : a), null);
    const round = roundOf(r);
    let when;
    if (open) {
      const soon = Date.parse(r.closes_at) - Date.now() < 86400e3;
      when = soon ? `<span class="when live">Kick-off in <span data-countdown="${esc(r.closes_at)}">${esc(countdown(r.closes_at))}</span></span>`
        : `<span class="when">${esc(clock(r.closes_at))}</span>`;
    } else {
      when = `<span class="when">${resolved ? "Final" : "Awaiting result"}</span>`;
    }
    const rows = outs.map((c) => {
      const won = winners.has(c.id);
      const cls = [c === fav ? "is-fav" : "", resolved && !won ? "is-lost" : ""].join(" ").trim();
      const odds = resolved
        ? (won ? `<span class="badge badge-won">✓ Result</span>` : "")
        : `<b class="num">${pct(c.price)}</b>${changeHtml(c.change_24h)}`;
      return `<li class="oc-row ${cls}"><a class="oc-name" href="market.html?id=${c.id}">${esc(isDraw(c.candidate) ? "Draw" : c.candidate)}</a>
        <span class="oc-odds">${odds}</span>
        <span class="bar" aria-hidden="true"><span style="width:${resolved ? (won ? 100 : 0) : c.price}%"></span></span></li>`;
    }).join("");
    const buttons = open ? `<div class="oc-buttons" style="--n:${outs.length}">${outs.map((c) => {
      const label = isDraw(c.candidate) ? "Draw" : c.candidate;
      return `<a class="oc-buy" href="market.html?id=${c.id}&amp;outcome=YES" aria-label="Buy YES on ${esc(App.outcomeLabel(c.candidate))} at ${c.price}p"><span>${esc(label)}</span><b class="num">${c.price}p</b></a>`;
    }).join("")}</div>` : "";
    const vol = outs.reduce((a, c) => a + (c.volume_24h || 0), 0);
    return `<article class="card race-card">
      <div class="match-top"><span class="comp"><span aria-hidden="true">${s.icon}</span>${esc(m.competition || r.election)}${round ? " · " + esc(round) : ""}</span>${when}</div>
      <a class="match-title" href="race.html?id=${r.id}">${esc(m.title)}</a>
      <ul class="oc-rows">${rows}</ul>
      ${buttons}
      <div class="rc-foot"><span>${compact(vol)} shares traded (24h)</span><span title="Sum of all outcome prices">book total ${r.overround}%</span></div>
    </article>`;
  }

  function renderMovers() {
    const items = all.filter((r) => r.status === "open")
      .flatMap((r) => (r.candidates || []).map((c) => ({ r, c })))
      .filter((x) => x.c.change_24h)
      .sort((a, b) => Math.abs(b.c.change_24h) - Math.abs(a.c.change_24h))
      .slice(0, 6);
    $("#movers").innerHTML = items.length ? items.map(({ r, c }) => `<li>
        <span class="row"><a href="market.html?id=${c.id}">${esc(App.outcomeLabel(c.candidate))}</a><span class="spacer"></span><b class="num">${pct(c.price)}</b>${changeHtml(c.change_24h)}</span>
        <span class="muted tiny">${esc(parseMatch(r.position).title)}</span></li>`).join("")
      : `<li class="muted small">Nothing has moved in the last 24 hours.</li>`;
  }

  async function loadDigest() {
    const el = $("#digest");
    try {
      const d = await api.get("/api/digest");
      const g = d.digest;
      if (!g || !g.text) { el.hidden = true; return; }
      el.innerHTML = `<div class="digest-head"><b>Today's odds</b><span class="ai-label">✦ Generated by Gemini</span><span class="faint tiny">${esc(rel(g.created_at))}</span></div><p>${esc(g.text)}</p>`;
      el.hidden = false;
    } catch (e) { el.hidden = true; }
  }

  async function loadArticles() {
    try {
      const d = await api.get("/api/articles?limit=8");
      const list = d.articles || [];
      $("#headlines-card").hidden = !list.length;
      $("#headlines").innerHTML = list.map((a) => `<li><span class="src">${esc(sourceName(a.source))}</span>
        <a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.title)}</a><span class="faint tiny">${esc(rel(a.published_at))}</span></li>`).join("");
    } catch (e) { $("#headlines-card").hidden = true; }
  }

  poll(loadRaces, 10000);
  poll(loadDigest, 60000);
  poll(loadArticles, 60000);
})();
