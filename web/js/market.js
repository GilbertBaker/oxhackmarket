// One outcome's market (market.html?id=N[&outcome=YES|NO]): chart, order ticket,
// depth ladder, trade tape and the user's orders/position. Everything is stored in
// YES terms; the ticket works in the chosen outcome's terms (Buy NO @ p = Sell YES @ 100-p).
(function () {
  "use strict";
  const { $, $$, esc, pence, signedPence, pct, yesNo, changeHtml, countdown, kickoff, rel, parseMatch, isDraw,
    outcomeLabel, outcomeNoLabel, sportOf, positionView, poll, initNav, qs, errText, msg, lineChart, setChartData,
    timeLabel, historySince, loginHref } = App;

  const id = Number(qs("id"));
  let market = null, race = null, book = { bids: [], asks: [] }, bucket = "5m", chart = null;
  const T = { outcome: String(qs("outcome") || "YES").toUpperCase() === "NO" ? "NO" : "YES", type: "limit", price: 50, qty: 10, touched: false };

  initNav("markets").then(start);

  function notFound(text) {
    $("#market-root").innerHTML = `<div class="empty card">${esc(text || "Market not found.")} <a href="index.html">Back to all matches</a></div>`;
  }

  function start() {
    if (!id) { notFound(); return; }
    loadMarket().then(() => {
      if (race && race.category === "isis") {
        const el = document.createElement("div");
        el.className = "banner";
        el.style.margin = "0 0 14px";
        el.innerHTML = `🚣 Daily Isis market. <a href="isis.html">See the flag model and AI summary →</a>`;
        $("#market-root").insertBefore(el, $("#market-root").children[1]);
      }
      setOutcome(T.outcome);
      poll(fast, 2000);
      poll(slow, 10000);
    }, (e) => notFound(e.status === 404 ? "Market not found." : "Couldn't load this market: " + errText(e)));
  }
  const fast = () => Promise.all([loadMarket(), loadBook(), loadTrades(), loadOrders()]);
  const slow = () => Promise.all([loadHistory(), loadPosition()]);

  // ------------------------------------------------------------ data
  async function loadMarket() {
    const d = await api.get(`/api/markets/${id}`);
    market = d.market;
    race = d.race;
    renderHead();
  }
  async function loadBook() {
    book = await api.get(`/api/markets/${id}/book?depth=10`);
    book.bids = book.bids || [];
    book.asks = book.asks || [];
    renderLadder();
    renderTicket();
  }
  async function loadTrades() {
    const d = await api.get(`/api/markets/${id}/trades?limit=40`);
    renderTape(d.trades || []);
  }
  async function loadOrders() {
    if (!App.user) {
      $("#my-orders").innerHTML = `<p class="muted small"><a href="${loginHref()}">Log in</a> to trade and see your orders.</p>`;
      return;
    }
    const d = await api.get("/api/orders?status=open");
    renderOrders((d.orders || []).filter((o) => o.market_id === id));
  }
  async function loadPosition() {
    const box = $("#my-position");
    if (!App.user) { box.hidden = true; return; }
    const d = await api.get("/api/portfolio");
    App.setUser(d.user);
    const p = (d.positions || []).find((x) => x.market_id === id);
    box.hidden = false;
    if (!p || !p.qty) {
      box.innerHTML = `<h2 style="margin-bottom:6px">Your position</h2><p class="muted small" style="margin:0">You don't hold any shares here.</p>`;
      return;
    }
    const v = positionView(p);
    box.innerHTML = `<h2 style="margin-bottom:10px">Your position</h2>
      <dl class="kv"><dt>Holding</dt><dd><span class="tag tag-${v.label.toLowerCase()}">${v.label}</span> <b class="num">${v.shares}</b> shares</dd>
      <dt>Avg price</dt><dd class="num">${v.avg.toFixed(1).replace(/\.0$/, "")}p</dd>
      <dt>Value now</dt><dd class="num">${pence(v.value)}</dd>
      <dt>P&amp;L</dt><dd class="num ${v.pnl >= 0 ? "up" : "down"}">${signedPence(v.pnl)}</dd>
      <dt>Pays</dt><dd class="num">${pence(v.shares * 100)} if ${esc(v.label === "YES" ? outcomeLabel(market.candidate, race) : outcomeNoLabel(market.candidate, race))}</dd></dl>`;
  }
  async function loadHistory() {
    const since = historySince(bucket);
    const d = await api.get(`/api/markets/${id}/history?bucket=${bucket}${since ? "&since=" + encodeURIComponent(since) : ""}`);
    const pts = d.points || [];
    if (!chart) chart = lineChart($("#m-chart"));
    setChartData(chart, pts.map((p) => timeLabel(p.t, bucket)), [{ label: outcomeLabel(market.candidate, race), color: "#0b2a66", fill: true, data: pts.map((p) => p.close) }]);
    $("#chart-empty").hidden = pts.length > 0;
  }

  // ------------------------------------------------------------ header
  function renderHead() {
    const isMatch = !race.kind || race.kind === "match";
    const m = isMatch ? parseMatch(race.position) : { competition: App.sportOf("", race).name, title: race.position };
    const label = race.kind === "binary" ? race.position : outcomeLabel(market.candidate, race);
    document.title = `${label} · ${m.title} · OxMarket`;
    $("#crumb-race").textContent = m.title;
    $("#crumb-race").href = `race.html?id=${race.id}`;
    $("#crumb-out").textContent = outcomeLabel(market.candidate, race, true);
    $("#m-icon").textContent = sportOf(m.competition || race.position, race).icon;
    $("#m-title").textContent = label;
    $("#m-sub").innerHTML = `<a href="race.html?id=${race.id}">${esc(m.title)}</a><span>· ${esc(m.competition || race.election)}</span>`;
    $("#closes-box").innerHTML = market.status === "open"
      ? `<div class="lbl">${isMatch ? "Kick-off" : "Closes"} in</div><div class="big num" data-countdown="${esc(market.closes_at)}">${esc(countdown(market.closes_at))}</div><div class="tiny faint">${esc(kickoff(market.closes_at))}</div>`
      : `<div class="lbl">${isMatch ? "Kick-off" : "Closed"}</div><div class="big" style="font-size:18px">${esc(kickoff(market.closes_at))}</div>`;
    $("#m-pct").textContent = pct(market.price);
    $("#m-chg").innerHTML = changeHtml(market.change_24h);
    $("#m-yesno").textContent = yesNo(market.price);
    $("#criteria").textContent = market.resolution_criteria || "";
    $("#t-name").innerHTML = `<span>${esc(label)}</span>`;

    const closed = market.status !== "open";
    $("#ticket").hidden = closed;
    $("#t-closed").hidden = !closed;
    if (closed) {
      $("#t-closed").innerHTML = market.status === "resolved"
        ? `<b>${market.outcome === "YES" ? "✓ This outcome happened" : "✗ This outcome didn't happen"}.</b> The market is settled: YES shares paid ${market.outcome === "YES" ? "£1.00" : "£0.00"} each.`
        : `Trading stopped${isMatch ? " at kick-off" : ""}. Waiting for the result.`;
    }
  }

  // ------------------------------------------------------------ ticket
  // Best price to buy the chosen outcome now (pence in that outcome's terms).
  function bestPrice(outcome) {
    if (outcome === "YES") return book.asks.length ? book.asks[0].price : null;
    return book.bids.length ? 100 - book.bids[0].price : null;
  }
  function setOutcome(o) {
    T.outcome = o;
    T.touched = false;
    $$(".oc-btn").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.outcome === o)));
    renderTicket();
  }
  function setPrice(p, touched) {
    T.price = Math.max(1, Math.min(99, Math.round(p) || 1));
    if (touched) T.touched = true;
    $("#t-price").value = T.price;
    $("#t-price-range").value = T.price;
    renderPreview();
  }
  function renderTicket() {
    if (!market) return;
    const yes = bestPrice("YES"), no = bestPrice("NO");
    $("#t-yes-px").textContent = (yes != null ? yes : market.price) + "p";
    $("#t-no-px").textContent = (no != null ? no : 100 - market.price) + "p";
    $("#t-explain").innerHTML = T.outcome === "YES"
      ? `You win £1 a share if the answer is <b>${esc(outcomeLabel(market.candidate, race))}</b>.`
      : `You win £1 a share if the answer is <b>${esc(outcomeNoLabel(market.candidate, race))}</b>.`;
    const sub = $("#t-submit");
    sub.className = `btn btn-block btn-lg ${App.user ? (T.outcome === "YES" ? "btn-yes-solid" : "btn-no-solid") : "btn-primary"}`;
    sub.textContent = App.user ? `Buy ${T.outcome === "YES" ? "Yes" : "No"}` : "Log in to trade";
    $("#t-price-wrap").hidden = T.type !== "limit";
    if (!T.touched) {
      const b = bestPrice(T.outcome);
      setPrice(b != null ? b : T.outcome === "YES" ? market.price : 100 - market.price);
    } else {
      renderPreview();
    }
    $("#t-avail").textContent = App.user ? `Available to trade: ${pence(App.user.available)}` : "";
  }
  function renderPreview() {
    const el = $("#t-preview");
    if (!market) return;
    const q = T.qty;
    if (!(Number.isInteger(q) && q >= 1)) { el.innerHTML = "Enter a whole number of shares."; return; }
    let cost, filled = q;
    if (T.type === "limit") {
      cost = T.price * q;
    } else {
      const levels = T.outcome === "YES" ? book.asks.map((l) => [l.price, l.qty]) : book.bids.map((l) => [100 - l.price, l.qty]);
      let rem = q;
      cost = 0;
      for (const [p, n] of levels) {
        if (rem <= 0) break;
        const take = Math.min(rem, n);
        cost += take * p;
        rem -= take;
      }
      filled = q - rem;
      if (!filled) { el.innerHTML = `<span class="warn">Nothing on offer right now. Use a limit order instead.</span>`; return; }
    }
    const cond = T.outcome === "YES" ? outcomeLabel(market.candidate, race) : outcomeNoLabel(market.candidate, race);
    const pays = filled * 100;
    let html = `Cost <b>${pence(cost)}</b> · pays <b>${pence(pays)}</b> if ${esc(cond)} · potential profit <b class="up">${pence(pays - cost)}</b>`;
    if (T.type === "market") {
      html += `<div class="tiny faint">Average ${(cost / filled).toFixed(1).replace(/\.0$/, "")}p a share.</div>`;
      if (filled < q) html += `<div class="tiny warn">Only ${filled} shares are on offer now; the rest won't fill.</div>`;
    } else {
      const best = bestPrice(T.outcome);
      html += best != null && T.price >= best
        ? `<div class="tiny faint">Fills now at ${best}p or better.</div>`
        : `<div class="tiny faint">Waits on the book until someone matches ${T.price}p.</div>`;
    }
    if (App.user && cost > App.user.available) html += `<div class="tiny warn">That's more than your available ${pence(App.user.available)}.</div>`;
    el.innerHTML = html;
  }

  $$(".oc-btn").forEach((b) => b.addEventListener("click", () => setOutcome(b.dataset.outcome)));
  $$("#t-type button").forEach((b) => b.addEventListener("click", () => {
    T.type = b.dataset.type;
    $$("#t-type button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    renderTicket();
  }));
  $("#t-price-range").addEventListener("input", (e) => setPrice(Number(e.target.value), true));
  $("#t-price").addEventListener("input", (e) => { if (e.target.value !== "") setPrice(Number(e.target.value), true); });
  $("#t-qty").addEventListener("input", (e) => { T.qty = Number(e.target.value); renderPreview(); });
  $$(".qty-quick button").forEach((b) => b.addEventListener("click", () => {
    T.qty = (Number.isInteger(T.qty) && T.qty > 0 ? T.qty : 0) + Number(b.dataset.add);
    $("#t-qty").value = T.qty;
    renderPreview();
  }));

  $("#ticket").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!App.user) { location.href = loginHref(); return; }
    const outcome = T.outcome;
    const body = { market_id: id, outcome, side: "buy", type: T.type, qty: T.qty };
    if (T.type === "limit") body.price = T.price;
    const btn = $("#t-submit");
    btn.disabled = true;
    $("#t-msg").innerHTML = "";
    try {
      const res = await api.post("/api/orders", body);
      if (res.user) App.setUser(res.user);
      $("#t-msg").innerHTML = msg(esc(fillSummary(res, outcome)), "ok");
      T.touched = false;
      await Promise.all([fast(), loadPosition()]);
    } catch (err) {
      $("#t-msg").innerHTML = msg(esc(errText(err)), "err");
    } finally {
      btn.disabled = false;
    }
  });
  function fillSummary(res, outcome) {
    const fills = res.fills || [];
    const n = fills.reduce((a, f) => a + f.qty, 0);
    const inTerms = (p) => (outcome === "YES" ? p : 100 - p); // fills are in YES terms
    const parts = [];
    if (n) {
      const avg = fills.reduce((a, f) => a + f.qty * inTerms(f.price), 0) / n;
      parts.push(`Bought ${n} ${outcome} at an average of ${avg.toFixed(1).replace(/\.0$/, "")}p.`);
    }
    if (res.order && res.order.qty_remaining > 0) parts.push(`${res.order.qty_remaining} shares are waiting on the book at ${inTerms(res.order.price)}p.`);
    return parts.join(" ") || "Nothing was on offer, so nothing filled.";
  }

  // ------------------------------------------------------------ ladder / tape / orders
  function renderLadder() {
    const asks = book.asks.slice(0, 8).reverse(), bids = book.bids.slice(0, 8);
    if (!asks.length && !bids.length) { $("#ladder").innerHTML = `<tbody><tr><td class="empty">No orders on the book yet.</td></tr></tbody>`; return; }
    const max = Math.max(1, ...asks.map((l) => l.qty), ...bids.map((l) => l.qty));
    const row = (l, kind) => `<tr class="${kind}-row" tabindex="0" data-kind="${kind}" data-price="${l.price}" style="--w:${((l.qty / max) * 100).toFixed(1)}%"
        aria-label="${kind === "ask" ? `Buy YES at ${l.price}p` : `Buy NO at ${100 - l.price}p`}, ${l.qty} shares">
        <td class="px num">${l.price}p</td><td class="no-eq num">${100 - l.price}p</td><td class="num right depth">${l.qty}</td></tr>`;
    const bestBid = bids[0], bestAsk = book.asks[0];
    const spread = bestBid && bestAsk ? `Spread ${bestAsk.price - bestBid.price}p · mid ${Math.round((bestAsk.price + bestBid.price) / 2)}%` : "One-sided book";
    $("#ladder").innerHTML = `<thead><tr><th>YES</th><th>NO</th><th class="right">Shares</th></tr></thead>
      <tbody>${asks.map((l) => row(l, "ask")).join("")}<tr class="spread-row"><td colspan="3">${spread}</td></tr>${bids.map((l) => row(l, "bid")).join("")}</tbody>`;
  }
  function pickLevel(tr) {
    if (!tr || !tr.dataset.price || market.status !== "open") return;
    const p = Number(tr.dataset.price);
    // Taking an ask = buying YES at it; hitting a bid = buying NO at 100 - bid.
    setOutcome(tr.dataset.kind === "ask" ? "YES" : "NO");
    setPrice(tr.dataset.kind === "ask" ? p : 100 - p, true);
    $("#t-qty").focus();
  }
  $("#ladder").addEventListener("click", (e) => pickLevel(e.target.closest("tr[data-price]")));
  $("#ladder").addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pickLevel(e.target.closest("tr[data-price]")); }
  });

  function renderTape(trades) {
    if (!trades.length) { $("#tape").innerHTML = `<p class="muted small">No trades yet.</p>`; return; }
    $("#tape").innerHTML = `<table class="ladder"><thead><tr><th>Time</th><th>Bought</th><th>Price</th><th class="right">Shares</th></tr></thead><tbody>${trades.map((t) => {
      const yes = t.taker_side === "buy";
      return `<tr style="cursor:default"><td class="faint num">${esc(new Date(t.time).toLocaleTimeString("en-GB"))}</td>
        <td><span class="tag tag-${yes ? "yes" : "no"}">${yes ? "YES" : "NO"}</span></td>
        <td class="num"><b>${yes ? t.price : 100 - t.price}p</b> <span class="no-eq">(YES ${t.price}p)</span></td>
        <td class="num right">${t.qty}</td></tr>`;
    }).join("")}</tbody></table>`;
  }

  function renderOrders(orders) {
    if (!orders.length) { $("#my-orders").innerHTML = `<p class="muted small" style="margin:0">No open orders on this market.</p>`; return; }
    $("#my-orders").innerHTML = `<div class="table-wrap"><table class="table"><thead><tr><th>Order</th><th class="right">Filled</th><th>Placed</th><th></th></tr></thead><tbody>${orders.map((o) => {
      const yes = o.side === "buy";
      return `<tr><td><span class="tag tag-${yes ? "yes" : "no"}">${yes ? "YES" : "NO"}</span> Buy at <b class="num">${yes ? o.price : 100 - o.price}p</b>
          <span class="tiny faint">(${yes ? "buy" : "sell"} YES ${o.price}p)</span></td>
        <td class="right num">${o.qty - o.qty_remaining}/${o.qty}</td><td class="faint small">${esc(rel(o.created_at))}</td>
        <td class="right"><button class="btn btn-danger btn-xs" type="button" data-cancel="${o.id}">Cancel</button></td></tr>`;
    }).join("")}</tbody></table></div>`;
  }
  $("#my-orders").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-cancel]");
    if (!b) return;
    b.disabled = true;
    try {
      await api.del(`/api/orders/${b.dataset.cancel}`);
      await Promise.all([loadOrders(), loadBook(), App.refreshUser()]);
      renderTicket();
    } catch (err) {
      b.disabled = false;
      $("#my-orders").insertAdjacentHTML("beforeend", msg(esc(errText(err)), "err"));
    }
  });

  $$("#bucket button").forEach((b) => b.addEventListener("click", () => {
    bucket = b.dataset.bucket;
    $$("#bucket button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    loadHistory().catch((e) => console.warn(errText(e)));
  }));
})();
