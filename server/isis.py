"""Isis rowing: a daily "Can you row on the Isis tomorrow?" market, like Polymarket's
rolling up/down markets. One binary market per day, resolved from the official OURCs Isis
flag at 07:00 UK time. The house market maker's fair price (ai_prior) comes from the linear
regression flag model in "isis predictor/model.py" (least squares, one-vs-rest, trained on
07:00 weather + yesterday's flag).

    python server/isis.py --backfill    7 past days (resolved) + tomorrow's market, with
                                        synthetic price history. Run with the app stopped.

In the app: GET /api/isis (dashboard data), POST /api/isis/summary/refresh (Gemini), and a
background job that resolves finished days and opens the next one.
"""
import csv
import importlib.util
import json
import logging
import math
import os
import random
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

SERVER_DIR = Path(__file__).resolve().parent
ROOT = SERVER_DIR.parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from flask import Blueprint, jsonify  # noqa: E402

import ai  # noqa: E402
import db  # noqa: E402

log = logging.getLogger("isis")
bp = Blueprint("isis", __name__)

LONDON = ZoneInfo("Europe/London")
CATEGORY = "isis"
ROWABLE = ("Green", "Light Blue", "Dark Blue")
FLAG_INFO = {
    "Green": "No restrictions: all crews can boat.",
    "Light Blue": "Novice coxes restricted; otherwise crews can boat.",
    "Dark Blue": "No novice crews; senior crews can boat.",
    "Amber": "Only senior crews with experienced coxes, with restrictions.",
    "Red": "No crews may boat.",
    "Black": "River closed to all boats.",
    "Grey": "Flag not monitored (outside term).",
}
RULES = ("Resolves YES if the official OURCs Isis flag at 07:00 UK time on {day} is Green, Light Blue "
         "or Dark Blue (college crews can go out). Amber, Red, Black or Grey resolve NO. "
         "Source: ourcs.co.uk/api/flags/status/isis/.")
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
FLAG_URL = "https://ourcs.co.uk/api/flags/status/isis/"
LAT, LON = 51.7437, -1.2466
FIELDS = {"temperature_2m": "temperature_2m_C", "precipitation": "precipitation_mm_prev_hour",
          "weather_code": "weather_code_WMO", "wind_speed_10m": "wind_speed_10m_kmh",
          "wind_direction_10m": "wind_direction_10m_deg", "wind_gusts_10m": "wind_gusts_10m_kmh",
          "visibility": "visibility_m", "cape": "cape_J_per_kg"}
LIVE_TTL = 1800
SUMMARY_MAX_AGE = timedelta(hours=3)
SUMMARY_COOLDOWN = timedelta(minutes=10)
TICK_EVERY_S = 600


# ---------------------------------------------------------------- the regression model

