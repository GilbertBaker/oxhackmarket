"""Issue the live 1-5 day flag forecast: refresh data, run the saved models, explain, store.

The forecast uses the 07:00 cut-off of the latest day (today after 07:00,
otherwise yesterday), exactly like the training data. For days missing from
hectorcsv1.csv, the 07:00 flag comes from the team's ourcs_flag_history table
in Tiger Data plus the live OURCs flag API.

At each horizon, the forecast uses whichever of the model and "copy today's flag"
scored better in the backtest (model/evaluation.json, from experiments.py).

Output: model/latest_forecast.json, and a row per horizon in Tiger Data's
flag_forecasts table. Run: py forecast.py [--no-fetch]
"""

from datetime import datetime
from pathlib import Path
import json
import sys

import numpy as np
import pandas as pd

import explain
import fetch_data
import tigerdb
from features import CLASSES, CUTOFF_HOUR, FLAGS, HORIZONS, TZ, build_daily, load_data
from neuralnet import FEATURES, load_ensemble

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))
from conditions import fetch_flag  # noqa: E402  (the team's live OURCs flag fetcher)

FORECAST_FILE = ROOT / "model" / "latest_forecast.json"
EVALUATION_FILE = ROOT / "model" / "evaluation.json"
STALE_HOURS = 6  # Warn when an input's latest reading is this much older than the cut-off.


def issue_day(now=None):
    """The day whose 07:00 cut-off the forecast uses."""
    now = now or pd.Timestamp.now(tz=TZ)
    day = now.tz_localize(None).normalize()
    return day if now.hour >= CUTOFF_HOUR else day - pd.Timedelta(days=1)


def flag_periods():
    """(status, start) flag periods: Tiger Data history, then the live OURCs flag if newer."""
    periods = []
    if tigerdb.configured():
        history = tigerdb.load_flag_history()
        periods = list(zip(history["status"], history["start"]))
    try:
        live = fetch_flag()
        start = pd.Timestamp(live["set_at"])
        if not periods or start > periods[-1][1]:
            periods.append((live["status"], start))
    except Exception as error:
        print(f"Live OURCs flag unavailable ({type(error).__name__}: {error})")
    return periods


def extend_flags(flags, day):
    """Add rows up to `day` (07:00 flag only) for days missing from hectorcsv1.csv."""
    missing = pd.date_range(flags.index.max() + pd.Timedelta(days=1), day, freq="D")
    if missing.empty:
        return flags.loc[:day], "hectorcsv1.csv"
    periods = flag_periods()
    rows = {}
    for d in missing:
        cutoff = (d + pd.Timedelta(hours=CUTOFF_HOUR)).tz_localize(TZ)
        in_force = [status for status, start in periods if start <= cutoff]
        if not in_force or in_force[-1] not in FLAGS:
            raise ValueError(f"No known OURCs flag at 07:00 on {d.date()}")
        rows[d] = in_force[-1]
    extra = pd.DataFrame({"ourcs_isis_flag_at_0700": pd.Series(rows)})
    return pd.concat([flags, extra]), "OURCs flag history (Tiger Data) + live OURCs API"


def freshness(ea, fc, cutoff):
    latest = {col: ea[col].dropna().index.max() for col in ea.columns}
    stale = [col for col, t in latest.items() if t < cutoff - pd.Timedelta(hours=STALE_HOURS)]
    return {
        "latest_readings": {col: t.isoformat() for col, t in latest.items()},
        "stale_inputs": stale,
        "rain_forecast_until": fc.index.max().isoformat(),
    }


def use_model_at(evaluation):
    """Horizon -> True if the model beat copying today's flag in the backtest."""
    if not evaluation:
        return {h: True for h in range(1, HORIZONS + 1)}
    return {r["horizon_days"]: r["model"]["exact"] >= r["copy_today"]["exact"] for r in evaluation["horizons"]}


def countdown(today_flag, days):
    if today_flag == "Green":
        for d in days:
            if d["flag"] != "Green":
                return f"Green expected until {d['day_name']}, when {d['flag']} is forecast"
        return f"Green expected to hold for at least {len(days)} days"
    for d in days:
        if d["flag"] == "Green":
            n = d["horizon_days"]
            return f"Green likely in {n} day{'s' if n > 1 else ''} ({d['day_name']})"
    return f"No green flag expected in the next {len(days)} days"


