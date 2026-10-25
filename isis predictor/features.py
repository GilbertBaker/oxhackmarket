"""Daily features for forecasting the OURCs Isis flag 1-5 days ahead, shared by all models.

Prediction setup: at 07:00 on day D (once that morning's flag is posted) predict
the 07:00 flag on days D+1 ... D+HORIZONS (one of CLASSES; Grey days are excluded
as targets). Every feature for day D uses only data available at 07:00 on day D:
- flags: day D's 07:00 flag and earlier (hectorcsv1.csv)
- weather: day D's 07:00 reading (hectorcsv1.csv)
- river flows/levels and rain gauge: EA readings up to 07:00 on day D
- rain forecasts: for each 24h window from D+j 07:00 (j = 0..4), the forecast
  issued j+1 days before each hour, so every one was issued before D 07:00.
check_no_lookahead() verifies the data cut-offs by recomputing features with
everything after the cut-off deleted.

Run fetch_data.py first to download data/ (and upload it to Tiger Data).
"""

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
FLAGS_FILE = ROOT / "hectorcsv1.csv"
EA_FILE = ROOT / "data" / "ea_15min.csv"
FORECAST_FILE = ROOT / "data" / "rain_forecast_hourly.csv"

TZ = "Europe/London"
CUTOFF_HOUR = 7
HORIZONS = 5  # Forecast the flag 1..HORIZONS days ahead.
FLAGS = ["Green", "Light Blue", "Dark Blue", "Amber", "Red", "Black", "Grey"]
CLASSES = FLAGS[:-1]  # Flags a model predicts, in severity order (Grey = not monitored).
WEATHER = [
    "temperature_2m_C",
    "precipitation_mm_prev_hour",
    "wind_speed_10m_kmh",
    "wind_gusts_10m_kmh",
    "visibility_m",
    "cape_J_per_kg",
]
# Flag severity order (Grey = not monitored, handled by its own feature).
SEVERITY = {"Green": 0, "Light Blue": 1, "Dark Blue": 2, "Amber": 3, "Red": 4, "Black": 5, "Grey": 0}
FLOWS = ["farmoor_flow_m3s", "cherwell_flow_m3s"]
# River, rain and flag features only: hectorcsv1.csv's 07:00 weather didn't help in experiments.py.
RIVER_RAIN_FLAG = [
    *[f"{river}_{stat}" for river in ("farmoor_flow", "cherwell_flow") for stat in (
        "log", "change_6h", "change_24h", "change_72h", "max_24h", "mean_7d")],
    "osney_level_m", "osney_level_m_change_24h", "iffley_level_m", "iffley_level_m_change_24h",
    "rain_obs_24h", "rain_obs_3D", "rain_obs_7D", "rain_obs_14D",
    "rain_forecast_missing", *[f"rain_fc_day{j}" for j in range(HORIZONS)],
    *[f"today_{f}" for f in FLAGS], "today_severity", "days_flag_held_log",
]
# A compact feature set: with ~600 days and few flag changes, more inputs mainly add noise.
COMPACT = [
    "farmoor_flow_log", "farmoor_flow_change_24h", "farmoor_flow_change_72h", "farmoor_flow_max_24h",
    "cherwell_flow_log", "cherwell_flow_change_24h",
    "rain_obs_3D", "rain_obs_7D", "rain_fc_day0", "rain_fc_day1",
    "today_severity", "today_Green", "today_Grey", "days_flag_held_log",
]
LEVELS = ["osney_level_m", "iffley_level_m"]


def load_data(source="auto"):
    """Return (flags, ea, forecasts): daily flag/weather rows, 15-min EA readings, hourly forecasts.

    River and forecast data come from Tiger Data ("db") or data/*.csv ("csv");
    "auto" uses the database when DATABASE_URL is configured.
    """
    import tigerdb

    flags = pd.read_csv(FLAGS_FILE, parse_dates=["date"]).sort_values("date").set_index("date")
    if source == "db" or (source == "auto" and tigerdb.configured()):
        ea, fc = tigerdb.load_ea(), tigerdb.load_forecasts()
    else:
        ea = pd.read_csv(EA_FILE, parse_dates=["time_utc"]).set_index("time_utc")
        fc = pd.read_csv(FORECAST_FILE, parse_dates=["time"])
    ea.index = ea.index.tz_convert(TZ)  # Stays timezone-aware: local clocks repeat an hour in October.
    return flags, ea, fc.set_index("time")


def _cutoffs(days):
    """07:00 local time on each day, timezone-aware to match the EA readings."""
    return (days + pd.Timedelta(hours=CUTOFF_HOUR)).tz_localize(TZ)


def _at_cutoffs(series, cutoffs, days):
    """Value of a time series at (or the last reading before) each cut-off, labelled by day."""
    s = series.dropna()
    return pd.Series(s.asof(cutoffs).to_numpy(), index=days)