def _load_model_module():
    path = ROOT / "isis predictor" / "model.py"
    spec = importlib.util.spec_from_file_location("isis_model", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load_model_module()
_model = None
_model_lock = threading.Lock()


def model():
    """Train once (pure Python, ~1 s) and keep the weights plus the test-split metrics."""
    global _model
    with _model_lock:
        if _model is None:
            weights, codes, flags, rows, split = M.train()
            test = rows[split:]
            pred = [M.predict_flag(weights, codes, flags, r)[0] for r in test]
            actual = [r["ourcs_isis_flag_at_0700"] for r in test]
            majority = max(weights, key=lambda k: sum(r["ourcs_isis_flag_at_0700"] == k for r in rows[:split]))
            n = len(test)
            _model = {
                "weights": weights, "codes": codes, "flags": flags,
                "metrics": {
                    "train_rows": split, "test_rows": n,
                    "train_from": rows[0]["date"], "test_from": test[0]["date"], "test_to": test[-1]["date"],
                    "accuracy": sum(a == p for a, p in zip(actual, pred)) / n,
                    "rowable_accuracy": sum((a in ROWABLE) == (p in ROWABLE) for a, p in zip(actual, pred)) / n,
                    "baseline_yesterday": sum(r["prev_flag"] == a for r, a in zip(test, actual)) / n,
                    "baseline_majority": sum(a == majority for a in actual) / n, "majority": majority,
                },
            }
            log.info("isis model trained: %d rows, test accuracy %.1f%%", split, 100 * _model["metrics"]["accuracy"])
        return _model


_csv_cache = None


def csv_rows():
    """{date iso: row} from the training CSV (07:00 weather + official flag)."""
    global _csv_cache
    if _csv_cache is None:
        with open(M.DATA, newline="", encoding="utf-8-sig") as f:
            _csv_cache = {r["date"]: r for r in csv.DictReader(f)}
    return _csv_cache


def _medians():
    out = {}
    rows = list(csv_rows().values())
    for key in FIELDS.values():
        vals = sorted(float(r[key]) for r in rows if r.get(key) not in (None, ""))
        out[key] = vals[len(vals) // 2] if vals else 0.0
    return out


def weather_text(code):
    for r in csv_rows().values():
        if str(int(float(r["weather_code_WMO"]))) == str(int(float(code))):
            return r["weather_description"]
    return f"WMO code {code}"


def predict(weather, prev_flag):
    """Model output for one 07:00 reading. Scores are least-squares outputs, clipped at 0 and
    normalised to sum to 1: a ranking, not a calibrated probability."""
    m = model()
    med = _medians()
    w = {k: (weather.get(k) if weather.get(k) not in (None, "") else med[k]) for k in FIELDS.values()}
    w["prev_flag"] = prev_flag or "Green"
    flag, scores = M.predict_flag(m["weights"], m["codes"], m["flags"], w)
    pos = {k: max(v, 0.0) for k, v in scores.items()}
    total = sum(pos.values()) or 1.0
    shares = {k: pos[k] / total for k in M.COLOURS if k in pos}
    names = M.feature_names(m["codes"], m["flags"])
    x = M.features(w, m["codes"], m["flags"])
    contrib = [{"feature": n, "value": round(xj, 3), "effect": round(xj * wj, 4)}
               for n, xj, wj in zip(names, x, m["weights"][flag]) if n != "bias" and xj != 0]
    contrib.sort(key=lambda c: -abs(c["effect"]))
    p_row = sum(v for k, v in shares.items() if k in ROWABLE)
    return {"flag": flag, "rowable": flag in ROWABLE, "p_row": round(p_row, 3),
            "scores": {k: round(v, 3) for k, v in scores.items()},
            "shares": {k: round(v, 3) for k, v in shares.items()},
            "contributions": contrib[:6], "bias": round(m["weights"][flag][0], 4),
            "inputs": {**{k: (float(v) if v is not None else None) for k, v in w.items() if k != "prev_flag"},
                       "weather": weather_text(w["weather_code_WMO"]), "prev_flag": w["prev_flag"]}}


def prior_pence(p_row):
    return int(min(max(round(p_row * 100), 3), 97))


# ---------------------------------------------------------------- live data

_live = {"at": 0.0, "weather": {}, "flag": None, "error": None}
_live_lock = threading.Lock()


def _fetch_json(url):
    with urlopen(Request(url, headers={"User-Agent": "OxMarket Isis"}), timeout=20) as r:
        return json.load(r)


def live(force=False):
    """Open-Meteo 07:00 weather (last 8 days + tomorrow) and the current OURCs flag, cached."""
    with _live_lock:
        if not force and time.time() - _live["at"] < LIVE_TTL and _live["weather"]:
            return _live
        try:
            q = urlencode({"latitude": LAT, "longitude": LON, "hourly": ",".join(FIELDS),
                           "models": "ukmo_seamless", "timezone": "Europe/London",
                           "past_days": 8, "forecast_days": 3})
            data = _fetch_json(f"{WEATHER_URL}?{q}")
            h = data["hourly"]
            _live["weather"] = {stamp[:10]: {key: h[f][i] for f, key in FIELDS.items()}
                                for i, stamp in enumerate(h["time"]) if stamp.endswith("T07:00")}
            f = _fetch_json(FLAG_URL)
            _live["flag"] = {"flag": f["status_text"], "notices": f.get("notices", []),
                             "set_at": f.get("set_date")}
            _live["error"] = None
        except Exception as e:
            log.warning("isis live data failed: %s", e)
            _live["error"] = f"{type(e).__name__}: {e}"
        _live["at"] = time.time()
        return _live


def now_london():
    return datetime.now(LONDON)


def weather_0700(d):
    row = csv_rows().get(d.isoformat())
    if row:
        return {k: row[k] for k in FIELDS.values()}, "observed (training data)"
    w = live()["weather"].get(d.isoformat())
    if w:
        return w, ("forecast" if d > now_london().date() else "observed") + " (Met Office via Open-Meteo)"
    return {}, "missing"


def flag_on(d):
    """Official 07:00 flag on day d, or None if not known yet."""
    row = csv_rows().get(d.isoformat())
    if row:
        return row["ourcs_isis_flag_at_0700"]
    now = now_london()
    if d == now.date() and now.hour >= 7:
        f = live()["flag"]
        return f["flag"] if f else None
    if d < now.date():   # past day after the CSV ends: the flag has not changed since set_at
        f = live()["flag"]
        if f and f.get("set_at") and db.parse_iso(f["set_at"]).astimezone(LONDON).date() <= d:
            return f["flag"]
    return None


def latest_flag_before(d):
    for i in range(1, 30):
        f = flag_on(d - timedelta(days=i))
        if f:
            return f
    return "Green"


def forecast_for(d):
    """The model's call for the 07:00 flag on day d, using only d's 07:00 weather and the
    flag on the day before (no lookahead)."""
    weather, source = weather_0700(d)
    out = predict(weather, latest_flag_before(d))
    out["weather_source"] = source
    out["target_date"] = d.isoformat()
    return out


# ---------------------------------------------------------------- markets

def question(d):
    return f"Can you row on the Isis tomorrow? ({d:%a} {d.day} {d:%b})"


def closes_at(d):
    return datetime(d.year, d.month, d.day, 7, tzinfo=LONDON).astimezone(timezone.utc)


def opens_at(d):
    return closes_at(d - timedelta(days=1))


def target_of(race):
    return race["closes_at"].astimezone(LONDON).date()


def ensure_category():
    db._exec(f"""INSERT INTO {db.SCHEMA}.categories (slug, name, sort) VALUES ('isis', 'Isis rowing', 15)
                 ON CONFLICT (slug) DO NOTHING""")


def isis_races(statuses=None):
    q = f"SELECT * FROM {db.SCHEMA}.races WHERE category = %s"
    params = [CATEGORY]
    if statuses:
        q += " AND status = ANY(%s)"
        params.append(list(statuses))
    return db._all(q + " ORDER BY closes_at", params)


def ensure_market(d):
    """Create the market for day d if it doesn't exist. Returns (race, created)."""
    import trading
    race = db._one(f"SELECT * FROM {db.SCHEMA}.races WHERE category = %s AND position = %s", (CATEGORY, question(d)))
    if race:
        return race, False
    term = db.current_term()
    fc = forecast_for(d)
    day = f"{d:%A} {d.day} {d:%B %Y}"
    desc = (f"Will the OURCs Isis flag let college crews out at 07:00 on {day}? The house market maker "
            f"opens at the linear regression model's call: {fc['flag']} ({prior_pence(fc['p_row'])}% rowable).")
    rid = trading.create_race(term["name"] if term else "Michaelmas 2026", question(d), closes_at(d), RULES.format(day=day),
                              [{"name": "Yes", "prior": prior_pence(fc["p_row"])}], kind="binary",
                              category=CATEGORY, description=desc, source_name="OURCs",
                              source_url="https://ourcs.co.uk/", ai_reason=f"model: {fc['flag']}")
    log.info("isis market %s opened for %s (prior %s)", rid, d, prior_pence(fc["p_row"]))
    return db.get_race(rid), True


def resolve_due():
    """Resolve every finished day whose 07:00 flag is known. Returns how many."""
    import trading
    n = 0
    for race in isis_races(("open", "closed")):
        if race["closes_at"] > db.utcnow():
            continue
        d = target_of(race)
        flag = flag_on(d)
        if not flag:
            if race["status"] == "open":
                trading.close_race(race["id"])
            continue
        market = db.race_markets(race["id"])[0]
        trading.resolve_race(race["id"], [market["id"]] if flag in ROWABLE else [])
        db._exec(f"UPDATE {db.SCHEMA}.races SET resolved_at = %s, description = coalesce(description, '') || %s WHERE id = %s",
                 (race["closes_at"], f" Result: {flag} flag at 07:00.", race["id"]))
        db._exec(f"UPDATE {db.SCHEMA}.markets SET resolved_at = %s WHERE race_id = %s", (race["closes_at"], race["id"]))
        log.info("isis race %s resolved: %s flag -> %s", race["id"], flag, "YES" if flag in ROWABLE else "NO")
        n += 1
    return n


def tick():
    ensure_category()
    resolve_due()
    ensure_market(now_london().date() + timedelta(days=1))


def start_background():
    def loop():
        time.sleep(15)
        while True:
            try:
                tick()
            except Exception:
                log.exception("isis tick failed")
            time.sleep(TICK_EVERY_S)
    threading.Thread(target=loop, daemon=True, name="isis").start()
    log.info("isis job started (every %ds)", TICK_EVERY_S)


# ---------------------------------------------------------------- backfill (demo)

def _synth(race, start, end, p0, p1, house, rng):
    """10-minute ticks + occasional house self-trades from p0 to p1 (pence)."""
    import demo
    step = demo.STEP
    n = max(int((end - start) / step), 2)
    path = demo._walk(n, demo._logit(p0), demo._logit(p1), rng)
    mid = db.race_markets(race["id"])[0]["id"]
    ticks, trades = [], []
    for i, x in enumerate(path):
        t = start + step * i
        if t >= end:
            break
        p = int(min(max(round(100 / (1 + math.exp(-x))), 2), 98))
        ticks.append((t, mid, max(p - 1, 1), min(p + 1, 99), p))
        if rng.random() < 0.3:
            side = rng.choice(("buy", "sell"))
            trades.append((t + timedelta(seconds=rng.randint(0, 590)), mid, min(max(p + (1 if side == "buy" else -1), 1), 99),
                           rng.choice((5, 10, 20, 25, 50)), house, house, side))
    with db.pool().connection() as c, c.transaction():
        with c.cursor() as cur:
            with cur.copy(f"COPY {db.SCHEMA}.price_ticks (time, market_id, best_bid, best_ask, last_price) FROM STDIN") as cp:
                for r in ticks:
                    cp.write_row(r)
            with cur.copy(f"COPY {db.SCHEMA}.trades (time, market_id, price, qty, buyer_id, seller_id, taker_side) FROM STDIN") as cp:
                for r in trades:
                    cp.write_row(r)
            cur.execute(f"UPDATE {db.SCHEMA}.races SET created_at = %s WHERE id = %s", (start, race["id"]))
            cur.execute(f"UPDATE {db.SCHEMA}.markets SET created_at = %s WHERE race_id = %s", (start, race["id"]))
    return len(ticks), len(trades)


def backfill(days=7, seed=None):
    rng = random.Random(seed)
    ensure_category()
    house = db.ensure_bot_user()
    today = now_london().date()
    earliest = None
    for d in [today - timedelta(days=i) for i in range(days - 1, -1, -1)] + [today + timedelta(days=1)]:
        race, created = ensure_market(d)
        if not created:
            print(f"  exists   {question(d)}")
            continue
        fc = forecast_for(d)
        p0 = prior_pence(fc["p_row"])
        start, end = opens_at(d), min(closes_at(d), db.utcnow())
        flag = flag_on(d) if closes_at(d) <= db.utcnow() else None
        if flag:   # the price drifts towards the result as 07:00 approaches
            p1 = rng.randint(88, 96) if flag in ROWABLE else rng.randint(4, 12)
        else:
            p1 = p0
        nt, ntr = _synth(race, start, end, p0, p1, house, rng)
        earliest = min(earliest or start, start)
        print(f"  created  #{race['id']} {question(d)}: model {fc['flag']} ({p0}%), "
              f"actual {flag or 'pending'}; {nt} ticks, {ntr} trades")
    resolve_due()
    if earliest:
        db.refresh_candles(earliest)


# ---------------------------------------------------------------- Gemini summary

SUMMARY_PROMPT = """You write the summary box for a play-money prediction market used by Oxford
students: "{question}". Write 3 to 5 sentences of plain text (no markdown, no lists).

Background you can rely on:
- The Isis is the stretch of the River Thames in Oxford where college crews row. OURCs (Oxford
  University Rowing Clubs) sets an official Isis flag each morning. Flag meanings: {flag_info}
- The market resolves YES if the official flag at 07:00 UK time on {target} is Green, Light Blue or
  Dark Blue, and NO for Amber, Red, Black or Grey.
- The flag mostly follows river height and flow (stream). Heavy rain upstream raises the river over
  several days, so flags tend to persist: "same as yesterday" is a strong baseline.

Data (use only these facts; never invent river levels, rainfall totals or official decisions):
- Official flag now: {flag_now} (set {flag_set}). Notices: {notices}
- Official 07:00 flags over the last week (oldest first): {week}
- Weather at 07:00 on {target} ({weather_source}): {weather}
- Model: a linear regression (least squares, one-vs-rest per flag colour) on 07:00 weather and
  yesterday's flag, trained on {train_rows} days from {train_from}. On {test_rows} held-out days
  ({test_from} to {test_to}) it got the exact flag right {acc:.0%} of the time, against {base:.0%} for
  "same as yesterday", and rowable-or-not right {row_acc:.0%}. It does not see river flow directly.
- Model call for {target}: {model_flag}; normalised scores (not calibrated probabilities): {shares}.
  Largest input effects on that call: {contrib}
- Market price now: YES {price}p, i.e. traders give a {price}% chance that crews can row.

Say what the model expects and the main reason, how the market price compares with it, and one
thing that could change the outcome. End by noting this is a forecast, not official OURCs guidance.
"""


def _fmt_weather(inp):
    return (f"{inp['weather']}, {inp['temperature_2m_C']:.1f} C, {inp['precipitation_mm_prev_hour']:.1f} mm rain in the "
            f"previous hour, wind {inp['wind_speed_10m_kmh']:.0f} km/h gusting {inp['wind_gusts_10m_kmh']:.0f}, "
            f"visibility {inp['visibility_m'] / 1000:.1f} km")


def make_summary(state):
    cur, fc, mt = state["current"], state["forecast"], state["model"]
    official = state["official"] or {}
    week = ", ".join(f"{h['date']} {h['actual_flag'] or '?'}" for h in state["history"]) or "none"
    if not ai._fake():
        text = ai._generate("isis", SUMMARY_PROMPT.format(
            question=cur["question"], target=fc["target_date"],
            flag_info="; ".join(f"{k}: {v}" for k, v in FLAG_INFO.items()),
            flag_now=official.get("flag", "unknown"), flag_set=official.get("set_at", "unknown"),
            notices="; ".join(official.get("notices") or []) or "none", week=week,
            weather_source=fc["weather_source"], weather=_fmt_weather(fc["inputs"]),
            train_rows=mt["train_rows"], train_from=mt["train_from"], test_rows=mt["test_rows"],
            test_from=mt["test_from"], test_to=mt["test_to"], acc=mt["accuracy"], base=mt["baseline_yesterday"],
            row_acc=mt["rowable_accuracy"], model_flag=fc["flag"],
            shares=", ".join(f"{k} {v:.2f}" for k, v in fc["shares"].items() if v > 0),
            contrib="; ".join(f"{c['feature']} = {c['value']} ({c['effect']:+.3f})" for c in fc["contributions"][:4]),
            price=cur["price"]))
        if text:
            return text
    return (f"The model expects a {fc['flag']} flag at 07:00 on {fc['target_date']}, mostly because yesterday's flag "
            f"was {fc['inputs']['prev_flag']} and flags tend to persist. The market prices YES at {cur['price']}p "
            f"against the model's {prior_pence(fc['p_row'])}%. This is a forecast, not official OURCs guidance.")


# ---------------------------------------------------------------- API

def state():
    today = now_london().date()
    current = None
    for race in isis_races(("open",)):
        if target_of(race) > today:
            current = race
            break
    if current is None:
        open_ = isis_races(("open", "closed"))
        current = open_[-1] if open_ else None
    official = live()["flag"]
    out = {"official": official, "live_error": live()["error"], "flags": FLAG_INFO,
           "rowable": list(ROWABLE), "model": model()["metrics"], "current": None, "forecast": None,
           "history": [], "summary": None}
    if current:
        m = db.race_markets(current["id"])[0]
        d = target_of(current)
        out["current"] = {"race_id": current["id"], "market_id": m["id"], "question": current["position"],
                          "target_date": d.isoformat(), "closes_at": db.iso(current["closes_at"]),
                          "status": current["status"], "price": int(m["price"]), "ai_prior": m.get("ai_prior")}
        out["forecast"] = forecast_for(d)
        b = db.latest_brief(current["id"], "isis")
        out["summary"] = {"text": b["text"], "created_at": db.iso(b["created_at"])} if b else None
    for race in reversed(isis_races(("resolved", "closed"))[-7:]):
        d = target_of(race)
        m = db.race_markets(race["id"])[0]
        fc = forecast_for(d)
        actual = flag_on(d)
        out["history"].append({"date": d.isoformat(), "race_id": race["id"], "market_id": m["id"],
                               "status": race["status"], "outcome": m.get("outcome"),
                               "actual_flag": actual, "model_flag": fc["flag"],
                               "model_p_row": fc["p_row"], "final_price": int(m["price"]),
                               "model_right": (actual in ROWABLE) == fc["rowable"] if actual else None})
    out["history"].sort(key=lambda h: h["date"])
    return out


def _refresh_summary(s):
    cur = s["current"]
    db.save_brief({"race_id": cur["race_id"], "kind": "isis", "text": make_summary(s)})
    b = db.latest_brief(cur["race_id"], "isis")
    s["summary"] = {"text": b["text"], "created_at": db.iso(b["created_at"])}


@bp.get("/api/isis")
def api_isis():
    s = state()
    if s["current"]:
        b = s["summary"]
        if b is None or db.parse_iso(b["created_at"]) < db.utcnow() - SUMMARY_MAX_AGE:
            _refresh_summary(s)
    return jsonify(s)


@bp.post("/api/isis/summary/refresh")
def api_isis_refresh():
    from data_api import current_user
    if not current_user():
        return jsonify(error="login required"), 401
    s = state()
    if not s["current"]:
        return jsonify(error="no open Isis market"), 404
    b = s["summary"]
    if b and db.parse_iso(b["created_at"]) > db.utcnow() - SUMMARY_COOLDOWN:
        return jsonify(error="The summary was refreshed in the last 10 minutes. Try again later."), 400
    _refresh_summary(s)
    return jsonify(summary=s["summary"])


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    logging.basicConfig(level=logging.INFO)
    if "--backfill" in sys.argv:
        db.ensure_schema()
        backfill()
    else:
        print(json.dumps(state(), indent=1, default=str)[:4000])
