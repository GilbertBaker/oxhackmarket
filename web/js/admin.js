// Admin portal: post Cuppers matches and their results by hand.
// A match is a race: position "<Competition>: <Home> vs <Away>", description
// "<Round> · <Competition>", one outcome market per result ("<Home>", "Draw", "<Away>").
(function () {
  "use strict";
  const { $, $$, esc, pct, kickoff, rel, parseMatch, orderOutcomes, isDraw, outcomeLabel, initNav, errText, msg, loginHref } = App;
  let races = [];

  initNav("admin").then((u) => {
    if (!u || !u.is_admin) {
      $("#admin-root").innerHTML = `<div class="empty card">${u ? "This page is for admins only." : `<a href="${loginHref()}">Log in</a> with an admin account to manage matches.`}</div>`;
      return;
    }
    setupForm();
    load();
  });
  $("#reload").addEventListener("click", () => load());

  function note(html, kind) {
    $("#admin-msg").innerHTML = msg(html, kind);
  }

  async function load() {
    try {
      races = (await api.get("/api/races?status=all")).races || [];
    } catch (e) {
      note("Couldn't load matches: " + esc(errText(e)), "err");
      return;
    }
    const now = Date.now();
    const kicked = (r) => Date.parse(r.closes_at) <= now;
    const awaiting = races.filter((r) => r.status === "closed" || (r.status === "open" && kicked(r)))
      .sort((a, b) => Date.parse(a.closes_at) - Date.parse(b.closes_at));
    const upcoming = races.filter((r) => r.status === "open" && !kicked(r)).sort((a, b) => Date.parse(a.closes_at) - Date.parse(b.closes_at));
    const recent = races.filter((r) => r.status === "resolved").sort((a, b) => Date.parse(b.resolved_at || b.closes_at) - Date.parse(a.resolved_at || a.closes_at)).slice(0, 10);

    $("#count-results").textContent = awaiting.length ? String(awaiting.length) : "";
    $("#count-upcoming").textContent = upcoming.length ? String(upcoming.length) : "";
    $("#results").innerHTML = awaiting.length ? `<div class="race-grid">${awaiting.map(resultCard).join("")}</div>`
      : `<div class="empty card">Nothing waiting for a result.</div>`;
    $("#upcoming").innerHTML = upcoming.length ? listTable(upcoming, (r) =>
      `<button class="btn btn-ghost btn-xs" type="button" data-close="${r.id}">Stop trading</button>`)
      : `<div class="empty card">No upcoming matches. Post one with the form.</div>`;
    $("#recent").innerHTML = recent.length ? listTable(recent, (r) => {
      const w = r.candidates.filter((c) => (r.winners || []).includes(c.id));
      return `<span class="badge badge-won">✓ ${esc(w.map((c) => outcomeLabel(c.candidate)).join(", ") || "Settled")}</span>`;
    }) : `<div class="empty card">No results posted yet.</div>`;

    const comps = Array.from(new Set(races.map((r) => parseMatch(r.position).competition).filter(Boolean))).sort();
    $("#comp-list").innerHTML = comps.map((c) => `<option value="${esc(c)}"></option>`).join("");
  }

  function listTable(list, action) {
    return `<div class="card"><div class="table-wrap"><table class="table"><thead><tr><th>Match</th><th>Kick-off</th><th>Odds</th><th></th></tr></thead><tbody>${list.map((r) => {
      const m = parseMatch(r.position);
      const odds = orderOutcomes(r).map((c) => `${esc(isDraw(c.candidate) ? "Draw" : c.candidate)} ${pct(c.price)}`).join(" · ");
      return `<tr><td><a href="race.html?id=${r.id}"><b>${esc(m.title)}</b></a><div class="tiny faint">${esc(m.competition)}</div></td>
        <td class="small" style="white-space:nowrap">${esc(kickoff(r.closes_at))}<div class="tiny faint">${esc(rel(r.closes_at))}</div></td>
        <td class="small muted">${odds}</td><td class="right">${action(r)}</td></tr>`;
    }).join("")}</tbody></table></div></div>`;
  }

  function resultCard(r) {
    const m = parseMatch(r.position);
    const outs = orderOutcomes(r);
    return `<form class="card admin-card" data-result="${r.id}">
      <div class="match-top"><span class="comp">${esc(m.competition)}</span><span class="when">${esc(kickoff(r.closes_at))}</span></div>
      <h3 style="font-size:17px;margin:6px 0 4px">${esc(m.title)}</h3>
      <fieldset style="border:0;padding:0;margin:0"><legend class="small muted">What happened?</legend>
        <ul class="check-list">${outs.map((c) => `<li><label><input type="radio" name="w-${r.id}" value="${c.id}" required>
          ${esc(outcomeLabel(c.candidate))}</label><span class="tiny faint">market had ${pct(c.price)}</span></li>`).join("")}</ul>
      </fieldset>
      <button class="btn btn-primary btn-sm" type="submit">Post result</button>
      <div class="r-msg" aria-live="polite"></div>
    </form>`;
  }

  // Post result: stop trading first if the match is still open, then resolve.
  $("#results").addEventListener("submit", async (e) => {
    const form = e.target.closest("form[data-result]");
    if (!form) return;
    e.preventDefault();
    const r = races.find((x) => x.id === Number(form.dataset.result));
    const picked = form.querySelector("input[type=radio]:checked");
    const out = form.querySelector(".r-msg");
    if (!picked) { out.innerHTML = msg("Pick the result first.", "err"); return; }
    const winner = r.candidates.find((c) => c.id === Number(picked.value));
    if (!confirm(`Post the result of ${parseMatch(r.position).title} as "${outcomeLabel(winner.candidate)}"?\n\nThis settles every position and pays out. It can't be undone.`)) return;
    const btn = form.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      if (r.status === "open") await api.post(`/api/admin/races/${r.id}/close`);
      await api.post(`/api/admin/races/${r.id}/resolve`, { winner_market_ids: [winner.id] });
      note(`Result posted: ${esc(parseMatch(r.position).title)}, ${esc(outcomeLabel(winner.candidate))}.`, "ok");
      await load();
      App.refreshUser();
    } catch (err) {
      out.innerHTML = msg(esc(errText(err)), "err");
      btn.disabled = false;
    }
  });

  $("#upcoming").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-close]");
    if (!b) return;
    const r = races.find((x) => x.id === Number(b.dataset.close));
    if (!confirm(`Stop trading on ${parseMatch(r.position).title} now? Open orders will be cancelled.`)) return;
    b.disabled = true;
    try {
      await api.post(`/api/admin/races/${r.id}/close`);
      note(`Trading stopped on ${esc(parseMatch(r.position).title)}.`, "ok");
      await load();
    } catch (err) {
      note(esc(errText(err)), "err");
      b.disabled = false;
    }
  });

  // ------------------------------------------------------------ post a match
  function setupForm() {
    const kick = new Date(Date.now() + 2 * 86400e3);
    kick.setHours(14, 0, 0, 0);
    $("#nm-kick").value = toLocalInput(kick);
    ["#nm-home", "#nm-away", "#nm-draw", "#nm-comp"].forEach((s) => $(s).addEventListener("input", renderPriors));
    $("#nm-draw").addEventListener("change", renderPriors);
    $("#nm-priors").addEventListener("input", renderPriorSum);
    renderPriors();
  }
  function toLocalInput(d) {
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
  }
  function outcomeNames() {
    const home = $("#nm-home").value.trim() || "Home", away = $("#nm-away").value.trim() || "Away";
    return $("#nm-draw").checked ? [home, "Draw", away] : [home, away];
  }
  function renderPriors() {
    const names = outcomeNames();
    const old = $$("#nm-priors input").map((i) => Number(i.value));
    const defaults = names.length === 3 ? [40, 25, 35] : [50, 50];
    const keep = old.length === names.length;
    $("#nm-priors").innerHTML = names.map((n, i) => `<div><label for="nm-p${i}" style="margin-top:0">${esc(isDraw(n) ? "Draw" : n)}</label>
      <input class="input" id="nm-p${i}" type="number" min="1" max="99" step="1" value="${keep ? old[i] : defaults[i]}" inputmode="numeric"></div>`).join("");
    const comp = $("#nm-comp").value.trim() || "competition";
    if (!$("#nm-criteria").dataset.edited) {
      $("#nm-criteria").value = `Resolves on the full-time result as confirmed by the ${comp} organisers / Oxford University Sport. The outcome that happened resolves YES, every other outcome NO. A walkover counts as a win for the team awarded the match.`;
    }
    renderPriorSum();
  }
  $("#nm-criteria").addEventListener("input", (e) => { e.target.dataset.edited = "1"; });
  function renderPriorSum() {
    const sum = $$("#nm-priors input").reduce((a, i) => a + (Number(i.value) || 0), 0);
    $("#nm-prior-sum").innerHTML = `Total ${sum}%${sum === 100 ? "" : ' <span class="warn">(should add up to about 100%)</span>'}. The market maker opens quotes around these.`;
  }

  $("#new-match").addEventListener("submit", async (e) => {
    e.preventDefault();
    const out = $("#nm-msg");
    const term = $("#nm-term").value.trim(), comp = $("#nm-comp").value.trim(), round = $("#nm-round").value.trim();
    const home = $("#nm-home").value.trim(), away = $("#nm-away").value.trim();
    const kick = new Date($("#nm-kick").value);
    const priors = $$("#nm-priors input").map((i) => Number(i.value));
    const err = !/^(Michaelmas|Hilary|Trinity) \d{4}$/.test(term) ? "Term must look like \"Michaelmas 2026\"."
      : !comp ? "Enter the competition." : !home || !away ? "Enter both teams."
      : home.toLowerCase() === away.toLowerCase() ? "The two teams must be different."
      : isDraw(home) || isDraw(away) ? "\"Draw\" can't be a team name."
      : isNaN(kick) ? "Enter the kick-off time." : kick.getTime() <= Date.now() ? "Kick-off must be in the future."
      : priors.some((p) => !Number.isInteger(p) || p < 1 || p > 99) ? "Opening odds must be whole numbers from 1 to 99." : "";
    if (err) { out.innerHTML = msg(esc(err), "err"); return; }
    const names = outcomeNames();
    const body = {
      election: term, position: `${comp}: ${home} vs ${away}`, seats: 1,
      description: round ? `${round} · ${comp}` : comp,
      closes_at: kick.toISOString().replace(/\.\d{3}Z$/, "Z"),
      resolution_criteria: $("#nm-criteria").value.trim(),
      candidates: names.map((n, i) => ({ name: n, prior: priors[i] })),
    };
    const btn = e.target.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      const d = await api.post("/api/admin/races", body);
      out.innerHTML = msg(`Posted <a href="race.html?id=${d.race.id}">${esc(home)} vs ${esc(away)}</a>. Trading is open.`, "ok");
      $("#nm-home").value = "";
      $("#nm-away").value = "";
      renderPriors();
      load();
    } catch (er) {
      out.innerHTML = msg(esc(errText(er)), "err");
    } finally {
      btn.disabled = false;
    }
  });
})();
