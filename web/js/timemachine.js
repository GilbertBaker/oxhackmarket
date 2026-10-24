// Time Machine: scrub or play through the term and see every match's odds as of
// that moment (GET /api/timemachine?t=...). Rows re-sort with a FLIP animation
// when the favourite changes; diamonds on the track mark those changes.
(function () {
  "use strict";
  const { $, esc, pct, isoNoMs, parseMatch, isDraw, outcomeLabel, sportOf, sourceName, initNav, errText } = App;

  const SL = $("#tm-slider");
  let start = 0, end = 0, t = 0, playing = false, timer = null, debounce = null, inflight = false;
  const cards = new Map(), leaders = new Map();
  initNav("timemachine");

  const tAt = (v) => start + ((end - start) * v) / 1000;
  const vAt = (ms) => Math.round(((ms - start) / (end - start)) * 1000);

  async function init() {
    let r;
    try {
      r = await api.get("/api/timemachine/range");
    } catch (e) {
      $("#tm-empty").textContent = "Couldn't load the Time Machine: " + errText(e);
      return;
    }
    start = Date.parse(r.start);
    end = Date.parse(r.end);
    if (!(end > start)) { $("#tm-empty").textContent = "Nothing to replay yet. Come back once matches have traded."; return; }
    $("#tm-start").textContent = new Date(start).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
    $("#tm-end").textContent = "Now";
    SL.disabled = false;
    SL.value = 1000;
    seek(end, true);
    loadMarkers();
  }

  function showClock(ms) {
    const d = new Date(ms);
    $("#tm-date").textContent = d.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
    $("#tm-time").textContent = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
    SL.setAttribute("aria-valuetext", d.toLocaleString("en-GB"));
  }

  // Debounced while scrubbing; while playing, one request in flight at a time.
  function seek(ms, now) {
    t = Math.max(start, Math.min(end, ms));
    showClock(t);
    clearTimeout(debounce);
    if (now || playing) fetchAt(); else debounce = setTimeout(fetchAt, 150);
  }
  async function fetchAt() {
    if (inflight) return;
    inflight = true;
    const want = t;
    try {
      render(await api.get(`/api/timemachine?t=${encodeURIComponent(isoNoMs(want))}`), want);
    } catch (e) {
      console.warn("Time Machine fetch failed:", errText(e));
    } finally {
      inflight = false;
      if (t !== want && !playing) fetchAt();
    }
  }

  function render(d, at) {
    const grid = $("#tm-races");
    const races = (d.races || []).slice().sort((a, b) => {
      const rank = { open: 0, closed: 1, resolved: 2 };
      return (rank[a.status] - rank[b.status]) || (Date.parse(a.closes_at || 0) - Date.parse(b.closes_at || 0));
    });
    $("#tm-empty").hidden = races.length > 0;
    if (!races.length) $("#tm-empty").textContent = "No matches had been posted yet at this point.";
    const seen = new Set();
    races.forEach((r) => {
      let el = cards.get(r.id);
      if (!el) {
        const m = parseMatch(r.position);
        el = document.createElement("article");
        el.className = "card race-card tm-card";
        el.innerHTML = `<div class="match-top"><span class="comp"><span aria-hidden="true">${sportOf(m.competition || r.position).icon}</span>${esc(m.competition)}</span><span class="when tm-status"></span></div>
          <a class="match-title" href="race.html?id=${r.id}">${esc(m.title)}</a><ol class="tm-list"></ol><div class="tm-lead"></div>`;
        cards.set(r.id, el);
      }
      grid.appendChild(el);
      seen.add(r.id);
      updateCard(el, r);
    });
    cards.forEach((el, rid) => { if (!seen.has(rid)) { el.remove(); cards.delete(rid); leaders.delete(rid); } });
    renderSummary(races, at);
    const arts = d.articles || [];
    $("#tm-news-card").hidden = !arts.length;
    $("#tm-articles").innerHTML = arts.map((a) => `<li><span class="src">${esc(sourceName(a.source))}</span>
      <a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.title)}</a>
      <span class="faint tiny">${esc(new Date(a.published_at).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }))}</span></li>`).join("");
  }

  function updateCard(el, r) {
    const list = el.querySelector(".tm-list");
    const sorted = r.candidates.slice().sort((a, b) => b.price - a.price);
    const fav = sorted[0];
    const rows = new Map(Array.from(list.children).map((li) => [Number(li.dataset.id), li]));
    const before = new Map();
    rows.forEach((li, k) => before.set(k, li.getBoundingClientRect().top));

    sorted.forEach((c) => {
      let li = rows.get(c.market_id);
      if (!li) {
        li = document.createElement("li");
        li.className = "tm-row";
        li.dataset.id = c.market_id;
        li.innerHTML = `<span class="tm-name"></span><span class="tm-pct num"></span><span class="bar" aria-hidden="true"><span style="width:0"></span></span>`;
        li.querySelector(".tm-name").textContent = isDraw(c.candidate) ? "Draw" : c.candidate;
      }
      li.classList.toggle("is-fav", c === fav);
      li.querySelector(".tm-pct").textContent = pct(c.price);
      li.querySelector(".bar > span").style.width = c.price + "%";
      list.appendChild(li);
      rows.delete(c.market_id);
    });
    rows.forEach((li) => li.remove());

    // FLIP: start each moved row at its old position, then let it slide.
    const moved = [];
    Array.from(list.children).forEach((li) => {
      const old = before.get(Number(li.dataset.id));
      const dy = old == null ? 0 : old - li.getBoundingClientRect().top;
      if (dy) { li.style.transition = "none"; li.style.transform = `translateY(${dy}px)`; moved.push(li); }
    });
    if (moved.length) {
      void list.offsetHeight;
      moved.forEach((li) => { li.style.transition = ""; li.style.transform = ""; });
    }

    const prev = leaders.get(r.id);
    const status = el.querySelector(".tm-status");
    status.textContent = { open: "Trading", closed: "Kicked off", resolved: "Result in" }[r.status] || "";
    const lead = el.querySelector(".tm-lead");
    if (prev != null && prev !== fav.market_id) {
      lead.innerHTML = `<span class="new-lead">New favourite: ${esc(outcomeLabel(fav.candidate))}</span>`;
      el.classList.remove("flash");
      void el.offsetWidth;
      el.classList.add("flash");
    } else {
      lead.textContent = `Favourite: ${outcomeLabel(fav.candidate)} at ${pct(fav.price)}`;
    }
    leaders.set(r.id, fav.market_id);
  }

  function renderSummary(races, at) {
    const open = races.filter((r) => r.status === "open");
    const done = races.filter((r) => r.status !== "open");
    const next = open.slice().sort((a, b) => Date.parse(a.closes_at) - Date.parse(b.closes_at))[0];
    const items = [`<li><b>${open.length}</b> ${open.length === 1 ? "match" : "matches"} open for trading, <b>${done.length}</b> played.</li>`];
    if (next && next.closes_at) {
      const fav = next.candidates.slice().sort((a, b) => b.price - a.price)[0];
      const ms = Date.parse(next.closes_at) - at;
      const hrs = Math.max(0, Math.round(ms / 3600e3));
      items.push(`<li>Next kick-off: <a href="race.html?id=${next.id}">${esc(parseMatch(next.position).title)}</a>
        <span class="muted small">in ${hrs >= 48 ? Math.round(hrs / 24) + " days" : hrs + " hours"}, ${esc(outcomeLabel(fav.candidate))} favoured at ${pct(fav.price)}</span></li>`);
    }
    const tightest = open.map((r) => {
      const s = r.candidates.slice().sort((a, b) => b.price - a.price);
      return { r, gap: s.length > 1 ? s[0].price - s[1].price : 100 };
    }).sort((a, b) => a.gap - b.gap)[0];
    if (tightest) items.push(`<li>Closest call: <a href="race.html?id=${tightest.r.id}">${esc(parseMatch(tightest.r.position).title)}</a> <span class="muted small">(${tightest.gap ? `top two ${tightest.gap} points apart` : "dead level at the top"})</span></li>`);
    $("#tm-summary").innerHTML = items.join("");
  }

  // Diamonds where any match's favourite changed (hourly history, strict lead change).
  async function loadMarkers() {
    try {
      const d = await api.get("/api/races?status=all");
      const hist = await Promise.all((d.races || []).map((r) =>
        api.get(`/api/races/${r.id}/history?bucket=1h`).then((h) => ({ r, h }), () => null)));
      const marks = [];
      hist.filter(Boolean).forEach(({ r, h }) => {
        const series = h.series || [];
        if (series.length < 2) return;
        const n = Math.max(...series.map((s) => s.points.length));
        let leader = null;
        for (let i = 0; i < n; i++) {
          const pts = series.map((s) => ({ s, p: s.points[i] })).filter((x) => x.p && x.p.close != null);
          if (!pts.length) continue;
          pts.sort((a, b) => b.p.close - a.p.close);
          const top = pts[0], cur = pts.find((x) => x.s.market_id === leader);
          if (leader == null) { leader = top.s.market_id; continue; }
          // Needs a clear 3-point lead, so noise around a tie doesn't flood the track.
          if (top.s.market_id !== leader && (!cur || top.p.close >= cur.p.close + 3)) {
            marks.push({ t: Date.parse(top.p.t), text: `${outcomeLabel(top.s.candidate)} became favourite in ${parseMatch(r.position).title}` });
            leader = top.s.market_id;
          }
        }
      });
      $("#tm-markers").innerHTML = marks.filter((m) => m.t >= start && m.t <= end).slice(-60).map((m) =>
        `<button type="button" class="tm-marker" style="left:${(((m.t - start) / (end - start)) * 100).toFixed(2)}%" data-t="${m.t}"
          title="${esc(m.text)} (${esc(new Date(m.t).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }))})" aria-label="${esc(m.text)}"></button>`).join("");
    } catch (e) {
      console.warn("Couldn't compute lead changes:", errText(e));
    }
  }
  $("#tm-markers").addEventListener("click", (e) => {
    const b = e.target.closest("[data-t]");
    if (!b) return;
    stop();
    const ms = Number(b.dataset.t);
    SL.value = vAt(ms);
    seek(ms, true);
  });

  // ------------------------------------------------------------ playback
  function play() {
    if (!(end > start)) return;
    if (Number(SL.value) >= 1000) { SL.value = 0; seek(start, true); }
    playing = true;
    $("#tm-play").textContent = "❚❚";
    $("#tm-play").setAttribute("aria-label", "Pause");
    timer = setInterval(() => {
      const v = Math.min(1000, Number(SL.value) + Number($("#tm-speed").value) * 2);
      SL.value = v;
      seek(tAt(v));
      if (v >= 1000) stop();
    }, 250);
  }
  function stop() {
    playing = false;
    clearInterval(timer);
    $("#tm-play").textContent = "▶";
    $("#tm-play").setAttribute("aria-label", "Play");
  }
  $("#tm-play").addEventListener("click", () => (playing ? stop() : play()));
  SL.addEventListener("input", () => { stop(); seek(tAt(Number(SL.value))); });
  $("#tm-now").addEventListener("click", () => {
    stop();
    // The range end moves forward as time passes.
    api.get("/api/timemachine/range").then((r) => { end = Date.parse(r.end); }, () => {}).then(() => {
      SL.value = 1000;
      seek(end, true);
    });
  });
  document.addEventListener("visibilitychange", () => { if (document.hidden) stop(); });

  init();
})();
