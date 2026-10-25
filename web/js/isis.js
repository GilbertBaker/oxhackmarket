// Isis rowing page: tomorrow's "Can you row?" market, last 7 days, the linear regression
// model's call and a Gemini summary, all from GET /api/isis.
(function () {
  "use strict";
  const { $, esc, pct, countdown, initNav, errText, lineChart, setChartData, timeLabel, isoNoMs, loginHref, poll } = App;
  const COLOURS = { "Green": "#2e9e5b", "Light Blue": "#7cc4f0", "Dark Blue": "#1f4e9c", "Amber": "#f0a020",
    "Red": "#d63b2f", "Black": "#222", "Grey": "#9aa3b2" };
  const NAMES = {
    temperature_2m_C: "Temperature (°C)", precipitation_mm_prev_hour: "Rain, previous hour (mm)",
    wind_speed_10m_kmh: "Wind speed (km/h)", wind_gusts_10m_kmh: "Wind gusts (km/h)", visibility_m: "Visibility (m)",
    cape_J_per_kg: "CAPE (J/kg)", wind_dir_sin: "Wind direction (sin)", wind_dir_cos: "Wind direction (cos)",
  };
  const fname = (f) => NAMES[f] || (f.startsWith("wmo_") ? `Weather code ${f.slice(4)}` : f.startsWith("prev_") ? `Yesterday ${f.slice(5)}` : f);
  const sw = (flag) => `<span class="flag-sw" style="background:${COLOURS[flag] || "#ccc"}"></span>`;
  let chart = null, data = null;

  initNav("isis").then(() => poll(load, 30000));

  async function load() {
    try { data = await api.get("/api/isis"); } catch (e) {
      $("#current").innerHTML = `<p class="muted">Couldn't load the Isis market: ${esc(errText(e))}</p>`;
      throw e;
    }
    renderCurrent(); renderSummary(); renderDays(); renderModel(); loadChart();
  }

  function renderCurrent() {
    const c = data.current, o = data.official;
    if (!c) { $("#current").innerHTML = `<p class="muted">No Isis market is open right now.</p>`; return; }
    $("#q").textContent = c.question;
    const open = c.status === "open";
    $("#current").innerHTML = `
      <div class="price-line"><span class="big-pct num">${pct(c.price)}</span><span class="muted">chance crews can row at 07:00 on ${esc(c.target_date)}</span></div>
      <p class="small muted">Model: <b>${pct(c.ai_prior)}</b> · Closes in <span data-countdown="${esc(c.closes_at)}">${esc(countdown(c.closes_at))}</span></p>
      ${open ? `<div class="hero-price">
        <a class="buy-yes" href="market.html?id=${c.market_id}&amp;outcome=YES">Yes ${c.price}p</a>
        <a class="buy-no" href="market.html?id=${c.market_id}&amp;outcome=NO">No ${100 - c.price}p</a></div>` : ""}
      <p class="small" style="margin-top:12px">Official flag now: ${o ? `${sw(o.flag)}<b>${esc(o.flag)}</b> <span class="faint">(set ${esc(new Date(o.set_at).toLocaleString("en-GB"))})</span>` : `<span class="muted">unavailable</span>`}</p>
      ${o && o.notices && o.notices.length ? `<p class="tiny faint">${o.notices.map(esc).join(" ")}</p>` : ""}`;
  }

  function renderSummary() {
    const s = data.summary;
    $("#summary").innerHTML = s ? `${esc(s.text)}<p class="tiny faint" style="margin-top:8px">Updated ${esc(new Date(s.created_at).toLocaleString("en-GB"))}</p>`
      : `<p class="muted">No summary yet.</p>`;
  }

  function renderDays() {
    const h = data.history || [];
    $("#days").innerHTML = h.length ? h.map((d) => {
      const res = d.outcome === "YES" ? `<span class="res yes">YES</span>` : d.outcome === "NO" ? `<span class="res no">NO</span>` : `<span class="res">…</span>`;
      const call = d.model_right == null ? "" : d.model_right ? "✓ model right" : "✗ model wrong";
      return `<a class="day" href="market.html?id=${d.market_id}">
        <div class="tiny faint">${esc(new Date(d.date + "T12:00:00").toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" }))}</div>
        ${res}
        <div class="small">${sw(d.actual_flag)}${esc(d.actual_flag || "unknown")}</div>
        <div class="tiny faint">Model: ${esc(d.model_flag)} (${Math.round(d.model_p_row * 100)}%)<br>${call}</div></a>`;
    }).join("") : `<p class="muted">No finished days yet.</p>`;
  }

  function renderModel() {
    const f = data.forecast, m = data.model;
    if (!f) { $("#model-call").innerHTML = `<p class="muted">No forecast.</p>`; return; }
    const bars = Object.keys(COLOURS).filter((k) => k in f.shares).map((k) => [k, f.shares[k]]).map(([k, v]) => `<div class="row"><span>${sw(k)}${esc(k)}</span>
      <span class="track"><span class="fill" style="display:block;width:${Math.round(v * 100)}%;background:${COLOURS[k]}"></span></span>
      <span class="num small">${v.toFixed(2)}</span></div>`).join("");
    const inp = f.inputs;
    const rows = [["Weather", esc(inp.weather)], ["Temperature", `${inp.temperature_2m_C.toFixed(1)} °C`],
      ["Rain, previous hour", `${inp.precipitation_mm_prev_hour.toFixed(1)} mm`],
      ["Wind", `${inp.wind_speed_10m_kmh.toFixed(0)} km/h, gusts ${inp.wind_gusts_10m_kmh.toFixed(0)}, from ${inp.wind_direction_10m_deg.toFixed(0)}°`],
      ["Visibility", `${(inp.visibility_m / 1000).toFixed(1)} km`], ["Yesterday's flag", `${sw(inp.prev_flag)}${esc(inp.prev_flag)}`]];
    const contrib = f.contributions.map((c) => `<tr><td>${esc(fname(c.feature))}</td><td class="right num">${c.value}</td>
      <td class="right num ${c.effect >= 0 ? "up" : "down"}">${c.effect >= 0 ? "+" : ""}${c.effect.toFixed(3)}</td></tr>`).join("");
    $("#model-call").innerHTML = `
      <p>For 07:00 on <b>${esc(f.target_date)}</b> the model predicts ${sw(f.flag)}<b>${esc(f.flag)}</b>:
        <b>${f.rowable ? "crews can row" : "no rowing"}</b> (rowable share ${Math.round(f.p_row * 100)}%).</p>
      <div class="bars">${bars}</div>
      <p class="tiny faint">Least-squares scores per colour, clipped at 0 and normalised. A ranking, not a calibrated probability.</p>
      <h3 style="margin-top:14px">Inputs <span class="tiny faint">(${esc(f.weather_source)})</span></h3>
      <dl class="kv">${rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>
      <h3 style="margin-top:14px">Why: input × weight for ${esc(f.flag)}</h3>
      <div class="table-wrap"><table class="table"><thead><tr><th>Input</th><th class="right">Value</th><th class="right">Effect on score</th></tr></thead>
      <tbody><tr><td>Bias w₀ (includes "yesterday was Green", the reference)</td><td class="right">1</td><td class="right num">${f.bias >= 0 ? "+" : ""}${f.bias.toFixed(3)}</td></tr>${contrib}</tbody></table></div>`;
    $("#model-info").innerHTML = `
      <p>Linear regression fitted by least squares: for each flag colour <i>k</i>, solve the normal equations
        <code class="f">XᵀX w<sub>k</sub> = Xᵀy<sub>k</sub></code>, where <i>y<sub>k</sub></i> is 1 on days with that flag. The predicted flag is the colour with the highest score <code class="f">w<sub>k</sub>·x</code>.</p>
      <p>Inputs <i>x</i>: the 07:00 weather (temperature, rain, wind speed/gusts/direction, visibility, CAPE, weather code) and yesterday's flag, one-hot encoded.</p>
      <dl class="kv">
        <dt>Training days</dt><dd>${m.train_rows} (from ${esc(m.train_from)})</dd>
        <dt>Test days</dt><dd>${m.test_rows} (${esc(m.test_from)} to ${esc(m.test_to)})</dd>
        <dt>Exact flag right</dt><dd><b>${(m.accuracy * 100).toFixed(1)}%</b></dd>
        <dt>Rowable or not right</dt><dd><b>${(m.rowable_accuracy * 100).toFixed(1)}%</b></dd>
        <dt>"Same as yesterday"</dt><dd>${(m.baseline_yesterday * 100).toFixed(1)}%</dd>
        <dt>"Always ${esc(m.majority)}"</dt><dd>${(m.baseline_majority * 100).toFixed(1)}%</dd></dl>
      <p class="small muted" style="margin-top:10px">The model's rowable share sets the house market maker's opening fair price, so trading starts at the model's view and traders move it from there.</p>
      <p class="tiny faint">Flags: ${Object.entries(data.flags).map(([k, v]) => `${sw(k)}<b>${esc(k)}</b> ${esc(v)}`).join(" · ")}</p>`;
  }

  async function loadChart() {
    const c = data.current;
    if (!c) return;
    if (!chart) chart = lineChart($("#isis-chart"), { legend: true });
    const since = isoNoMs(Date.parse(c.closes_at) - 2 * 86400e3);
    const d = await api.get(`/api/markets/${c.market_id}/history?bucket=5m&since=${encodeURIComponent(since)}`);
    const pts = (d.points || []).filter((p) => p.close != null);
    setChartData(chart, pts.map((p) => timeLabel(p.t, "5m")), [
      { label: "Market chance", color: "#0b2a66", fill: true, data: pts.map((p) => p.close) },
      { label: "Model", color: "#f0a020", data: pts.map(() => c.ai_prior) },
    ]);
  }

  $("#summary-refresh").addEventListener("click", async () => {
    const msg = $("#summary-msg");
    if (!App.user) { msg.innerHTML = `<a href="${loginHref()}">Log in</a> to refresh the summary.`; return; }
    msg.textContent = "Asking Gemini…";
    try {
      const d = await api.post("/api/isis/summary/refresh");
      data.summary = d.summary; renderSummary(); msg.textContent = "";
    } catch (e) { msg.textContent = errText(e); }
  });
})();
