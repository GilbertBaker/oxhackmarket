// Frontend JavaScript: talks to the Python backend using fetch().
const status = document.querySelector("#backend-status");
const countdown = document.querySelector("#countdown");
const official = document.querySelector("#official");
const days = document.querySelector("#days");
const explanationBox = document.querySelector("#explanation-box");
const explanationSource = document.querySelector("#explanation-source");
const explanation = document.querySelector("#explanation");
const freshness = document.querySelector("#freshness");
const skillPanel = document.querySelector("#skill-panel");
const skillNote = document.querySelector("#skill-note");
const skillRows = document.querySelector("#skill-rows");

const percent = (x) => `${Math.round(x * 100)}%`;
const flagClass = (flag) => `chip flag-${flag.toLowerCase().replace(" ", "-")}`;
const formatTime = (iso) => new Date(iso).toLocaleString("en-GB", {
  weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
});

async function checkBackend() {
  try {
    const response = await fetch("/api/health");
    if (!response.ok) throw new Error("Health check failed");
    status.textContent = "● Python connected";
    status.className = "status online";
  } catch {
    status.textContent = "● Backend unavailable";
    status.className = "status offline";
  }
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function renderDays(forecast) {
  days.replaceChildren(...forecast.days.map((day) => {
    const item = element("li", "day");
    item.append(
      element("span", "day-name", day.day_name),
      element("span", "day-date", new Date(day.target_date).toLocaleDateString("en-GB", { day: "numeric", month: "short" })),
      element("span", flagClass(day.flag), day.flag),
      element("span", "day-chance", `${percent(day.p_green)} chance of green`),
      element("span", "day-method", day.method === "model" ? "model" : "copy of today's flag"),
    );
    item.setAttribute("aria-label",
      `${day.day_name}: ${day.flag} forecast, ${percent(day.p_green)} chance of green`);
    return item;
  }));
}

function renderSkill(evaluation) {
  if (!evaluation) return;
  skillPanel.hidden = false;
  skillNote.textContent = `Tested on every day from ${evaluation.tested_from} to ${evaluation.tested_to}, ` +
    "always training only on earlier days. Accuracy is the share of days with exactly the right flag colour; " +
    "copying today's flag is the bar to beat.";
  skillRows.replaceChildren(...evaluation.horizons.map((h) => {
    const row = element("tr");
    const better = h.model.exact >= h.copy_today.exact;
    row.append(
      element("th", "", h.horizon_days),
      element("td", better ? "" : "best", percent(h.copy_today.exact)),
      element("td", better ? "best" : "", percent(h.model.exact)),
      element("td", "", `${Math.round(h.model.change_days_right)} of ${Math.round(h.model.change_days)}`),
    );
    row.firstChild.setAttribute("scope", "row");
    return row;
  }));
}

async function loadForecast() {
  try {
    const response = await fetch("/api/forecast");
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Request failed");
    const forecast = data.forecast;
    if (!forecast) {
      countdown.textContent = data.updating ? "Building the first forecast… (about a minute)" : "No forecast yet.";
      if (data.last_error) official.textContent = `Last error: ${data.last_error}`;
      setTimeout(loadForecast, 15000);
      return;
    }
    countdown.textContent = forecast.countdown;
    official.textContent = `Official OURCs flag at ${formatTime(forecast.issued_at)}: ${forecast.official_flag_at_cutoff}`;
    renderDays(forecast);

    explanationBox.hidden = false;
    explanationSource.textContent = forecast.explanation.source === "gemini" ? "WHY · EXPLAINED BY GEMINI" : "WHY";
    explanation.textContent = forecast.explanation.text;

    const stale = forecast.freshness.stale_inputs;
    freshness.textContent = `Forecast made with data up to ${formatTime(forecast.issued_at)}` +
      (stale.length ? ` · Stale inputs: ${stale.join(", ")}` : "") +
      (data.updating ? " · Updating…" : "") +
      (data.last_error ? ` · Last update failed: ${data.last_error}` : "");
    renderSkill(forecast.evaluation);
  } catch (error) {
    countdown.textContent = `No forecast: ${error.message}`;
  }
}

checkBackend();
loadForecast();
