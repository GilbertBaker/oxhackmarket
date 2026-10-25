// Match page (race.html?id=N): every outcome's odds, history chart and Gemini brief.
(function () {
  "use strict";
  const { $, $$, esc, pct, yesNo, changeHtml, compact, countdown, kickoff, rel, parseMatch, orderOutcomes, isDraw,
    outcomeLabel, sportOf, poll, initNav, qs, errText, msg, PALETTE, lineChart, setChartData, timeLabel, historySince } = App;

  const id = Number(qs("id"));
  let race = null, bucket = "5m", chart = null;
  const colors = {};
  initNav("markets");

  if (!id) { notFound(); return; }

  function notFound(text) {
    $("#race-root").innerHTML = `<div class="empty card">${esc(text || "Match not found.")} <a href="index.html">Back to all matches</a></div>`;
  }

  async function loadRace() {
    const d = await api.get(`/api/races/${id}`);
    race = d.race;
    if (race.kind === "binary" && race.candidates.length) {   // one market: its page is the race page
      location.replace(`market.html?id=${race.candidates[0].id}`);
      return;
    }
    // Colours follow market id, so they don't swap when the order changes.
    race.candidates.slice().sort((a, b) => a.id - b.id).forEach((c, i) => { colors[c.id] = colors[c.id] || PALETTE[i % PALETTE.length]; });
    renderHead();
    renderTable();
  }

  function renderHead() {
    const isMatch = !race.kind || race.kind === "match";
    const s = sportOf(isMatch ? parseMatch(race.position).competition || race.position : "", race);
    const m = isMatch ? parseMatch(race.position) : { competition: s.name, title: race.position };
    const round = isMatch ? (race.description || "").split(" · ")[0] : "";
    document.title = `${m.title} · OxMarket`;
    $("#crumb-comp").textContent = m.competition || race.election;
    $("#race-icon").textContent = s.icon;
    $("#race-title").textContent = m.title;
    const badge = { open: '<span class="badge badge-open">Trading open</span>', closed: '<span class="badge badge-closed">Awaiting result</span>', resolved: '<span class="badge badge-resolved">Resolved</span>' }[race.status] || "";
    $("#race-meta").innerHTML = `${badge}<span>${esc(m.competition)}${round ? " · " + esc(round) : ""}</span><span>· ${esc(race.election)}</span>`;
    const [inLbl, pastLbl, stops] = isMatch ? ["Kick-off in", "Kicked off", "Trading stops at kick-off."] : ["Closes in", "Closed", "Trading stops then."];
    $("#closes-box").innerHTML = race.status === "open"
      ? `<div class="lbl">${inLbl}</div><div class="big num" data-countdown="${esc(race.closes_at)}">${esc(countdown(race.closes_at))}</div><div class="tiny faint">${esc(kickoff(race.closes_at))}. ${stops}</div>`
      : `<div class="lbl">${pastLbl}</div><div class="big" style="font-size:18px">${esc(kickoff(race.closes_at))}</div>`;
    if (!isMatch && race.description) $("#race-meta").innerHTML += `<span>· ${esc(race.description)}</span>`;

    const banner = $("#result-banner");
    if (race.status === "resolved") {
      const w = race.candidates.filter((c) => (race.winners || []).includes(c.id));
      banner.className = "banner";
      banner.innerHTML = `✓ Result: ${esc(w.map((c) => outcomeLabel(c.candidate, race)).join(", ") || "posted")}`;
      banner.hidden = false;
    } else if (race.status === "closed") {
      banner.className = "banner closed";
      banner.textContent = "Trading has stopped. Waiting for the admin to post the result.";
      banner.hidden = false;
    } else {
      banner.hidden = true;
    }

    $("#rules").innerHTML = `<dt>${isMatch ? "Competition" : "Category"}</dt><dd>${esc(m.competition || "–")}</dd>
      ${round ? `<dt>Round</dt><dd>${esc(round)}</dd>` : ""}
      ${race.seats > 1 ? `<dt>Winners</dt><dd>${race.seats}</dd>` : ""}
      <dt>${isMatch ? "Kick-off" : "Closes"}</dt><dd>${esc(kickoff(race.closes_at))}</dd>
      <dt>Term</dt><dd>${esc(race.election)}</dd>
      ${race.source_url ? `<dt>Source</dt><dd><a href="${esc(race.source_url)}" target="_blank" rel="noopener">${esc(race.source_name || "Link")}</a></dd>` : ""}`;
    $("#criteria").textContent = race.resolution_criteria || "";
  }

  function renderTable() {
    const outs = orderOutcomes(race);
    const resolved = race.status === "resolved", open = race.status === "open";
    const winners = new Set(race.winners || []);
    const fav = outs.reduce((a, c) => (!a || c.price > a.price ? c : a), null);
    $("#cand-body").innerHTML = outs.map((c) => {
      const name = outcomeLabel(c.candidate, race, true);
      let action = "";
      if (open) {
        action = `<a class="btn btn-yes btn-xs" href="market.html?id=${c.id}&amp;outcome=YES" aria-label="Buy YES on ${esc(outcomeLabel(c.candidate, race))}">Yes ${c.price}p</a>
          <a class="btn btn-no btn-xs" href="market.html?id=${c.id}&amp;outcome=NO" aria-label="Buy NO on ${esc(outcomeLabel(c.candidate, race))}">No ${100 - c.price}p</a>`;
      } else if (resolved) {
        action = winners.has(c.id) ? '<span class="badge badge-won">✓ Result</span>' : '<span class="badge badge-lost">No</span>';
      }
      return `<tr class="${!resolved && c === fav ? "is-fav" : ""}">
        <td><span class="cand-cell"><span class="swatch" style="background:${colors[c.id]}" aria-hidden="true"></span><a href="market.html?id=${c.id}">${esc(name)}</a>${!resolved && c === fav ? '<span class="fav-tag">Favourite</span>' : ""}</span></td>
        <td class="right num"><b>${pct(c.price)}</b><div class="tiny faint">${yesNo(c.price)}</div></td>
        <td>${changeHtml(c.change_24h)}</td>
        <td class="right num">${compact(c.volume_24h)}</td>
        <td class="right" style="white-space:nowrap">${action}</td></tr>`;
    }).join("");
    $("#overround").textContent = `Book total ${race.overround}% (sum of all outcome prices; above 100% is the market's margin).`;
  }

  async function loadHistory() {
    const since = historySince(bucket);
    const d = await api.get(`/api/races/${id}/history?bucket=${bucket}${since ? "&since=" + encodeURIComponent(since) : ""}`);
    const series = d.series || [];
    if (!chart) chart = lineChart($("#race-chart"), { legend: true });
    // Align every outcome on the union of timestamps.
    const times = Array.from(new Set(series.flatMap((s) => s.points.map((p) => p.t)))).sort();
    const order = race ? orderOutcomes(race).map((c) => c.id) : [];
    series.sort((a, b) => order.indexOf(a.market_id) - order.indexOf(b.market_id));
    const datasets = series.map((s) => {
      const byT = new Map(s.points.map((p) => [p.t, p.close]));
      return { label: outcomeLabel(s.candidate, race, true), color: colors[s.market_id] || "#64748b", data: times.map((t) => (byT.has(t) ? byT.get(t) : null)) };
    });
    setChartData(chart, times.map((t) => timeLabel(t, bucket)), datasets);
    $("#chart-empty").hidden = times.length > 0;
  }

  $$("#bucket button").forEach((b) => b.addEventListener("click", () => {
    bucket = b.dataset.bucket;
    $$("#bucket button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    loadHistory().catch((e) => console.warn(errText(e)));
  }));

  function renderBrief(b) {
    $("#brief-text").innerHTML = b
      ? `<p>${esc(b.text)}</p><p class="tiny faint">Updated ${esc(rel(b.created_at))}</p>`
      : `<p class="muted">No analysis yet. Gemini writes one once the odds start moving.</p>`;
  }
  async function loadBrief() {
    try { renderBrief((await api.get(`/api/races/${id}/brief`)).brief); } catch (e) { renderBrief(null); }
  }
  $("#brief-refresh").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    $("#brief-msg").innerHTML = "";
    try {
      renderBrief((await api.post(`/api/races/${id}/brief/refresh`)).brief);
    } catch (err) {
      $("#brief-msg").innerHTML = msg(err.status === 401 ? `<a href="${App.loginHref()}">Log in</a> to refresh the analysis.` : esc(errText(err)), "err");
    } finally {
      btn.disabled = false;
    }
  });

  loadRace().then(() => {
    poll(loadRace, 2000, false);
    poll(loadHistory, 10000);
    loadBrief();
  }, (e) => notFound(e.status === 404 ? "Match not found." : "Couldn't load this match: " + errText(e)));
})();