def build_daily(flags, ea, fc):
    """All features for every day in flags (index = day D), using data up to D 07:00."""
    days = flags.index
    cutoffs = _cutoffs(days)
    out = pd.DataFrame(index=days)

    # River flow: the main driver of the flag. Log scale since flags track it roughly geometrically.
    for col in FLOWS:
        flow = np.log1p(ea[col].clip(lower=0))
        name = col.replace("_m3s", "")
        out[f"{name}_log"] = _at_cutoffs(flow, cutoffs, days)
        for hours in (6, 24, 72):
            out[f"{name}_change_{hours}h"] = out[f"{name}_log"] - _at_cutoffs(flow, cutoffs - pd.Timedelta(hours=hours), days)
        out[f"{name}_max_24h"] = _at_cutoffs(flow.rolling("24h").max(), cutoffs, days)
        out[f"{name}_mean_7d"] = _at_cutoffs(flow.rolling("7D").mean(), cutoffs, days)
    for col in LEVELS:
        out[col] = _at_cutoffs(ea[col], cutoffs, days)
        out[f"{col}_change_24h"] = out[col] - _at_cutoffs(ea[col], cutoffs - pd.Timedelta(hours=24), days)

    # Observed rain at Osney up to the cut-off.
    for window in ("24h", "3D", "7D", "14D"):
        out[f"rain_obs_{window}"] = _at_cutoffs(ea["osney_rain_mm"].rolling(window).sum(), cutoffs, days)

    # Forecast rain for each 24h window D+j 07:00 - D+j+1 07:00. Every hour in window j
    # uses the forecast issued j+1 days earlier (or an older one if missing), so even
    # the window's last hour (D+j+1 06:00) was forecast by D 06:00, before the cut-off.
    window_start = (fc.index - pd.Timedelta(hours=CUTOFF_HOUR)).normalize()
    missing = pd.Series(False, index=days)
    for j in range(HORIZONS):
        hourly = fc[f"rain_fc_{j + 1}d_mm"]
        for older in range(j + 2, HORIZONS + 1):
            hourly = hourly.fillna(fc[f"rain_fc_{older}d_mm"])
        window = hourly.groupby(window_start).sum(min_count=1).reindex(days + pd.Timedelta(days=j))
        window.index = days
        missing |= window.isna()
        out[f"rain_fc_day{j}"] = window.fillna(0)
    out["rain_forecast_missing"] = missing.astype("float32")

    # Today's 07:00 flag and how long it has held.
    flag = flags["ourcs_isis_flag_at_0700"]
    unknown = set(flag) - set(FLAGS)
    if unknown:
        raise ValueError(f"Unknown flag values: {unknown}")
    for f in FLAGS:
        out[f"today_{f}"] = (flag == f).astype("float32")
    out["today_severity"] = flag.map(SEVERITY)
    run_id = (flag != flag.shift()).cumsum()
    out["days_flag_held_log"] = np.log1p(flag.groupby(run_id).cumcount())

    # Weather at 07:00 today (NaN for live days that only have a flag).
    for col in WEATHER:
        out[col] = flags[col] if col in flags else np.nan
    radians = np.deg2rad(flags["wind_direction_10m_deg"]) if "wind_direction_10m_deg" in flags else np.nan
    out["wind_dir_sin"] = np.sin(radians)
    out["wind_dir_cos"] = np.cos(radians)
    return out.astype("float32")


def build_dataset(flags, ea, fc, columns=None):
    """Return (X, Y, today, dates, columns) for days with features and at least one known target.

    Y[:, h-1] is the flag h days later and today today's flag, as indices into
    CLASSES; -1 marks a Grey or unknown flag (Y entries of -1 are not targets).
    """
    daily = build_daily(flags, ea, fc)
    if columns is not None:
        daily = daily[columns]
    index = {f: i for i, f in enumerate(CLASSES)}
    flag = flags["ourcs_isis_flag_at_0700"].map(index)  # NaN for Grey.
    days = flags.index
    Y = np.stack([flag.reindex(days + pd.Timedelta(days=h)).to_numpy() for h in range(1, HORIZONS + 1)], axis=1)
    Y = np.nan_to_num(Y, nan=-1).astype("int32")
    keep = (daily.notna().all(axis=1) & (Y >= 0).any(axis=1)).to_numpy()
    today = flag.fillna(-1).to_numpy("int32")
    return daily[keep].to_numpy("float32"), Y[keep], today[keep], days[keep], list(daily.columns)


def check_no_lookahead(flags, ea, fc, n_checks=30, seed=0):
    """Assert each day's features are unchanged when all data after its cut-off is deleted.

    Deletes: EA readings after D 07:00, flag/weather rows after day D, and forecast
    hours after the last window used. Forecast issue times are handled by choosing
    lead times (see build_daily), which truncation can't test.
    """
    full = build_daily(flags, ea, fc)
    dates = build_dataset(flags, ea, fc)[3]
    rng = np.random.default_rng(seed)
    for d in rng.choice(dates, size=min(n_checks, len(dates)), replace=False):
        d = pd.Timestamp(d)
        cutoff = _cutoffs(pd.DatetimeIndex([d]))[0]
        cut = build_daily(
            flags[flags.index <= d],
            ea[ea.index <= cutoff],
            fc[fc.index < cutoff.tz_localize(None) + pd.Timedelta(days=HORIZONS)],
        )
        if not np.allclose(cut.loc[d], full.loc[d], equal_nan=True):
            bad = cut.columns[~np.isclose(cut.loc[d], full.loc[d], equal_nan=True)]
            raise AssertionError(f"Features for {d.date()} use data after the cut-off: {list(bad)}")
