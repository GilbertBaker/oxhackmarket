// Mock backend (COLLAB 9 shapes) so every page works before the server exists.
// Used by api.js when ?mock=1 / localStorage.mock==="1" / file://.
// A fake Michaelmas 2026 Cuppers fixture list: each match is a race whose
// candidates are the outcomes ("<Home>", "Draw", "<Away>"). Admins post
// matches and results by hand, so there is no scraping and no articles.
// State lives in memory, so a reload resets it.
(function () {
  "use strict";
  const H = 3600e3, D = 24 * H;
  const NOW0 = Math.floor(Date.now() / H) * H;
  const iso = (ms) => (ms == null ? null : new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z"));
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  function rng(seed) {
    let s = (seed >>> 0) || 1;
    return () => { s = (Math.imul(s, 1664525) + 1013904223) >>> 0; return s / 4294967296; };
  }
  const TERM = "Michaelmas 2026";
  const crit = (comp) => `Resolves on the full-time result as published by Oxford University Sport or the ${comp} organisers (reported by Cherwell / The Oxford Student). The winning outcome resolves YES, every other outcome NO. Walkovers count as a win for the team awarded the match.`;

  // kick: [days from today, local hour] or hours from now. created: hours from now. priors in outcome order. shocks: [hour, outcomeIndex, logOddsDelta]
  const DEFS = [
    { id: 1, comp: "Men's Football Cuppers", round: "Group A", home: "Keble", away: "Hertford", draw: true, kick: [-9, 14], created: -12 * 24, priors: [52, 24, 24], winner: "Keble", shocks: [[-240, 0, 0.4]] },
    { id: 2, comp: "Women's Rugby Cuppers", round: "Round 1", home: "Oriel", away: "Brasenose", draw: false, kick: [-6, 14], created: -10 * 24, priors: [55, 45], winner: "Brasenose", shocks: [[-170, 1, 0.7]] },
    { id: 3, comp: "Men's Hockey Cuppers", round: "Group B", home: "St Peter's", away: "Magdalen", draw: true, kick: [-3, 13], created: -8 * 24, priors: [40, 25, 35], winner: "Draw", shocks: [[-120, 2, 0.5]] },
    { id: 4, comp: "Mixed Netball Cuppers", round: "Quarter-final", home: "Exeter", away: "St Hugh's", draw: false, kick: -2, created: -6 * 24, priors: [50, 50], shocks: [[-60, 0, 0.6], [-20, 1, 0.3]] },
    { id: 5, comp: "Men's Football Cuppers", round: "Group A", home: "St Edmund Hall", away: "Worcester", draw: true, kick: [1, 14], created: -9 * 24, priors: [42, 26, 32], shocks: [[-150, 0, 0.3], [-60, 2, 0.8], [-14, 0, -0.3]] },
    { id: 6, comp: "Men's Football Cuppers", round: "Group B", home: "Balliol", away: "Christ Church", draw: true, kick: [1, 16], created: -7 * 24, priors: [30, 28, 42], shocks: [[-100, 0, 0.6], [-30, 2, 0.5]] },
    { id: 7, comp: "Women's Football Cuppers", round: "Group A", home: "St Anne's", away: "Somerville", draw: true, kick: [3, 14], created: -5 * 24, priors: [38, 27, 35], shocks: [[-50, 1, 0.5]] },
    { id: 8, comp: "Men's Rugby Cuppers", round: "Round 1", home: "St Catherine's", away: "Jesus", draw: false, kick: [4, 14], created: -6 * 24, priors: [58, 42], shocks: [[-70, 1, 0.7], [-8, 0, 0.25]] },
    { id: 9, comp: "Mixed Netball Cuppers", round: "Quarter-final", home: "Lincoln", away: "Merton", draw: false, kick: [5, 18], created: -4 * 24, priors: [47, 53], shocks: [[-40, 0, 0.6]] },
    { id: 10, comp: "Men's Hockey Cuppers", round: "Group B", home: "Hertford", away: "University", draw: true, kick: [7, 13], created: -3 * 24, priors: [36, 24, 40], shocks: [[-20, 0, 0.5]] },
    { id: 11, comp: "Mixed Lacrosse Cuppers", round: "Semi-final", home: "Wadham", away: "Pembroke", draw: false, kick: [9, 14], created: -2 * 24, priors: [51, 49], shocks: [] },
  ];

  const ARTICLES = [];

  const S = {
    races: {}, markets: {}, orders: [], trades: {}, positions: {}, briefs: {},
    nextOrder: 5001, nextMarket: 1, nextRace: 12, lastLive: Date.now(),
    user: { id: 7, username: "mockuser", cash: 100000, is_admin: true },
  };
  function loggedIn() { try { return localStorage.getItem("mockLoggedOut") !== "1"; } catch (e) { return true; } }
  function setLoggedIn(v) { try { v ? localStorage.removeItem("mockLoggedOut") : localStorage.setItem("mockLoggedOut", "1"); } catch (e) { /* ignore */ } }

  function norm(x) {
    const e = x.map(Math.exp), s = e.reduce((a, b) => a + b, 0);
    return e.map((v) => clamp(Math.round((v / s) * 100), 1, 99));
  }
  function genSeries(def, r) {
    const rand = rng(def.id * 7919);
    const end = Math.min(NOW0, r.closes_at);
    let x = def.priors.map((p) => Math.log(p));
    const pts = [];
    for (let t = r.created_at; t <= end; t += H) {
      const h = Math.round((t - NOW0) / H);
      x = x.map((v) => v + (rand() - 0.5) * 0.09);
      (def.shocks || []).forEach((s) => { if (s[0] === h) x[s[1]] += s[2]; });
      pts.push({ t, p: norm(x) });
    }
    if (!pts.length) pts.push({ t: r.created_at, p: norm(x) });
    return pts;
  }

  function kickTime(k) {
    if (typeof k === "number") return NOW0 + k * H;
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    return d.getTime() + k[0] * D + k[1] * H;
  }
  function buildRace(def) {
    const names = def.draw ? [def.home, "Draw", def.away] : [def.home, def.away];
    const closes = kickTime(def.kick);
    const status = def.winner ? "resolved" : closes <= Date.now() ? "closed" : "open";
    const r = {
      id: def.id, election: TERM, position: `${def.comp}: ${def.home} vs ${def.away}`, seats: 1,
      description: `${def.round} · ${def.comp}`, resolution_criteria: crit(def.comp),
      closes_at: closes, status, source_name: "Cherwell", source_url: "https://cherwell.org/sport/",
      created_at: NOW0 + def.created * H, resolved_at: def.winner ? closes + 3 * H : null,
      marketIds: [], series: [],
    };
    const vr = rng(def.id * 104729);
    names.forEach((name, i) => {
      const id = S.nextMarket++;
      S.markets[id] = { id, race_id: r.id, candidate: name, ai_prior: def.priors[i], outcome: null, created_at: r.created_at, price: def.priors[i], vol: status === "open" ? 80 + Math.floor(vr() * 900) : 0 };
      r.marketIds.push(id);
    });
    {
      r.series = genSeries(def, r);
      if (def.winner) {
        // Price converges on the result in the final hours.
        const k = names.indexOf(def.winner);
        r.series.push({ t: closes + H, p: names.map((_, i) => (i === k ? 96 : 2)) });
      }
      const last = r.series[r.series.length - 1].p;
      r.marketIds.forEach((id, i) => {
        const m = S.markets[id];
        m.price = last[i];
        if (def.winner) m.outcome = names[i] === def.winner ? "YES" : "NO";
      });
    }
    S.races[r.id] = r;
  }
  DEFS.forEach(buildRace);

  // ------------------------------------------------------------ prices
  const raceOf = (m) => S.races[m.race_id];
  function priceAt(m, t) {
    const r = raceOf(m), ser = r.series, k = r.marketIds.indexOf(m.id);
    if (!ser.length || t < ser[0].t) return null;
    const lastT = ser[ser.length - 1].t;
    if (t >= lastT) {
      if (r.status !== "open") return ser[ser.length - 1].p[k];
      const f = Math.min(1, (t - lastT) / Math.max(1, Date.now() - lastT));
      return ser[ser.length - 1].p[k] + (m.price - ser[ser.length - 1].p[k]) * f;
    }
    const i = Math.min(ser.length - 1, Math.floor((t - ser[0].t) / H));
    const a = ser[i], b = ser[i + 1] || a;
    return a.p[k] + (b.p[k] - a.p[k]) * ((t - a.t) / H);
  }
  function fineSeries(m, step, since) {
    const r = raceOf(m);
    if (!r.series.length) return [];
    const until = r.status === "open" ? Date.now() : Math.min(Date.now(), r.series[r.series.length - 1].t);
    const pts = [];
    for (let t = Math.ceil(Math.max(since, r.series[0].t) / step) * step; t <= until; t += step) {
      const v = priceAt(m, t);
      if (v == null) continue;
      const noise = step < H ? (rng(m.id * 100003 + Math.floor(t / step))() - 0.5) * 1.6 : 0;
      pts.push({ t, close: clamp(Math.round(v + noise), 1, 99) });
    }
    if (r.status === "open") pts.push({ t: Date.now(), close: m.price });
    return pts;
  }
  function sinceFor(bucket, sinceParam, r) {
    const floor = bucket === "1m" ? Date.now() - 6 * H : bucket === "5m" ? Date.now() - 3 * D : r.created_at;
    const asked = sinceParam ? Date.parse(sinceParam) : bucket === "1m" ? Date.now() - 3 * H : bucket === "5m" ? Date.now() - 2 * D : r.created_at;
    return Math.max(floor, isNaN(asked) ? floor : asked);
  }
  const STEP = { "1m": 60e3, "5m": 300e3, "1h": H };

  // Small random moves every couple of seconds, so open matches feel live.
  function tickLive() {
    const now = Date.now();
    const n = Math.min(5, Math.floor((now - S.lastLive) / 2000));
    if (n < 1) return;
    S.lastLive = now;
    Object.values(S.races).filter((r) => r.status === "open").forEach((r) => {
      for (let i = 0; i < n; i++) {
        if (Math.random() > 0.3) continue;
        const ms = r.marketIds.map((id) => S.markets[id]);
        const up = ms[Math.floor(Math.random() * ms.length)], dn = ms[Math.floor(Math.random() * ms.length)];
        if (up === dn || dn.price <= 2 || up.price >= 98) continue;
        up.price++; dn.price--;
        addTrade(up, up.price, "buy", 5 + Math.floor(Math.random() * 40));
      }
    });
    Object.values(S.races).forEach((r) => {
      if (r.status === "open" && r.closes_at <= now) r.status = "closed";
    });
  }

  // ------------------------------------------------------------ JSON shapes
  function questionFor(r, name) {
    const m = r.position.match(/^(.*?):\s*(.+?)\s+vs\s+(.+)$/);
    if (!m) return `Will ${name} win (${r.election})?`;
    if (name === "Draw") return `Will ${m[2]} vs ${m[3]} end in a draw (${m[1]}, ${r.election})?`;
    return `Will ${name} beat ${name === m[2] ? m[3] : m[2]} (${m[1]}, ${r.election})?`;
  }
  function marketJson(m) {
    const r = raceOf(m);
    const ago = priceAt(m, Date.now() - D);
    const spark = [];
    for (let i = 23; i >= 0; i--) { const v = priceAt(m, Date.now() - i * H); if (v != null) spark.push(Math.round(v)); }
    return {
      id: m.id, race_id: r.id, candidate: m.candidate, question: questionFor(r, m.candidate),
      description: r.description, resolution_criteria: r.resolution_criteria, category: "cuppers",
      source_name: r.source_name, source_url: r.source_url, closes_at: iso(r.closes_at), status: r.status,
      outcome: m.outcome, price: m.price, change_24h: ago == null ? 0 : Math.round(m.price - ago),
      volume_24h: m.vol, ai_prior: m.ai_prior, created_at: iso(m.created_at), sparkline: spark,
    };
  }
  function raceJson(r, withCands) {
    const cands = r.marketIds.map((id) => marketJson(S.markets[id])).sort((a, b) => b.price - a.price);
    const out = {
      id: r.id, election: r.election, position: r.position, seats: r.seats, description: r.description,
      closes_at: iso(r.closes_at), status: r.status, source_name: r.source_name, source_url: r.source_url,
      resolution_criteria: r.resolution_criteria, created_at: iso(r.created_at), resolved_at: iso(r.resolved_at),
      winners: cands.filter((c) => c.outcome === "YES").map((c) => c.id),
      overround: cands.reduce((a, c) => a + c.price, 0),
    };
    if (withCands !== false) out.candidates = cands;
    return out;
  }
  function userJson() {
    return { id: S.user.id, username: S.user.username, cash: S.user.cash, available: available(), is_admin: S.user.is_admin };
  }
  const orderJson = (o) => ({ id: o.id, market_id: o.market_id, side: o.side, price: o.price, qty: o.qty, qty_remaining: o.qty_remaining, status: o.status, created_at: iso(o.created_at) });

  // ------------------------------------------------------------ trading
  function addTrade(m, price, side, qty, time) {
    (S.trades[m.id] = S.trades[m.id] || []).unshift({ time: time || Date.now(), price, qty, taker_side: side });
    S.trades[m.id].length = Math.min(S.trades[m.id].length, 80);
    m.vol += qty;
  }
  function seedTrades(m) {
    if (S.trades[m.id]) return;
    S.trades[m.id] = [];
    const r = raceOf(m);
    if (!r.series.length) return;
    const rand = rng(m.id * 31337);
    const end = r.status === "open" ? Date.now() : r.closes_at;
    let t = end;
    for (let i = 0; i < 25; i++) {
      t -= (4 + rand() * 20) * 60e3;
      if (t < r.created_at) break;
      const p = clamp(Math.round((priceAt(m, t) || m.price) + (rand() - 0.5) * 2), 1, 99);
      S.trades[m.id].push({ time: t, price: p, qty: 5 + Math.floor(rand() * 60), taker_side: rand() > 0.5 ? "buy" : "sell" });
    }
  }
  function houseBook(m) {
    if (raceOf(m).status === "resolved") return { bids: [], asks: [] };
    const rand = rng(m.id * 31 + Math.floor(Date.now() / 6000));
    const asks = [], bids = [];
    for (let k = 1; k <= 6; k++) {
      if (m.price + k <= 99) asks.push({ price: m.price + k, qty: 20 + Math.floor(rand() * 90) });
      if (m.price - k >= 1) bids.push({ price: m.price - k, qty: 20 + Math.floor(rand() * 90) });
    }
    return { bids, asks };
  }
  function bookFor(m, depth) {
    const b = houseBook(m);
    const merge = (levels, price, qty) => {
      const l = levels.find((x) => x.price === price);
      if (l) l.qty += qty; else levels.push({ price, qty });
    };
    S.orders.filter((o) => o.market_id === m.id && o.status === "open").forEach((o) => merge(o.side === "buy" ? b.bids : b.asks, o.price, o.qty_remaining));
    b.bids.sort((x, y) => y.price - x.price);
    b.asks.sort((x, y) => x.price - y.price);
    return { bids: b.bids.slice(0, depth), asks: b.asks.slice(0, depth) };
  }
  function available() {
    let locked = 0;
    S.orders.filter((o) => o.status === "open").forEach((o) => { locked += (o.side === "buy" ? o.price : 100 - o.price) * o.qty_remaining; });
    Object.values(S.positions).forEach((p) => { if (p.qty < 0) locked += -p.qty * 100; });
    return S.user.cash - locked;
  }
  function addPosition(mid, dq, dc) {
    const p = S.positions[mid] = S.positions[mid] || { qty: 0, net_cost: 0 };
    p.qty += dq; p.net_cost += dc;
  }
  function placeOrder(b) {
    const m = S.markets[Number(b.market_id)];
    if (!m) throw err(400, "Market not found");
    if (raceOf(m).status !== "open") throw err(400, "Market is not open for trading");
    if (b.outcome !== "YES" && b.outcome !== "NO") throw err(400, "outcome must be YES or NO");
    if (b.side !== "buy" && b.side !== "sell") throw err(400, "side must be buy or sell");
    const type = b.type || "limit";
    const qty = Number(b.qty);
    if (!Number.isInteger(qty) || qty < 1 || qty > 10000) throw err(400, "Quantity must be a whole number from 1 to 10000");
    let yesPrice = null;
    if (type === "limit") {
      const p = Number(b.price);
      if (!Number.isInteger(p) || p < 1 || p > 99) throw err(400, "Price must be a whole number from 1 to 99");
      yesPrice = b.outcome === "YES" ? p : 100 - p;
    }
    const yesSide = (b.outcome === "YES") === (b.side === "buy") ? "buy" : "sell";
    const levels = houseBook(m)[yesSide === "buy" ? "asks" : "bids"];
    const fills = [];
    let rem = qty;
    for (const l of levels) {
      if (rem <= 0) break;
      if (yesPrice != null && (yesSide === "buy" ? l.price > yesPrice : l.price < yesPrice)) break;
      const q = Math.min(rem, l.qty);
      fills.push({ price: l.price, qty: q });
      rem -= q;
    }
    const resting = type === "limit" ? rem : 0;
    const outCost = (yp) => (yesSide === "buy" ? yp : 100 - yp);
    const need = fills.reduce((a, f) => a + outCost(f.price) * f.qty, 0) + (resting ? outCost(yesPrice) * resting : 0);
    if (need > available()) throw err(400, `Insufficient funds: needs ${(need / 100).toFixed(2)}, available ${(available() / 100).toFixed(2)}`);
    fills.forEach((f) => {
      const cost = f.price * f.qty;
      if (yesSide === "buy") { S.user.cash -= cost; addPosition(m.id, f.qty, cost); } else { S.user.cash += cost; addPosition(m.id, -f.qty, -cost); }
      addTrade(m, f.price, yesSide, f.qty);
    });
    if (fills.length) m.price = clamp(m.price + (yesSide === "buy" ? 1 : -1), 1, 99);
    let order = null;
    if (resting) {
      order = { id: S.nextOrder++, market_id: m.id, side: yesSide, price: yesPrice, qty, qty_remaining: resting, status: "open", created_at: Date.now() };
      S.orders.push(order);
    }
    return { order: order && orderJson(order), fills, user: userJson() };
  }

  // Starting portfolio for the mock user.
  function seedPortfolio() {
    const find = (rid, name) => S.races[rid].marketIds.map((id) => S.markets[id]).find((m) => m.candidate === name);
    const hold = (m, qty, avg) => { addPosition(m.id, qty, qty * avg); S.user.cash -= qty * avg; };
    hold(find(5, "Worcester"), 40, 27);
    hold(find(8, "Jesus"), -25, 50); // sold YES at 50 = bought NO at 50
    hold(find(7, "Draw"), 30, 24);
    hold(find(4, "Exeter"), 20, 48);
    const w = find(6, "Balliol");
    S.orders.push({ id: S.nextOrder++, market_id: w.id, side: "buy", price: Math.max(1, w.price - 4), qty: 50, qty_remaining: 50, status: "open", created_at: Date.now() - 2 * H });
  }
  seedPortfolio();

  // ------------------------------------------------------------ AI text
  function briefFor(r) {
    const cands = raceJson(r).candidates;
    const lead = cands[0], mover = cands.slice().sort((a, b) => Math.abs(b.change_24h) - Math.abs(a.change_24h))[0];
    const name = (c) => (c.candidate === "Draw" ? "the draw" : c.candidate);
    let t = `${lead.candidate === "Draw" ? "A draw" : lead.candidate} ${lead.candidate === "Draw" ? "is" : "are"} the most likely result at ${lead.price}%.`;
    if (mover && Math.abs(mover.change_24h) >= 2) t += ` The biggest move in the last 24 hours was ${name(mover)} (${mover.change_24h > 0 ? "+" : ""}${mover.change_24h} points).`;
    else t +=  " The odds have barely moved in the last 24 hours.";
    return t;
  }
  S.briefs = {};
  Object.values(S.races).forEach((r) => { S.briefs[r.id] = { text: briefFor(r), created_at: Date.now() - 2 * H }; });

  function digest() {
    const open = Object.values(S.races).filter((r) => r.status === "open").map((r) => raceJson(r));
    const next = open.slice().sort((a, b) => Date.parse(a.closes_at) - Date.parse(b.closes_at))[0];
    const movers = open.flatMap((r) => r.candidates.map((c) => ({ c, r }))).sort((a, b) => Math.abs(b.c.change_24h) - Math.abs(a.c.change_24h));
    const mv = movers[0];
    let t = `${open.length} Cuppers matches are open for trading.`;
    if (next) t += ` Next up is ${next.position.split(": ")[1]}, where ${next.candidates[0].candidate === "Draw" ? "a draw" : next.candidates[0].candidate} ${next.candidates[0].candidate === "Draw" ? "is" : "are"} favoured at ${next.candidates[0].price}%.`;
    if (mv) t += ` Biggest mover: ${mv.c.candidate} in ${mv.r.position.split(": ")[1]} (${mv.c.change_24h > 0 ? "+" : ""}${mv.c.change_24h} points).`;
    return { text: t, created_at: iso(Date.now() - 5 * H) };
  }

  // ------------------------------------------------------------ admin helpers
  function validateRace(b, partial) {
    if (!partial || b.position !== undefined) if (!String(b.position || "").trim()) throw err(400, "Match (position) is required");
    if (!partial || b.seats !== undefined) { const s = Number(b.seats); if (!Number.isInteger(s) || s < 1 || s > 10) throw err(400, "seats must be 1..10"); }
    if (!partial || b.closes_at !== undefined) if (isNaN(Date.parse(b.closes_at))) throw err(400, "closes_at must be an ISO date");
    if (!partial || b.candidates !== undefined) {
      const c = b.candidates || [];
      if (c.length < 2 || c.length > 12) throw err(400, "Need 2 to 12 outcomes");
      const names = c.map((x) => String(x.name || "").trim());
      if (names.some((n) => !n) || new Set(names).size !== names.length) throw err(400, "Outcome names must be non-empty and unique");
      if (c.some((x) => !(x.prior >= 1 && x.prior <= 99))) throw err(400, "Priors must be 1..99");
    }
  }
  function setOutcomes(r, cands) {
    r.marketIds.forEach((id) => delete S.markets[id]);
    r.marketIds = [];
    cands.forEach((c) => {
      const id = S.nextMarket++;
      S.markets[id] = { id, race_id: r.id, candidate: c.name.trim(), ai_prior: c.prior, outcome: null, created_at: Date.now(), price: c.prior, vol: 0 };
      r.marketIds.push(id);
    });
  }
  function openRace(r) {
    r.status = "open";
    r.created_at = Math.min(r.created_at, NOW0);
    r.series = [{ t: NOW0, p: r.marketIds.map((id) => S.markets[id].price) }];
  }

  // ------------------------------------------------------------ router
  function err(status, error) { return { status, error }; }
  function needUser() { if (!loggedIn()) throw err(401, "Not logged in"); }
  function needAdmin() { needUser(); if (!S.user.is_admin) throw err(403, "Admins only"); }
  function getRace(id) { const r = S.races[id]; if (!r || r.status === "rejected") throw err(404, "Match not found"); return r; }
  function getMarket(id) { const m = S.markets[id]; if (!m) throw err(404, "Market not found"); return m; }
  const articleJson = (a) => ({ id: a.id, title: a.title, url: a.url, source: a.source, published_at: iso(a.published_at), summary: a.summary });

  const routes = [
    ["GET", /^\/api\/me$/, () => { needUser(); return { user: userJson() }; }],
    ["POST", /^\/api\/login$/, (b) => login(b, false)],
    ["POST", /^\/api\/register$/, (b) => login(b, true)],
    ["POST", /^\/api\/logout$/, () => { setLoggedIn(false); return { ok: true }; }],

    ["GET", /^\/api\/races$/, (b, q) => {
      const st = q.get("status") || "open";
      const list = Object.values(S.races).filter((r) => (st === "all" ? ["open", "closed", "resolved"].includes(r.status) : r.status === st));
      list.sort((a, b2) => a.closes_at - b2.closes_at);
      return { races: list.map((r) => raceJson(r)) };
    }],
    ["GET", /^\/api\/races\/(\d+)$/, (b, q, id) => ({ race: raceJson(getRace(+id)) })],
    ["GET", /^\/api\/races\/(\d+)\/history$/, (b, q, id) => {
      const r = getRace(+id), bucket = STEP[q.get("bucket")] ? q.get("bucket") : "5m";
      const since = sinceFor(bucket, q.get("since"), r);
      return {
        bucket, series: r.marketIds.map((mid) => {
          const m = S.markets[mid];
          return { market_id: mid, candidate: m.candidate, points: fineSeries(m, STEP[bucket], since).map((p) => ({ t: iso(p.t), close: p.close })) };
        }),
      };
    }],
    ["GET", /^\/api\/races\/(\d+)\/brief$/, (b, q, id) => { getRace(+id); const br = S.briefs[+id]; return { brief: br ? { text: br.text, created_at: iso(br.created_at) } : null }; }],
    ["POST", /^\/api\/races\/(\d+)\/brief\/refresh$/, (b, q, id) => {
      needUser();
      const r = getRace(+id), br = S.briefs[r.id];
      if (br && Date.now() - br.created_at < 10 * 60e3) throw err(400, "This analysis was refreshed in the last 10 minutes. Try again later.");
      S.briefs[r.id] = { text: briefFor(r), created_at: Date.now() };
      return { brief: { text: S.briefs[r.id].text, created_at: iso(Date.now()) } };
    }],

    ["GET", /^\/api\/markets$/, (b, q) => {
      const rid = q.get("race_id"), st = q.get("status");
      return { markets: Object.values(S.markets).filter((m) => (!rid || m.race_id === +rid) && (!st || raceOf(m).status === st)).map(marketJson) };
    }],
    ["GET", /^\/api\/markets\/(\d+)$/, (b, q, id) => { const m = getMarket(+id); return { market: marketJson(m), race: raceJson(raceOf(m), false) }; }],
    ["GET", /^\/api\/markets\/(\d+)\/history$/, (b, q, id) => {
      const m = getMarket(+id), bucket = STEP[q.get("bucket")] ? q.get("bucket") : "5m";
      const pts = fineSeries(m, STEP[bucket], sinceFor(bucket, q.get("since"), raceOf(m)));
      return { bucket, points: pts.map((p, i) => { const o = i ? pts[i - 1].close : p.close; return { t: iso(p.t), open: o, high: Math.max(o, p.close), low: Math.min(o, p.close), close: p.close, volume: (p.t / 60e3) % 7 * 3 }; }) };
    }],
    ["GET", /^\/api\/markets\/(\d+)\/trades$/, (b, q, id) => {
      const m = getMarket(+id); seedTrades(m);
      return { trades: S.trades[m.id].slice(0, Number(q.get("limit")) || 50).map((t) => ({ time: iso(t.time), price: t.price, qty: t.qty, taker_side: t.taker_side })) };
    }],
    ["GET", /^\/api\/markets\/(\d+)\/book$/, (b, q, id) => bookFor(getMarket(+id), Number(q.get("depth")) || 10)],

    ["POST", /^\/api\/orders$/, (b) => { needUser(); seedTrades(getMarket(+b.market_id)); return placeOrder(b); }],
    ["DELETE", /^\/api\/orders\/(\d+)$/, (b, q, id) => {
      needUser();
      const o = S.orders.find((x) => x.id === +id && x.status === "open");
      if (!o) throw err(404, "Order not found");
      o.status = "cancelled";
      return { ok: true };
    }],
    ["GET", /^\/api\/orders$/, (b, q) => { needUser(); return { orders: S.orders.filter((o) => q.get("status") === "all" || o.status === "open").slice().reverse().map(orderJson) }; }],
    ["GET", /^\/api\/portfolio$/, () => {
      needUser();
      const positions = Object.entries(S.positions).filter(([, p]) => p.qty !== 0).map(([mid, p]) => {
        const m = S.markets[mid], r = raceOf(m);
        return { market_id: m.id, question: questionFor(r, m.candidate), status: r.status, race_id: r.id, candidate: m.candidate, position: r.position,
          qty: p.qty, label: p.qty > 0 ? "YES" : "NO", shares: Math.abs(p.qty), price: m.price, value: p.qty * m.price, net_cost: p.net_cost };
      });
      return { user: userJson(), equity: equity(), positions };
    }],

    ["GET", /^\/api\/articles$/, (b, q) => ({ articles: ARTICLES.filter((a) => a.published_at <= Date.now()).sort((a, b2) => b2.published_at - a.published_at).slice(0, Number(q.get("limit")) || 20).map(articleJson) })],
    ["GET", /^\/api\/digest$/, () => ({ digest: digest() })],

    ["GET", /^\/api\/timemachine\/range$/, () => {
      const starts = Object.values(S.races).filter((r) => r.series.length).map((r) => r.created_at);
      if (!starts.length) return { start: iso(Date.now()), end: iso(Date.now()) };
      return { start: iso(Math.min(...starts)), end: iso(Date.now()) };
    }],
    ["GET", /^\/api\/timemachine$/, (b, q) => {
      const t = Date.parse(q.get("t"));
      if (isNaN(t)) throw err(400, "t must be an ISO timestamp");
      const races = Object.values(S.races).filter((r) => r.series.length && r.created_at <= t).sort((a, b2) => a.closes_at - b2.closes_at).map((r) => ({
        id: r.id, election: r.election, position: r.position, seats: r.seats, closes_at: iso(r.closes_at),
        status: r.resolved_at && t >= r.resolved_at ? "resolved" : t >= r.closes_at ? "closed" : "open",
        candidates: r.marketIds.map((mid) => ({ market_id: mid, candidate: S.markets[mid].candidate, price: Math.round(priceAt(S.markets[mid], t)) })),
      }));
      const articles = ARTICLES.filter((a) => a.published_at <= t).sort((a, b2) => b2.published_at - a.published_at).slice(0, 10)
        .map((a) => ({ title: a.title, url: a.url, source: a.source, published_at: iso(a.published_at) }));
      return { t: iso(t), races, articles };
    }],
    ["GET", /^\/api\/leaderboard$/, () => ({ leaders: leaders().map((l, i) => ({ rank: i + 1, username: l.username, equity: l.equity })) })],
    ["GET", /^\/api\/leaderboard\/history$/, (b, q) => {
      const top = leaders().slice(0, Number(q.get("top")) || 5);
      return { series: top.map((l, i) => {
        const rand = rng(i * 977 + 3), pts = [];
        let e = 100000;
        const n = 7 * 24;
        for (let k = 0; k <= n; k++) {
          e += (rand() - 0.47) * 900;
          const target = 100000 + ((l.equity - 100000) * k) / n;
          const v = Math.round(e * 0.35 + target * 0.65);
          pts.push({ t: iso(NOW0 - (n - k) * H), equity: k === n ? l.equity : v });
        }
        return { username: l.username, points: pts };
      }) };
    }],

    ["POST", /^\/api\/admin\/races$/, (b) => {
      needAdmin(); validateRace(b);
      const position = b.position.trim();
      if (Object.values(S.races).some((r) => r.election === b.election && r.position === position)) throw err(400, "That match already exists");
      const r = { id: S.nextRace++, election: b.election || TERM, position, seats: Number(b.seats), description: b.description || "", resolution_criteria: b.resolution_criteria || crit("Cuppers"),
        closes_at: Date.parse(b.closes_at), status: "open", source_name: null, source_url: null, created_at: Date.now(), resolved_at: null, marketIds: [], series: [] };
      S.races[r.id] = r;
      setOutcomes(r, b.candidates);
      openRace(r);
      return { race: raceJson(r) };
    }],
    ["POST", /^\/api\/admin\/races\/(\d+)\/candidates$/, (b, q, id) => {
      needAdmin();
      const r = getRace(+id), name = String(b.name || "").trim(), prior = Number(b.prior) || 5;
      if (!name) throw err(400, "Name is required");
      if (r.marketIds.some((mid) => S.markets[mid].candidate === name)) throw err(400, "That outcome already exists");
      const mid = S.nextMarket++;
      S.markets[mid] = { id: mid, race_id: r.id, candidate: name, ai_prior: prior, outcome: null, created_at: Date.now(), price: prior, vol: 0 };
      r.marketIds.push(mid);
      r.series.forEach((p) => p.p.push(prior));
      return { market: marketJson(S.markets[mid]) };
    }],
    ["POST", /^\/api\/admin\/races\/(\d+)\/close$/, (b, q, id) => {
      needAdmin();
      const r = getRace(+id);
      if (r.status !== "open") throw err(400, "Match is not open");
      r.status = "closed"; r.closes_at = Math.min(r.closes_at, Date.now());
      S.orders.filter((o) => o.status === "open" && r.marketIds.includes(o.market_id)).forEach((o) => { o.status = "cancelled"; });
      return { race: raceJson(r) };
    }],
    ["POST", /^\/api\/admin\/races\/(\d+)\/resolve$/, (b, q, id) => {
      needAdmin();
      const r = getRace(+id), ids = (b.winner_market_ids || []).map(Number);
      if (r.status !== "closed") throw err(400, "Close trading on the match before posting the result");
      if (ids.length !== r.seats || ids.some((x) => !r.marketIds.includes(x))) throw err(400, `Pick exactly ${r.seats} winning outcome(s) from this match`);
      r.marketIds.forEach((mid) => {
        const m = S.markets[mid], win = ids.includes(mid), p = S.positions[mid];
        m.outcome = win ? "YES" : "NO";
        if (p) { S.user.cash += win ? p.qty * 100 : 0; delete S.positions[mid]; }
      });
      r.status = "resolved"; r.resolved_at = Date.now();
      return { race: raceJson(r) };
    }],
    ["GET", /^\/api\/admin\/invariants$/, () => ({ ok: true, problems: [] })],
    // admin portal (read-only in mock mode beyond what the routes above support)
    ["GET", /^\/api\/meta$/, () => ({
      categories: [{ slug: "cuppers", name: "Cuppers", sort: 10 }, { slug: "news", name: "Oxford news", sort: 20 }, { slug: "union", name: "Oxford Union", sort: 30 }],
      sports: [], competitions: Array.from(new Set(DEFS.map((d) => d.comp))).map((n, i) => ({ id: i + 1, name: n, has_draw: /Football|Hockey/.test(n), active: true })),
      teams: Array.from(new Set(DEFS.flatMap((d) => [d.home, d.away]))).sort(), terms: [TERM], current_term: TERM,
      defaults: { priors_draw: [40, 25, 35], priors_no_draw: [50, 50], criteria_match: crit("Cuppers"), criteria_generic: "Resolves by admin decision." },
    })],
    ["GET", /^\/api\/admin\/races$/, () => { needAdmin(); return { races: Object.values(S.races).map((r) => raceJson(r, true)) }; }],
    ["GET", /^\/api\/admin\/articles$/, () => ({ articles: [] })],
    ["GET", /^\/api\/admin\/settings$/, () => ({ settings: [{ key: "mm.size", value: 50, description: "Market maker: shares quoted on each side" }] })],
    ["GET", /^\/api\/admin\/ref\/(\w+)$/, () => ({ rows: [] })],
    ["GET", /^\/api\/admin\/actions$/, () => ({ actions: [] })],
  ];

  function login(b, register) {
    const u = String(b.username || "");
    if (!/^[A-Za-z0-9_]{3,20}$/.test(u)) throw err(400, "Username must be 3-20 letters, digits or _");
    if (String(b.password || "").length < 6) throw err(400, "Password must be at least 6 characters");
    S.user.username = u;
    setLoggedIn(true);
    return { user: userJson(), _status: register ? 201 : 200 };
  }
  function equity() {
    return S.user.cash + Object.entries(S.positions).reduce((a, [mid, p]) => a + p.qty * S.markets[mid].price, 0);
  }
  function leaders() {
    const others = [["dreaming_spire", 131250], ["boatie42", 118900], ["rad_cam", 112040], ["iffley_ultra", 107310], ["quad_rat", 103880],
      ["punt_pilot", 99120], ["blue_jumper", 96400], ["bod_card", 91050], ["isis_rower", 87730]];
    const list = others.map(([username, e]) => ({ username, equity: e }));
    if (loggedIn()) list.push({ username: S.user.username, equity: equity() });
    return list.sort((a, b) => b.equity - a.equity);
  }

  function handle(method, path, body) {
    return new Promise((resolve, reject) => {
      setTimeout(() => {
        try {
          tickLive();
          const url = new URL(path, "http://mock.local");
          for (const [m, re, fn] of routes) {
            if (m !== method) continue;
            const hit = url.pathname.match(re);
            if (hit) {
              const res = fn(body || {}, url.searchParams, ...hit.slice(1));
              if (res && res._status) delete res._status;
              return resolve(JSON.parse(JSON.stringify(res)));
            }
          }
          reject(err(404, `Mock has no route for ${method} ${url.pathname}`));
        } catch (e) {
          reject(e && e.status ? e : err(500, String(e && e.message || e)));
        }
      }, 60 + Math.random() * 90);
    });
  }

  window.MOCK = { handle };
})();
