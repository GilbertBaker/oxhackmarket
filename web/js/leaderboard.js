// Leaderboard: ranked equity table plus the "Hindsight" equity-over-time chart.
(function () {
  "use strict";
  const { $, esc, pence, signedPence, poll, initNav, errText, PALETTE, lineChart, setChartData, dateTime } = App;
  const START = 100000;
  let chart = null;
  initNav("leaderboard").then(() => {
    poll(loadLeaders, 10000);
    poll(loadHistory, 60000);
  });

  async function loadLeaders() {
    let d;
    try { d = await api.get("/api/leaderboard"); } catch (e) {
      $("#leaders").innerHTML = `<tr><td colspan="4" class="muted">Couldn't load the leaderboard: ${esc(errText(e))}</td></tr>`;
      throw e;
    }
    const me = App.user && App.user.username;
    const rows = d.leaders || [];
    $("#leaders").innerHTML = rows.length ? rows.map((l) => {
      const pnl = l.equity - START;
      return `<tr class="${l.username === me ? "me-row" : ""}"><td><span class="rank${l.rank <= 3 ? " r" + l.rank : ""}">${l.rank}</span></td>
        <td><b>${esc(l.username)}</b>${l.username === me ? ' <span class="fav-tag">You</span>' : ""}</td>
        <td class="right num">${pence(l.equity)}</td>
        <td class="right num ${pnl > 0 ? "up" : pnl < 0 ? "down" : ""}">${pnl > 0 ? "▲ " : pnl < 0 ? "▼ " : ""}${signedPence(pnl)}</td></tr>`;
    }).join("") : `<tr><td colspan="4" class="muted">No traders yet.</td></tr>`;
  }

  async function loadHistory() {
    const d = await api.get("/api/leaderboard/history?bucket=1h&top=5");
    const series = d.series || [];
    if (!chart) chart = lineChart($("#lb-chart"), { legend: true, money: true });
    const times = Array.from(new Set(series.flatMap((s) => s.points.map((p) => p.t)))).sort();
    setChartData(chart, times.map(dateTime), series.map((s, i) => {
      const byT = new Map(s.points.map((p) => [p.t, p.equity]));
      return { label: s.username, color: PALETTE[i % PALETTE.length], data: times.map((t) => (byT.has(t) ? byT.get(t) : null)) };
    }));
  }
})();
