"""Download free river and rain data used by the models into data/.

Sources (no API key needed):
- Environment Agency Hydrology API: 15-minute river levels/flows and rain gauge.
  https://environment.data.gov.uk/hydrology/doc/reference
- Environment Agency real-time flood-monitoring API: same gauges, but flows are
  published there immediately (the hydrology API lags ~9 days for flows).
  https://environment.data.gov.uk/flood-monitoring/doc/reference
- Open-Meteo Previous Runs API: archived rain forecasts as issued 1-5 days ahead.
  https://open-meteo.com/en/docs/previous-runs-api

Also uploads everything to Tiger Data (TimescaleDB) when DATABASE_URL is set in .env.

Run: py fetch_data.py   (re-run to refresh; takes under a minute)
"""

from datetime import date
from io import StringIO
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
import json

import pandas as pd

import tigerdb

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
START = date(2023, 12, 1)  # A month before hectorcsv1.csv starts, for rolling features.

EA_MEASURES = "https://environment.data.gov.uk/hydrology/id/measures/{}/readings.csv"
# name -> EA hydrology measure id (15-minute readings).
EA_SERIES = {
    "osney_level_m": "5086b349-e04b-4f8e-b8f1-fa92b21078d0-level-i-900-m-qualified",
    "iffley_level_m": "ab070726-7ac8-4037-a352-90552398d491-level-i-900-m-qualified",
    "farmoor_flow_m3s": "6b80bd1c-6c19-4c26-875d-108bf8ef1a84-flow-i-900-m3s-qualified",
    "cherwell_flow_m3s": "839866a2-80ca-42a8-a695-8e6d6b165fa6-flow-i-900-m3s-qualified",
    "osney_rain_mm": "5086b349-e04b-4f8e-b8f1-fa92b21078d0_256230TP-rainfall-t-900-mm-qualified",
}
EA_FILE = DATA_DIR / "ea_15min.csv"

FLOOD_READINGS = "https://environment.data.gov.uk/flood-monitoring/id/measures/{}/readings"
# Real-time measures for the same gauges, used to fill the hydrology API's lag.
FLOOD_SERIES = {
    "farmoor_flow_m3s": "1100TH-flow--Mean-15_min-m3_s",
    "cherwell_flow_m3s": "1490TH-flow--Mean-15_min-m3_s",
}

OPEN_METEO = "https://previous-runs-api.open-meteo.com/v1/forecast"
OXFORD = {"latitude": 51.75, "longitude": -1.26}
FORECAST_FILE = DATA_DIR / "rain_forecast_hourly.csv"
FORECAST_LEADS = range(1, 6)  # Forecasts issued 1..5 days before the hour they cover.


def fetch_ea_series(measure, start, end):
    """All 15-minute readings for one measure, one year per request. Times are UTC."""
    frames = []
    for year in range(start.year, end.year + 1):
        lo, hi = max(start, date(year, 1, 1)), min(end, date(year, 12, 31))
        query = urlencode({"mineq-date": lo.isoformat(), "maxeq-date": hi.isoformat(), "_limit": 200000})
        with urlopen(f"{EA_MEASURES.format(measure)}?{query}", timeout=120) as r:
            frames.append(pd.read_csv(StringIO(r.read().decode("utf-8")), usecols=["dateTime", "value"]))
    df = pd.concat(frames)
    df["dateTime"] = pd.to_datetime(df["dateTime"], utc=True)
    return df.drop_duplicates("dateTime").set_index("dateTime")["value"]


def fetch_flood_series(measure, since):
    """Real-time 15-minute readings since a UTC timestamp (the API keeps ~4 weeks)."""
    query = urlencode({"since": since.strftime("%Y-%m-%dT%H:%M:%SZ"), "_sorted": "", "_limit": 10000})
    with urlopen(f"{FLOOD_READINGS.format(measure)}?{query}", timeout=120) as r:
        items = json.load(r)["items"]
    s = pd.Series({pd.Timestamp(i["dateTime"]): i["value"] for i in items}, dtype="float64")
    return s.sort_index()


def fetch_forecasts(start, end):
    """Hourly rain forecasts (Europe/London time) issued 1..5 days before each hour."""
    query = urlencode({
        **OXFORD,
        "hourly": ",".join(f"precipitation_previous_day{lead}" for lead in FORECAST_LEADS),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "timezone": "Europe/London",
    })
    with urlopen(f"{OPEN_METEO}?{query}", timeout=120) as r:
        hourly = json.load(r)["hourly"]
    return pd.DataFrame(hourly).rename(columns={
        f"precipitation_previous_day{lead}": f"rain_fc_{lead}d_mm" for lead in FORECAST_LEADS})


def main():
    DATA_DIR.mkdir(exist_ok=True)
    end = date.today()

    series = {}
    for name, measure in EA_SERIES.items():
        series[name] = fetch_ea_series(measure, START, end)
        if name in FLOOD_SERIES:
            # Overlap a week so the two sources can be compared, then fill the gap.
            last = series[name].index.max()
            recent = fetch_flood_series(FLOOD_SERIES[name], last - pd.Timedelta(days=7))
            overlap = recent.index.intersection(series[name].index)
            max_diff = (recent[overlap] - series[name][overlap]).abs().max()
            print(f"{name}: real-time vs hydrology max difference over {len(overlap)} overlapping readings: {max_diff:.3f}")
            series[name] = pd.concat([series[name], recent[recent.index > last]])
        print(f"{name}: {len(series[name])} readings, last {series[name].index.max()}")
    ea = pd.DataFrame(series).sort_index()
    ea.index.name = "time_utc"
    ea.to_csv(EA_FILE)

    # Forecasts extend past today so the coming days' forecasts are included.
    fc = fetch_forecasts(START, end + pd.Timedelta(days=max(FORECAST_LEADS) + 1))
    fc.to_csv(FORECAST_FILE, index=False)
    print(f"rain forecasts: {len(fc)} hours, {fc['rain_fc_1d_mm'].isna().mean():.1%} missing")
    print(f"Saved {EA_FILE.relative_to(ROOT)} and {FORECAST_FILE.relative_to(ROOT)}")

    if tigerdb.configured():
        tigerdb.upload(ea, fc)
        print("Uploaded to Tiger Data")


if __name__ == "__main__":
    main()
