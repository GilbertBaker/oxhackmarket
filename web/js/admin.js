// Admin portal: create markets of every kind (Cuppers matches, yes/no questions,
// multiple choice), approve drafts, post results, turn scraped news into markets, and
// edit the reference data and live settings stored in the database.
(function () {
  "use strict";
  const { $, $$, esc, pct, kickoff, rel, dateTime, parseMatch, orderOutcomes, isDraw, outcomeLabel, raceTitle,
    initNav, errText, msg, loginHref, loadMeta } = App;
  let races = [];
  let meta = null;
  let articleId = null;   // set when the create form was opened from an article

  initNav("admin").then(async (u) => {
    if (!u || !u.is_admin) {
      $("#admin-root").innerHTML = `<div class="empty card">${u ? "This page is for admins only." : `<a href="${loginHref()}">Log in</a> with an admin account to manage markets.`}</div>`;
      return;
    }
    try {
      meta = await loadMeta(true);
    } catch (e) {
      note("Couldn't load reference data: " + esc(errText(e)), "err");
      return;
    }
    setupCreate();
    showTab((location.hash || "#markets").slice(1));
    loadMarkets();
    loadArticles(true);
  });

  function note(html, kind) {
    $("#admin-msg").innerHTML = html ? msg(html, kind) : "";
    if (html) $("#admin-msg").scrollIntoView({ block: "nearest" });
  }
  const KIND_LABEL = { match: "Cuppers match", binary: "Yes / No", multi: "Multiple choice" };
  const STATUS_LABEL = { proposed: "Draft", open: "Open", closed: "Closed", resolved: "Resolved", rejected: "Rejected" };
  const statusBadge = (s) => `<span class="badge badge-${s === "rejected" ? "lost" : s}">${STATUS_LABEL[s] || s}</span>`;
  const isoZ = (d) => d.toISOString().replace(/\.\d{3}Z$/, "Z");
  function toLocalInput(d) {
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
  }
  const catName = (slug) => ((meta && meta.categories.find((c) => c.slug === slug)) || { name: slug }).name;

  // ------------------------------------------------------------ tabs
  function showTab(name) {
    if (!$(`.admin-pane[data-pane="${name}"]`)) name = "markets";
    $$(".admin-tabs [data-tab]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
    $$(".admin-pane").forEach((p) => { p.hidden = p.dataset.pane !== name; });
    history.replaceState(null, "", "#" + name);
    if (name === "articles") loadArticles();
    if (name === "ref") loadRef();
    if (name === "settings") loadSettings();
    if (name === "activity") loadActivity();
  }
  $(".admin-tabs").addEventListener("click", (e) => {
    const b = e.target.closest("[data-tab]");
    if (b) { note(""); showTab(b.dataset.tab); }
  });
  $("#reload").addEventListener("click", async () => {
    note("");
    meta = await loadMeta(true).catch(() => meta);
    loadMarkets();
    showTab((location.hash || "#markets").slice(1));
  });

  // ------------------------------------------------------------ markets tab
  async function loadMarkets() {
    try {
      races = (await api.get("/api/admin/races?status=any")).races || [];
    } catch (e) {
      note("Couldn't load markets: " + esc(errText(e)), "err");
      return;
    }
    const now = Date.now();
    const byClose = (a, b) => Date.parse(a.closes_at) - Date.parse(b.closes_at);
    const drafts = races.filter((r) => r.status === "proposed").sort(byClose);
    const awaiting = races.filter((r) => r.status === "closed" || (r.status === "open" && Date.parse(r.closes_at) <= now)).sort(byClose);
    $("#count-drafts").textContent = drafts.length || "";
    $("#count-results").textContent = awaiting.length || "";
    $("#count-attn").textContent = drafts.length + awaiting.length || "";
    $("#drafts").innerHTML = drafts.length ? `<div class="race-grid">${drafts.map(draftCard).join("")}</div>`
      : `<div class="empty card">No drafts. Markets saved as drafts (or proposed by Gemini from the news) appear here.</div>`;
    $("#results").innerHTML = awaiting.length ? `<div class="race-grid">${awaiting.map(resultCard).join("")}</div>`
      : `<div class="empty card">Nothing waiting for a result.</div>`;
    const cats = Array.from(new Set(races.map((r) => r.category))).sort();
    const sel = $("#f-category"), keep = sel.value;
    sel.innerHTML = `<option value="">Any category</option>` + cats.map((c) => `<option value="${esc(c)}">${esc(catName(c))}</option>`).join("");
    sel.value = cats.includes(keep) ? keep : "";
    renderAll();
  }

  function oddsSummary(r) {
    if (r.kind === "binary") return `YES ${pct((r.candidates[0] || {}).price)}`;
    return orderOutcomes(r).map((c) => `${esc(outcomeLabel(c.candidate, r, true))} ${pct(c.price)}`).join(" · ");
  }
  function sourceLink(r) {
    return r.source_url ? `<a class="tiny" href="${esc(r.source_url)}" target="_blank" rel="noopener">${esc(r.source_name || "source")} ↗</a>` : "";
  }

  function draftCard(r) {
    return `<div class="card admin-card" data-race="${r.id}">
      <div class="match-top"><span class="comp">${esc(KIND_LABEL[r.kind])} · ${esc(catName(r.category))}</span><span class="when">closes ${esc(kickoff(r.closes_at))}</span></div>
      <h3 style="font-size:17px;margin:6px 0 4px">${esc(raceTitle(r))}</h3>
      <p class="small muted" style="margin:0 0 6px">${oddsSummary(r)}</p>
      ${r.created_by_ai ? `<p class="tiny"><span class="ai-label">Gemini</span> ${esc(r.ai_reason || "")}</p>` : ""}
      <p class="tiny faint" style="margin:0 0 10px">${esc(r.resolution_criteria)} ${sourceLink(r)}</p>
      <div class="row" style="gap:6px">
        <button class="btn btn-primary btn-xs" type="button" data-act="approve">Approve &amp; open</button>
        <button class="btn btn-ghost btn-xs" type="button" data-act="edit">Edit</button>
        <button class="btn btn-ghost btn-xs" type="button" data-act="reject">Reject</button>
      </div></div>`;
  }

  function resultCard(r) {
    const outs = r.kind === "binary"
      ? [{ id: r.candidates[0].id, label: "Yes", price: r.candidates[0].price }, { id: "none", label: "No", price: 100 - r.candidates[0].price }]
      : orderOutcomes(r).map((c) => ({ id: c.id, label: outcomeLabel(c.candidate, r), price: c.price }));
    const multiSeat = r.kind !== "binary" && r.seats > 1;
    const type = multiSeat ? "checkbox" : "radio";
    return `<form class="card admin-card" data-result="${r.id}">
      <div class="match-top"><span class="comp">${esc(r.kind === "match" ? parseMatch(r.position).competition : KIND_LABEL[r.kind])}</span><span class="when">${esc(kickoff(r.closes_at))}</span></div>
      <h3 style="font-size:17px;margin:6px 0 4px">${esc(raceTitle(r))}</h3>
      <fieldset style="border:0;padding:0;margin:0"><legend class="small muted">What happened?${multiSeat ? ` Pick ${r.seats}.` : ""}</legend>
        <ul class="check-list">${outs.map((o) => `<li><label><input type="${type}" name="w-${r.id}" value="${o.id}">
          ${esc(o.label)}</label><span class="tiny faint">market had ${pct(o.price)}</span></li>`).join("")}</ul>
      </fieldset>
      <button class="btn btn-primary btn-sm" type="submit">Post result</button>
      <div class="r-msg" aria-live="polite"></div>
    </form>`;
  }

  function renderAll() {
    const q = $("#f-search").value.trim().toLowerCase(), st = $("#f-status").value, kd = $("#f-kind").value, ct = $("#f-category").value;
    const list = races.filter((r) => (!st || r.status === st) && (!kd || r.kind === kd) && (!ct || r.category === ct)
      && (!q || `${r.position} ${r.election} ${r.description || ""}`.toLowerCase().includes(q)))
      .sort((a, b) => (a.status === "open" ? 0 : 1) - (b.status === "open" ? 0 : 1) || Date.parse(a.closes_at) - Date.parse(b.closes_at));
    $("#count-all").textContent = list.length || "";
    if (!list.length) { $("#all-markets").innerHTML = `<div class="empty card">No markets match.</div>`; return; }
    $("#all-markets").innerHTML = `<div class="card"><div class="table-wrap"><table class="table"><thead><tr>
      <th>Market</th><th>Status</th><th>Closes</th><th>Odds</th><th></th></tr></thead><tbody>${list.map((r) => {
      const link = ["open", "closed", "resolved"].includes(r.status) ? `race.html?id=${r.id}` : null;
      const title = link ? `<a href="${link}"><b>${esc(raceTitle(r))}</b></a>` : `<b>${esc(raceTitle(r))}</b>`;
      const sub = [KIND_LABEL[r.kind], catName(r.category), r.kind === "match" ? parseMatch(r.position).competition : "", r.election].filter(Boolean);
      const acts = [];
      if (!["resolved", "rejected"].includes(r.status)) acts.push(`<button class="btn btn-ghost btn-xs" type="button" data-act="edit">Edit</button>`);
      if (r.status === "proposed") acts.push(`<button class="btn btn-ghost btn-xs" type="button" data-act="approve">Approve</button>`);
      if (r.status === "open") acts.push(`<button class="btn btn-ghost btn-xs" type="button" data-act="close">Stop trading</button>`);
      if (r.status === "resolved") {
        const w = r.kind === "binary" ? (r.winners.length ? "Yes" : "No")
          : r.candidates.filter((c) => r.winners.includes(c.id)).map((c) => outcomeLabel(c.candidate, r)).join(", ");
        acts.push(`<span class="badge badge-won">✓ ${esc(w || "Settled")}</span>`);
      }
      return `<tr data-race="${r.id}"><td>${title}<div class="tiny faint">${sub.map(esc).join(" · ")} ${sourceLink(r)}</div></td>
        <td>${statusBadge(r.status)}</td>
        <td class="small" style="white-space:nowrap">${esc(kickoff(r.closes_at))}<div class="tiny faint">${esc(rel(r.closes_at))}</div></td>
        <td class="small muted">${oddsSummary(r)}</td><td class="right" style="white-space:nowrap">${acts.join(" ")}</td></tr>`;
    }).join("")}</tbody></table></div></div>`;
  }
  ["#f-search", "#f-status", "#f-kind", "#f-category"].forEach((s) => $(s).addEventListener("input", renderAll));

  // approve / reject / close / edit buttons (drafts grid + table)
  $("[data-pane=markets]").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-act]");
    if (!b) return;
    const r = races.find((x) => x.id === Number(b.closest("[data-race]").dataset.race));
    if (!r) return;
    const title = raceTitle(r);
    if (b.dataset.act === "edit") return openEdit(r);
    const ask = { approve: `Approve "${title}" and open trading now?`, reject: `Reject the draft "${title}"?`,
      close: `Stop trading on "${title}" now? Open orders will be cancelled.` }[b.dataset.act];
    if (!confirm(ask)) return;
    b.disabled = true;
    try {
      await api.post(`/api/admin/races/${r.id}/${b.dataset.act}`);
      note({ approve: `Approved: ${esc(title)}. Trading is open.`, reject: `Rejected: ${esc(title)}.`,
        close: `Trading stopped on ${esc(title)}.` }[b.dataset.act], "ok");
      loadMarkets();
    } catch (err) {
      note(esc(errText(err)), "err");
      b.disabled = false;
    }
  });

  // post result (stop trading first if still open)
  $("#results").addEventListener("submit", async (e) => {
    const form = e.target.closest("form[data-result]");
    if (!form) return;
    e.preventDefault();
    const r = races.find((x) => x.id === Number(form.dataset.result));
    const picked = $$("input:checked", form).map((i) => i.value);
    const out = form.querySelector(".r-msg");
    const need = r.kind === "binary" ? 1 : r.seats;
    if (picked.length !== need) { out.innerHTML = msg(need === 1 ? "Pick the result first." : `Pick exactly ${need} winners.`, "err"); return; }
    const winners = picked.filter((v) => v !== "none").map(Number);
    const label = r.kind === "binary" ? (winners.length ? "Yes" : "No")
      : r.candidates.filter((c) => winners.includes(c.id)).map((c) => outcomeLabel(c.candidate, r)).join(", ");
    if (!confirm(`Post the result of "${raceTitle(r)}" as "${label}"?\n\nThis settles every position and pays out. It can't be undone.`)) return;
    const btn = form.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      if (r.status === "open") await api.post(`/api/admin/races/${r.id}/close`);
      await api.post(`/api/admin/races/${r.id}/resolve`, { winner_market_ids: winners });
      note(`Result posted: ${esc(raceTitle(r))}, ${esc(label)}.`, "ok");
      await loadMarkets();
      App.refreshUser();
    } catch (err) {
      out.innerHTML = msg(esc(errText(err)), "err");
      btn.disabled = false;
    }
  });

  // ------------------------------------------------------------ edit dialog
  let editing = null;
  function openEdit(r) {
    editing = r;
    $("#edit-title").textContent = "Edit: " + raceTitle(r);
    $("#e-close").value = toLocalInput(new Date(r.closes_at));
    $("#e-category").innerHTML = meta.categories.map((c) => `<option value="${esc(c.slug)}">${esc(c.name)}</option>`).join("");
    $("#e-category").value = r.category;
    $("#e-desc").value = r.description || "";
    $("#e-criteria").value = r.resolution_criteria || "";
    $("#e-source-name").value = r.source_name || "";
    $("#e-source-url").value = r.source_url || "";
    $("#e-msg").innerHTML = "";
    $("#edit-dialog").showModal();
  }
  $("#e-cancel").addEventListener("click", () => $("#edit-dialog").close());
  $("#edit-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const r = editing;
    const close = new Date($("#e-close").value);
    if (isNaN(close)) { $("#e-msg").innerHTML = msg("Enter a valid close time.", "err"); return; }
    const body = {};
    if (Math.abs(close - new Date(r.closes_at)) >= 60e3) body.closes_at = isoZ(close);
    const fields = [["category", "#e-category"], ["description", "#e-desc"], ["resolution_criteria", "#e-criteria"],
      ["source_name", "#e-source-name"], ["source_url", "#e-source-url"]];
    for (const [k, sel] of fields) if ($(sel).value.trim() !== (r[k] || "")) body[k] = $(sel).value.trim();
    if (!Object.keys(body).length) { $("#edit-dialog").close(); return; }
    $("#e-save").disabled = true;
    try {
      await api.patch(`/api/admin/races/${r.id}`, body);
      $("#edit-dialog").close();
      note(`Saved changes to ${esc(raceTitle(r))}.`, "ok");
      loadMarkets();
    } catch (err) {
      $("#e-msg").innerHTML = msg(esc(errText(err)), "err");
    } finally {
      $("#e-save").disabled = false;
    }
  });

  // ------------------------------------------------------------ create tab
  const kind = () => ($("input[name=kind]:checked") || {}).value || "match";
  function setupCreate() {
    $("#c-term").innerHTML = meta.terms.map((t) => `<option>${esc(t)}</option>`).join("");
    $("#c-term").value = meta.current_term || meta.terms[0];
    $("#c-category").innerHTML = meta.categories.map((c) => `<option value="${esc(c.slug)}">${esc(c.name)}</option>`).join("");
    $("#c-comp").innerHTML = meta.competitions.map((c) => `<option>${esc(c.name)}</option>`).join("");
    $("#team-list").innerHTML = meta.teams.map((t) => `<option value="${esc(t)}"></option>`).join("");
    const kick = new Date(Date.now() + 2 * 86400e3);
    kick.setHours(14, 0, 0, 0);
    $("#c-close").value = toLocalInput(kick);
    $$("input[name=kind]").forEach((i) => i.addEventListener("change", () => applyKind(true)));
    $("#c-comp").addEventListener("change", () => { syncDraw(); renderPriors(); });
    ["#c-home", "#c-away", "#c-draw"].forEach((s) => $(s).addEventListener("input", renderPriors));
    $("#c-draw").addEventListener("change", renderPriors);
    $("#c-priors").addEventListener("input", renderSum);
    $("#c-outcomes").addEventListener("input", renderSum);
    $("#c-outcomes").addEventListener("click", (e) => {
      const b = e.target.closest("[data-remove]");
      if (b && $$("#c-outcomes .outcome-row").length > 2) { b.closest(".outcome-row").remove(); renderSum(); }
    });
    $("#c-add-outcome").addEventListener("click", () => { addOutcomeRow("", 10); renderSum(); });
    $("#c-criteria").addEventListener("input", (e) => { e.target.dataset.edited = "1"; });
    $("#create-form").addEventListener("input", renderPreview);
    addOutcomeRow("", 50);
    addOutcomeRow("", 50);
    syncDraw();
    applyKind(false);
  }
  function syncDraw() {
    const comp = meta.competitions.find((c) => c.name === $("#c-comp").value);
    $("#c-draw").checked = !!(comp && comp.has_draw);
  }
  function applyKind(userChange) {
    const k = kind();
    $$("#create-form [data-kind]").forEach((el) => { el.hidden = !el.dataset.kind.split(" ").includes(k); });
    if (userChange) $("#c-category").value = k === "match" ? "cuppers" : (articleId ? "news" : $("#c-category").value === "cuppers" ? "news" : $("#c-category").value);
    else $("#c-category").value = k === "match" ? "cuppers" : "news";
    if (!$("#c-criteria").dataset.edited) $("#c-criteria").value = k === "match" ? meta.defaults.criteria_match : meta.defaults.criteria_generic;
    renderPriors();
  }
  function addOutcomeRow(name, prior) {
    const row = document.createElement("div");
    row.className = "outcome-row";
    row.innerHTML = `<input class="input input-sm" placeholder="Outcome name" maxlength="80" value="${esc(name)}">
      <input class="input input-sm" type="number" min="1" max="99" step="1" value="${prior}" inputmode="numeric" aria-label="Opening odds %">
      <button class="icon-btn" type="button" data-remove aria-label="Remove outcome">×</button>`;
    $("#c-outcomes").appendChild(row);
  }
  function matchNames() {
    const home = $("#c-home").value.trim() || "Home", away = $("#c-away").value.trim() || "Away";
    return $("#c-draw").checked ? [home, "Draw", away] : [home, away];
  }
  function renderPriors() {
    if (kind() === "match") {
      const names = matchNames();
      const old = $$("#c-priors input").map((i) => Number(i.value));
      const defaults = names.length === 3 ? meta.defaults.priors_draw : meta.defaults.priors_no_draw;
      const keep = old.length === names.length;
      $("#c-priors").innerHTML = names.map((n, i) => `<div><label for="c-p${i}" style="margin-top:0">${esc(isDraw(n) ? "Draw" : n)}</label>
        <input class="input" id="c-p${i}" type="number" min="1" max="99" step="1" value="${keep ? old[i] : defaults[i]}" inputmode="numeric"></div>`).join("");
    }
    renderSum();
  }
  function outcomes() {
    const k = kind();
    if (k === "match") return matchNames().map((n, i) => ({ name: n, prior: Number(($$("#c-priors input")[i] || {}).value) }));
    if (k === "binary") return [{ name: "Yes", prior: Number($("#c-prior").value) }];
    return $$("#c-outcomes .outcome-row").map((row) => {
      const [n, p] = $$("input", row);
      return { name: n.value.trim(), prior: Number(p.value) };
    });
  }
  function renderSum() {
    const k = kind();
    if (k === "binary") { $("#c-prior-sum").textContent = ""; renderPreview(); return; }
    const sum = outcomes().reduce((a, o) => a + (o.prior || 0), 0);
    const target = k === "multi" ? 100 * (Number($("#c-seats").value) || 1) : 100;
    $("#c-prior-sum").innerHTML = `Total ${sum}%${Math.abs(sum - target) <= 2 ? "" : ` <span class="warn">(should add up to about ${target}%)</span>`}. The market maker opens quotes around these.`;
    renderPreview();
  }
  function position() {
    const k = kind();
    if (k === "match") return `${$("#c-comp").value}: ${$("#c-home").value.trim()} vs ${$("#c-away").value.trim()}`;
    return $("#c-question").value.trim().replace(/\s+/g, " ");
  }
  function renderPreview() {
    const k = kind(), pos = position();
    const outs = outcomes();
    const close = new Date($("#c-close").value);
    const r = { kind: k, position: pos };
    $("#c-preview").innerHTML = `<div class="tiny faint">${esc(KIND_LABEL[k])} · ${esc(catName($("#c-category").value))} · ${esc($("#c-term").value)}</div>
      <div style="font-weight:650;margin:4px 0 8px;color:var(--text)">${esc(raceTitle(r) || "…")}</div>
      <ul class="tiny" style="margin:0;padding-left:18px">${outs.map((o) => `<li>${esc(k === "binary" ? "YES" : (o.name ? outcomeLabel(o.name, r) : "…"))}: ${o.prior || "–"}%</li>`).join("")}</ul>
      <p class="tiny" style="margin:8px 0 0">${isNaN(close) ? "" : `Trading stops ${esc(dateTime(close.toISOString()))} (${esc(rel(close.toISOString()))})`}</p>`;
  }

  $("#create-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const status = (e.submitter && e.submitter.dataset.status) || "open";
    const out = $("#c-msg");
    const k = kind(), outs = outcomes(), pos = position();
    const close = new Date($("#c-close").value);
    let err = "";
    if (k === "match") {
      const home = $("#c-home").value.trim(), away = $("#c-away").value.trim();
      err = !home || !away ? "Enter both teams." : home.toLowerCase() === away.toLowerCase() ? "The two teams must be different."
        : isDraw(home) || isDraw(away) ? "\"Draw\" can't be a team name." : "";
    } else if (!pos) err = "Enter the question.";
    else if (k === "multi" && outs.some((o) => !o.name)) err = "Every outcome needs a name.";
    if (!err && isNaN(close)) err = "Enter the close time.";
    if (!err && close.getTime() <= Date.now()) err = "The close time must be in the future.";
    if (!err && outs.some((o) => !Number.isInteger(o.prior) || o.prior < 1 || o.prior > 99)) err = "Opening odds must be whole numbers from 1 to 99.";
    if (err) { out.innerHTML = msg(esc(err), "err"); return; }
    const round = $("#c-round").value.trim();
    const desc = $("#c-desc").value.trim() || (k === "match" ? (round ? `${round} · ${$("#c-comp").value}` : $("#c-comp").value) : "");
    const body = {
      kind: k, status, election: $("#c-term").value, category: $("#c-category").value, position: pos,
      seats: k === "multi" ? Number($("#c-seats").value) || 1 : 1, closes_at: isoZ(close),
      description: desc, resolution_criteria: $("#c-criteria").value.trim(),
      source_name: $("#c-source-name").value.trim(), source_url: $("#c-source-url").value.trim(),
      candidates: outs, article_id: articleId,
    };
    $$("#create-form button[type=submit]").forEach((b) => { b.disabled = true; });
    try {
      const d = await api.post("/api/admin/races", body);
      const link = status === "open" ? `<a href="race.html?id=${d.race.id}">${esc(raceTitle(d.race))}</a>` : esc(raceTitle(d.race));
      out.innerHTML = msg(status === "open" ? `Published ${link}. Trading is open.` : `Saved ${link} as a draft (Markets → Drafts).`, "ok");
      resetCreate();
      loadMarkets();
      if (body.article_id) loadArticles(true);
    } catch (er) {
      out.innerHTML = msg(esc(errText(er)), "err");
    } finally {
      $$("#create-form button[type=submit]").forEach((b) => { b.disabled = false; });
    }
  });
  function resetCreate() {
    ["#c-home", "#c-away", "#c-question", "#c-desc", "#c-source-name", "#c-source-url", "#c-round"].forEach((s) => { $(s).value = ""; });
    $("#c-outcomes").innerHTML = "";
    addOutcomeRow("", 50);
    addOutcomeRow("", 50);
    articleId = null;
    $("#from-article").hidden = true;
    delete $("#c-criteria").dataset.edited;
    applyKind(false);
  }

  // ------------------------------------------------------------ articles tab
  let articles = [];
  async function loadArticles(countOnly) {
    try {
      const pending = countOnly || $("#a-pending").checked;
      articles = (await api.get(`/api/admin/articles?limit=80${pending ? "&pending=1" : ""}`)).articles || [];
    } catch (e) {
      if (!countOnly) $("#articles").innerHTML = msg("Couldn't load articles: " + esc(errText(e)), "err");
      return;
    }
    if (countOnly) { $("#count-articles").textContent = articles.length || ""; return; }
    if ($("#a-pending").checked) $("#count-articles").textContent = articles.length || "";
    $("#articles").innerHTML = articles.length ? `<div class="card"><div class="table-wrap"><table class="table"><tbody>${articles.map((a) => `
      <tr data-article="${a.id}"><td><a href="${esc(a.url)}" target="_blank" rel="noopener"><b>${esc(a.title)}</b> ↗</a>
        <div class="tiny faint">${esc(a.source)} · ${esc(a.published_at ? rel(a.published_at) : "")}</div>
        ${a.summary ? `<div class="small muted" style="margin-top:4px">${esc(a.summary)}</div>` : ""}</td>
        <td class="right" style="white-space:nowrap;vertical-align:top">
          ${a.race_ids.length ? `<span class="badge badge-open">${a.race_ids.length} market${a.race_ids.length > 1 ? "s" : ""}</span> ` : ""}
          <button class="btn btn-primary btn-xs" type="button" data-act="make">Create market</button>
          ${a.processed ? "" : `<button class="btn btn-ghost btn-xs" type="button" data-act="dismiss">Dismiss</button>`}</td></tr>`).join("")}
      </tbody></table></div></div>` : `<div class="empty card">No ${$("#a-pending").checked ? "unhandled " : ""}articles. Try “Scrape now”.</div>`;
  }
  $("#a-pending").addEventListener("change", () => loadArticles());
  $("#a-scrape").addEventListener("click", async (e) => {
    e.target.disabled = true;
    note("Scraping Cherwell and The Oxford Student…", "info");
    try {
      const d = await api.post("/api/admin/scrape");
      note(`Scrape finished: ${d.new_articles} new article${d.new_articles === 1 ? "" : "s"}.`, "ok");
      loadArticles();
      loadMarkets();
    } catch (err) {
      note(esc(errText(err)), "err");
    } finally {
      e.target.disabled = false;
    }
  });
  $("#articles").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-act]");
    if (!b) return;
    const a = articles.find((x) => x.id === Number(b.closest("[data-article]").dataset.article));
    if (b.dataset.act === "dismiss") {
      b.disabled = true;
      try { await api.post(`/api/admin/articles/${a.id}/dismiss`); loadArticles(); } catch (err) { note(esc(errText(err)), "err"); b.disabled = false; }
      return;
    }
    // prefill the create form as a yes/no news market linked to this article
    resetCreate();
    articleId = a.id;
    $("input[name=kind][value=binary]").checked = true;
    applyKind(true);
    $("#c-category").value = "news";
    $("#c-question").value = "";
    $("#c-question").placeholder = `A question about: ${a.title}`;
    $("#c-source-name").value = a.source;
    $("#c-source-url").value = a.url;
    const close = new Date(Date.now() + 30 * 86400e3);
    close.setHours(12, 0, 0, 0);
    $("#c-close").value = toLocalInput(close);
    $("#from-article").hidden = false;
    $("#from-article").innerHTML = `From the article <a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.title)}</a> (${esc(a.source)}). Write a question it raises that will be answered publicly by the close time.`;
    showTab("create");
    renderPreview();
    $("#c-question").focus();
  });

  // ------------------------------------------------------------ reference data tab
  const REF = {
    competitions: { help: "Cuppers competitions offered in the match form. Deactivate instead of deleting so old markets keep their name.",
      cols: [["name", "Name", "text"], ["sport", "Sport", "sport"], ["category", "Category", "category"], ["has_draw", "Draw possible", "bool"], ["active", "Active", "bool"]] },
    teams: { help: "Colleges and teams suggested for home / away.", cols: [["name", "Name", "text"], ["kind", "Kind", "teamkind"], ["active", "Active", "bool"]] },
    terms: { help: "Terms a market can belong to. The current term is the one whose dates contain today.",
      cols: [["name", "Name", "text"], ["starts_on", "Starts", "date"], ["ends_on", "Ends", "date"]] },
    categories: { help: "Groups markets on the home page.", cols: [["slug", "Slug", "text"], ["name", "Name", "text"], ["sort", "Order", "int"]] },
    sports: { help: "Icons for Cuppers competitions; the pattern (a regular expression) is matched against the competition name.",
      cols: [["name", "Name", "text"], ["icon", "Icon", "text"], ["pattern", "Pattern", "text"], ["sort", "Order", "int"]] },
  };
  let refTable = "competitions", refRows = [];
  $(".ref-seg").addEventListener("click", (e) => {
    const b = e.target.closest("[data-ref]");
    if (!b) return;
    refTable = b.dataset.ref;
    $$(".ref-seg [data-ref]").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
    loadRef();
  });
  function cell(type, name, v) {
    if (type === "bool") return `<input type="checkbox" data-f="${name}" ${v ? "checked" : ""} aria-label="${name}">`;
    if (type === "sport") return `<select class="input input-sm" data-f="${name}"><option value="">–</option>${meta.sports.map((s) => `<option ${s.name === v ? "selected" : ""}>${esc(s.name)}</option>`).join("")}</select>`;
    if (type === "category") return `<select class="input input-sm" data-f="${name}">${meta.categories.map((c) => `<option value="${esc(c.slug)}" ${c.slug === v ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select>`;
    if (type === "teamkind") return `<select class="input input-sm" data-f="${name}">${["college", "other"].map((k) => `<option ${k === v ? "selected" : ""}>${k}</option>`).join("")}</select>`;
    const t = type === "date" ? "date" : type === "int" ? "number" : "text";
    return `<input class="input input-sm" type="${t}" data-f="${name}" value="${esc(v == null ? "" : v)}">`;
  }
  async function loadRef() {
    const spec = REF[refTable];
    $("#ref-help").textContent = spec.help;
    try {
      refRows = (await api.get(`/api/admin/ref/${refTable}`)).rows || [];
    } catch (e) {
      $("#ref-table").innerHTML = msg(esc(errText(e)), "err");
      return;
    }
    const key = refTable === "categories" ? "slug" : refTable === "sports" ? "name" : "id";
    const blank = Object.fromEntries(spec.cols.map(([n, , t]) => [n, t === "bool" ? (n === "active") : t === "teamkind" ? "college" : t === "category" ? "cuppers" : ""]));
    $("#ref-table").innerHTML = `<div class="card"><div class="table-wrap"><table class="table ref-grid"><thead><tr>${spec.cols.map(([, l]) => `<th>${l}</th>`).join("")}<th></th></tr></thead><tbody>
      ${refRows.map((r) => `<tr data-key="${esc(r[key])}">${spec.cols.map(([n, , t]) => `<td>${cell(t, n, r[n])}</td>`).join("")}
        <td class="right"><button class="btn btn-ghost btn-xs" type="button" data-save>Save</button></td></tr>`).join("")}
      <tr data-new>${spec.cols.map(([n, , t]) => `<td>${cell(t, n, blank[n])}</td>`).join("")}<td class="right"><button class="btn btn-primary btn-xs" type="button" data-save>Add</button></td></tr>
      </tbody></table></div></div>`;
  }
  function rowValues(tr) {
    const out = {};
    for (const [n, , t] of REF[refTable].cols) {
      const el = tr.querySelector(`[data-f="${n}"]`);
      out[n] = t === "bool" ? el.checked : t === "int" ? (el.value === "" ? 100 : Number(el.value)) : el.value.trim();
    }
    return out;
  }
  $("#ref-table").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-save]");
    if (!b) return;
    const tr = b.closest("tr");
    const vals = rowValues(tr);
    b.disabled = true;
    try {
      if (tr.hasAttribute("data-new")) {
        await api.post(`/api/admin/ref/${refTable}`, vals);
        note(`Added to ${refTable}.`, "ok");
      } else {
        const key = tr.dataset.key;
        const old = refRows.find((r) => String(r[refTable === "categories" ? "slug" : refTable === "sports" ? "name" : "id"]) === key);
        const changes = Object.fromEntries(Object.entries(vals).filter(([k, v]) => String(v) !== String(old[k] == null ? "" : old[k])));
        if (!Object.keys(changes).length) { b.disabled = false; return; }
        await api.patch(`/api/admin/ref/${refTable}/${encodeURIComponent(key)}`, changes);
        note(`Saved ${refTable} change.`, "ok");
      }
      meta = await loadMeta(true);
      setupCreateLists();
      loadRef();
    } catch (err) {
      note(esc(errText(err)), "err");
      b.disabled = false;
    }
  });
  function setupCreateLists() {
    const keepComp = $("#c-comp").value, keepTerm = $("#c-term").value, keepCat = $("#c-category").value;
    $("#c-term").innerHTML = meta.terms.map((t) => `<option>${esc(t)}</option>`).join("");
    $("#c-term").value = meta.terms.includes(keepTerm) ? keepTerm : meta.current_term;
    $("#c-category").innerHTML = meta.categories.map((c) => `<option value="${esc(c.slug)}">${esc(c.name)}</option>`).join("");
    $("#c-category").value = keepCat;
    $("#c-comp").innerHTML = meta.competitions.map((c) => `<option>${esc(c.name)}</option>`).join("");
    if (meta.competitions.some((c) => c.name === keepComp)) $("#c-comp").value = keepComp;
    $("#team-list").innerHTML = meta.teams.map((t) => `<option value="${esc(t)}"></option>`).join("");
  }

  // ------------------------------------------------------------ settings tab
  let settings = [];
  function settingInput(s) {
    const v = s.value;
    if (Array.isArray(v)) return `<input class="input input-sm" data-key="${esc(s.key)}" data-type="list" value="${esc(v.join(", "))}">`;
    if (typeof v === "number") return `<input class="input input-sm" data-key="${esc(s.key)}" data-type="num" type="number" step="any" value="${v}" style="max-width:140px">`;
    return `<textarea class="input input-sm" data-key="${esc(s.key)}" data-type="text" rows="2">${esc(v)}</textarea>`;
  }
  async function loadSettings() {
    try {
      settings = (await api.get("/api/admin/settings")).settings || [];
    } catch (e) {
      $("#settings-body").innerHTML = `<tr><td colspan="3">${msg(esc(errText(e)), "err")}</td></tr>`;
      return;
    }
    $("#settings-body").innerHTML = settings.map((s) => `<tr><td><code>${esc(s.key)}</code><div class="tiny faint">${esc(s.description || "")}</div></td>
      <td style="min-width:260px">${settingInput(s)}</td><td class="tiny faint" style="white-space:nowrap">${s.updated_at ? esc(rel(s.updated_at)) : ""}</td></tr>`).join("");
    $("#settings-msg").innerHTML = "";
  }
  $("#settings-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const changes = {};
    for (const el of $$("[data-key]", e.target)) {
      const s = settings.find((x) => x.key === el.dataset.key);
      let v = el.value.trim();
      if (el.dataset.type === "num") v = Number(v);
      if (el.dataset.type === "list") v = v.split(",").map((x) => Number(x.trim())).filter((x) => !isNaN(x));
      if (JSON.stringify(v) !== JSON.stringify(s.value)) changes[s.key] = v;
    }
    if (!Object.keys(changes).length) { $("#settings-msg").innerHTML = `<span class="small muted">No changes.</span>`; return; }
    try {
      await api.put("/api/admin/settings", changes);
      await loadSettings();
      $("#settings-msg").innerHTML = `<span class="small" style="color:var(--yes-ink)">Saved ${Object.keys(changes).length} setting${Object.keys(changes).length > 1 ? "s" : ""}.</span>`;
      meta = await loadMeta(true);
    } catch (err) {
      $("#settings-msg").innerHTML = `<span class="small" style="color:var(--no-ink)">${esc(errText(err))}</span>`;
    }
  });

  // ------------------------------------------------------------ activity tab
  const ACTIONS = [
    [/^POST \/api\/admin\/races$/, "Created market"], [/^PATCH \/api\/admin\/races\//, "Edited market"],
    [/\/approve$/, "Approved draft"], [/\/reject$/, "Rejected draft"], [/races\/.*\/close$/, "Stopped trading"],
    [/races\/.*\/resolve$/, "Posted result"], [/markets\/.*\/close$/, "Closed outcome market"], [/markets\/.*\/resolve$/, "Resolved outcome market"],
    [/\/candidates$/, "Added outcome"], [/^PUT \/api\/admin\/settings/, "Changed settings"], [/^POST \/api\/admin\/ref\//, "Added reference data"],
    [/^PATCH \/api\/admin\/ref\//, "Edited reference data"], [/\/scrape$/, "Scraped news feeds"], [/\/dismiss$/, "Dismissed article"],
    [/mm\/requote$/, "Requoted market maker"],
  ];
  const actionLabel = (a) => (ACTIONS.find(([re]) => re.test(a)) || [null, a])[1];
  function detailText(d) {
    if (!d) return "";
    if (d.position) return d.position;
    if (d.winner_market_ids) return `winners: ${d.winner_market_ids.join(", ") || "none (NO)"}`;
    const s = JSON.stringify(d);
    return s.length > 140 ? s.slice(0, 140) + "…" : s;
  }
  async function loadActivity() {
    let acts;
    try {
      acts = (await api.get("/api/admin/actions?limit=200")).actions || [];
    } catch (e) {
      $("#activity").innerHTML = msg(esc(errText(e)), "err");
      return;
    }
    $("#activity").innerHTML = acts.length ? `<div class="card"><div class="table-wrap"><table class="table"><thead><tr><th>When</th><th>Admin</th><th>Action</th><th>Market</th><th>Details</th></tr></thead><tbody>
      ${acts.map((a) => `<tr><td class="small" style="white-space:nowrap" title="${esc(a.time)}">${esc(rel(a.time))}</td><td class="small">${esc(a.admin || "–")}</td>
        <td class="small"><b>${esc(actionLabel(a.action))}</b></td>
        <td class="small">${a.race_id ? `<a href="race.html?id=${a.race_id}">${esc(a.race_position || "#" + a.race_id)}</a>` : ""}</td>
        <td class="tiny muted">${esc(detailText(a.details))}</td></tr>`).join("")}</tbody></table></div></div>`
      : `<div class="empty card">No admin actions recorded yet.</div>`;
  }
})();