def run_forecast(fetch=True):
    if fetch:
        fetch_data.main()
    flags, ea, fc = load_data()
    day = issue_day()
    flags, flag_source = extend_flags(flags, day)
    cutoff = (day + pd.Timedelta(hours=CUTOFF_HOUR)).tz_localize(TZ)
    daily = build_daily(flags, ea[ea.index <= cutoff], fc).loc[[day]]
    inputs = daily[FEATURES]
    if inputs.isna().any(axis=1).iloc[0]:
        raise ValueError(f"Missing inputs at {cutoff}: {list(inputs.columns[inputs.isna().iloc[0]])}")

    probs = load_ensemble()(inputs.to_numpy("float32"))[0]  # (HORIZONS, classes)
    evaluation = json.loads(EVALUATION_FILE.read_text()) if EVALUATION_FILE.is_file() else None
    use_model = use_model_at(evaluation)
    today_flag = flags.loc[day, "ourcs_isis_flag_at_0700"]
    copy_flag = "Green" if today_flag == "Grey" else today_flag

    days = []
    for h in range(1, HORIZONS + 1):
        target = day + pd.Timedelta(days=h)
        p = {c: round(float(probs[h - 1, i]), 3) for i, c in enumerate(CLASSES)}
        model_flag = CLASSES[int(probs[h - 1].argmax())]
        days.append({
            "horizon_days": h,
            "target_date": str(target.date()),
            "day_name": target.strftime("%A"),
            "flag": model_flag if use_model[h] else copy_flag,
            "method": "model" if use_model[h] else "copy today's flag",
            "model_flag": model_flag,
            "p_green": p["Green"],
            "probabilities": p,
        })

    row = daily.iloc[0]
    facts = {
        "forecast_made_at": cutoff.isoformat(),
        "official_flag_at_cutoff": today_flag,
        "river": {
            "farmoor_flow_m3s": round(float(np.expm1(row["farmoor_flow_log"])), 1),
            "farmoor_flow_change_24h_pct": round(float(np.expm1(row["farmoor_flow_change_24h"]) * 100), 1),
            "farmoor_flow_change_72h_pct": round(float(np.expm1(row["farmoor_flow_change_72h"]) * 100), 1),
            "cherwell_flow_m3s": round(float(np.expm1(row["cherwell_flow_log"])), 1),
            "note": "Flags are green below about 20 m3/s at Farmoor and almost never green above 35.",
        },
        "rain": {
            "observed_last_3_days_mm": round(float(row["rain_obs_3D"]), 1),
            "forecast_by_day_mm": [round(float(row[f"rain_fc_day{j}"]), 1) for j in range(HORIZONS)],
            "forecast_next_5_days_mm": round(float(sum(row[f"rain_fc_day{j}"] for j in range(HORIZONS))), 1),
        },
        "forecast": [{"day": d["day_name"], "date": d["target_date"], "flag": d["flag"],
                      "chance_green": d["p_green"]} for d in days],
        "backtest_green_or_not_accuracy_by_days_ahead": {
            r["horizon_days"]: round(max(r["model"]["green_or_not"], r["copy_today"]["green_or_not"]), 3)
            for r in evaluation["horizons"]} if evaluation else None,
    }
    text, source = explain.explain(facts)

    result = {
        "issued_at": cutoff.isoformat(),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "official_flag_at_cutoff": today_flag,
        "official_flag_source": flag_source,
        "countdown": countdown(today_flag, days),
        "days": days,
        "explanation": {"text": text, "source": source},
        "facts": facts,
        "freshness": freshness(ea, fc, cutoff),
        "evaluation": evaluation,
    }
    FORECAST_FILE.parent.mkdir(exist_ok=True)
    FORECAST_FILE.write_text(json.dumps(result, indent=2))
    if tigerdb.configured():
        tigerdb.save_forecast(cutoff.to_pydatetime(), days)
    return result


def main():
    result = run_forecast(fetch="--no-fetch" not in sys.argv)
    print(f"Forecast issued at {result['issued_at']} (official flag then: {result['official_flag_at_cutoff']})")
    print(result["countdown"])
    for d in result["days"]:
        print(f"  {d['day_name']:<9} {d['target_date']}  {d['flag']:<10}  P(green) {d['p_green']:.2f}  [{d['method']}]")
    print(f"\n{result['explanation']['text']}  [{result['explanation']['source']}]")
    if result["freshness"]["stale_inputs"]:
        print(f"Stale inputs: {result['freshness']['stale_inputs']}")


if __name__ == "__main__":
    main()
