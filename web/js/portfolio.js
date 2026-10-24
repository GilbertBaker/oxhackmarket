// Portfolio: cash/equity tiles, positions grouped by match, open orders with cancel.
(function () {
  "use strict";
  const { $, esc, pence, signedPence, rel, parseMatch, outcomeLabel, positionView, poll, initNav, errText, msg, loginHref } = App;
  const START = 100000;
  const marketCache = new Map();

  initNav("portfolio").then((u) => {
    if (!u) {
      $("#pf-root").innerHTML = `<div class="empty card" style="margin-top:20px"><a href="${loginHref()}">Log in</a> to see your portfolio. New accounts start with £1,000 of play money.</div>`;
      return;
    }
    poll(load, 5000);
  });

  async function load() {
    const [pf, od] = await Promise.all([api.get("/api/portfolio"), api.get("/api/orders?status=open")]);
    App.setUser(pf.user);
    renderStats(pf);
    renderPositions(pf.positions || []);
    (pf.positions || []).forEach((p) => marketCache.set(p.market_id, { candidate: p.candidate, race_id: p.race_id, position: p.position }));
    await renderOrders(od.orders || []);
  }

  function renderStats(pf) {
    const pnl = pf.equity - START;
    const tile = (lbl, val, cls) => `<div class="card stat"><div class="lbl">${lbl}</div><div class="val num ${cls || ""}">${val}</div></div>`;
    $("#stats").innerHTML = tile("Cash", pence(pf.user.cash)) + tile("Available to trade", pence(pf.user.available))
      + tile("Equity", pence(pf.equity)) + tile("Profit / loss vs £1,000", signedPence(pnl), pnl > 0 ? "up" : pnl < 0 ? "down" : "");
  }

  function renderPositions(list) {
    list = list.filter((p) => p.qty);
    if (!list.length) {
      $("#positions").innerHTML = `<p class="muted" style="margin:8px 0 0">No positions yet. <a href="index.html">Find a match to trade.</a></p>`;
      return;
    }
    const groups = new Map();
    list.forEach((p) => {
      const k = p.race_id || "m" + p.market_id;
      if (!groups.has(k)) groups.set(k, { race_id: p.race_id, position: p.position || p.question, items: [] });
      groups.get(k).items.push(p);
    });
    $("#positions").innerHTML = `<div class="table-wrap"><table class="table"><thead><tr><th>Outcome</th><th>Side</th><th class="right">Shares</th><th class="right">Avg</th><th class="right">Now</th>
      <th class="right">Value</th><th class="right">Cost</th><th class="right">P&amp;L</th></tr></thead><tbody>${Array.from(groups.values()).map((g) => {
      const m = parseMatch(g.position);
      const head = g.race_id ? `<a href="race.html?id=${g.race_id}">${esc(m.title)}</a>` : esc(m.title);
      return `<tr class="group-row"><td colspan="8"><span class="group-head">${head}<span class="tiny faint">${esc(m.competition)}</span></span></td></tr>${g.items.map((p) => {
        const v = positionView(p);
        const status = p.status && p.status !== "open" ? ` <span class="badge badge-${p.status === "closed" ? "closed" : "resolved"}">${p.status === "closed" ? "Awaiting result" : esc(p.status)}</span>` : "";
        return `<tr><td><a href="market.html?id=${p.market_id}">${esc(outcomeLabel(p.candidate || ""))}</a>${status}</td>
          <td><span class="tag tag-${v.label.toLowerCase()}">${v.label}</span></td>
          <td class="right num">${v.shares}</td><td class="right num">${v.avg.toFixed(1).replace(/\.0$/, "")}p</td>
          <td class="right num">${v.outPrice}p</td><td class="right num">${pence(v.value)}</td><td class="right num">${pence(v.cost)}</td>
          <td class="right num ${v.pnl > 0 ? "up" : v.pnl < 0 ? "down" : ""}">${signedPence(v.pnl)}</td></tr>`;
      }).join("")}`;
    }).join("")}</tbody></table></div>`;
  }

  async function marketInfo(id) {
    if (!marketCache.has(id)) {
      try {
        const d = await api.get(`/api/markets/${id}`);
        marketCache.set(id, { candidate: d.market.candidate, race_id: d.race.id, position: d.race.position });
      } catch (e) {
        marketCache.set(id, { candidate: "Market #" + id, race_id: null, position: "" });
      }
    }
    return marketCache.get(id);
  }

  async function renderOrders(orders) {
    if (!orders.length) { $("#orders").innerHTML = `<p class="muted small" style="margin:0">No open orders.</p>`; return; }
    const infos = await Promise.all(orders.map((o) => marketInfo(o.market_id)));
    $("#orders").innerHTML = `<div class="table-wrap"><table class="table"><thead><tr><th>Market</th><th>Order</th><th class="right">Filled</th><th>Placed</th><th></th></tr></thead><tbody>${orders.map((o, i) => {
      const info = infos[i], yes = o.side === "buy";
      return `<tr><td><a href="market.html?id=${o.market_id}">${esc(outcomeLabel(info.candidate))}</a><div class="tiny faint">${esc(parseMatch(info.position).title)}</div></td>
        <td><span class="tag tag-${yes ? "yes" : "no"}">${yes ? "YES" : "NO"}</span> Buy at <b class="num">${yes ? o.price : 100 - o.price}p</b></td>
        <td class="right num">${o.qty - o.qty_remaining}/${o.qty}</td><td class="faint small">${esc(rel(o.created_at))}</td>
        <td class="right"><button class="btn btn-danger btn-xs" type="button" data-cancel="${o.id}">Cancel</button></td></tr>`;
    }).join("")}</tbody></table></div>`;
  }
  $("#pf-root").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-cancel]");
    if (!b) return;
    b.disabled = true;
    try {
      await api.del(`/api/orders/${b.dataset.cancel}`);
      await load();
    } catch (err) {
      b.disabled = false;
      $("#orders").insertAdjacentHTML("beforeend", msg(esc(errText(err)), "err"));
    }
  });
})();
