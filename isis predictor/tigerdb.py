"""Tiger Data (TimescaleDB) storage for river readings, rain forecasts and flag forecasts.

Tables (created on first use):
- river_readings: 15-minute EA readings, a hypertable compressed after 30 days.
- rain_forecasts: hourly archived rain forecasts by lead time, a hypertable.
- river_hourly: continuous aggregate of river_readings (hourly flow, levels, rain),
  kept up to date by a refresh policy; used for the website's charts.
- flag_forecasts: every forecast the model issues, a hypertable, so the forecast
  track record builds up over time.
Flag history is read from ourcs_flag_history (loaded separately by the team).

Needs DATABASE_URL in .env (see README). Requires: pip install "psycopg[binary]"
"""

from io import StringIO
import json
import os

import pandas as pd

import env  # noqa: F401  (loads .env)

EA_COLUMNS = ["osney_level_m", "iffley_level_m", "farmoor_flow_m3s", "cherwell_flow_m3s", "osney_rain_mm"]
FORECAST_COLUMNS = [f"rain_fc_{lead}d_mm" for lead in range(1, 6)]
REVISION_DAYS = 10  # Recent EA readings get quality-checked and revised, so re-upload these.

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS river_readings (
    time TIMESTAMPTZ NOT NULL,
    {", ".join(f"{c} DOUBLE PRECISION" for c in EA_COLUMNS)}
);
SELECT create_hypertable('river_readings', 'time', if_not_exists => TRUE);
ALTER TABLE river_readings SET (timescaledb.compress, timescaledb.compress_orderby = 'time');
SELECT add_compression_policy('river_readings', INTERVAL '30 days', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS rain_forecasts (
    time TIMESTAMP NOT NULL,  -- Europe/London wall-clock hour the forecast is for
    {", ".join(f"{c} DOUBLE PRECISION" for c in FORECAST_COLUMNS)}
);
SELECT create_hypertable('rain_forecasts', 'time', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS flag_forecasts (
    issued_at TIMESTAMPTZ NOT NULL,  -- the 07:00 cut-off the forecast used
    target_date DATE NOT NULL,
    horizon_days INTEGER NOT NULL,
    flag TEXT NOT NULL,
    p_green DOUBLE PRECISION NOT NULL,
    probabilities JSONB NOT NULL,
    PRIMARY KEY (issued_at, horizon_days)
);
SELECT create_hypertable('flag_forecasts', 'issued_at', if_not_exists => TRUE);
"""

HOURLY_VIEW = """
CREATE MATERIALIZED VIEW IF NOT EXISTS river_hourly WITH (timescaledb.continuous) AS
SELECT time_bucket('1 hour', time) AS hour,
       avg(farmoor_flow_m3s) AS farmoor_flow_m3s,
       avg(cherwell_flow_m3s) AS cherwell_flow_m3s,
       avg(osney_level_m) AS osney_level_m,
       avg(iffley_level_m) AS iffley_level_m,
       sum(osney_rain_mm) AS osney_rain_mm
FROM river_readings
GROUP BY hour
WITH NO DATA;
SELECT add_continuous_aggregate_policy('river_hourly',
    start_offset => INTERVAL '30 days', end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour', if_not_exists => TRUE);
"""


def configured():
    return bool(os.environ.get("DATABASE_URL"))


def connect(autocommit=False):
    import psycopg  # Imported lazily so the rest of the project works without it.
    return psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=15, autocommit=autocommit)


def ensure_schema():
    # Continuous aggregate DDL and refreshes can't run inside a transaction.
    with connect(autocommit=True) as conn:
        for statement in (SCHEMA + HOURLY_VIEW).split(";"):
            if statement.strip():
                conn.execute(statement)


def _copy(conn, table, df, columns):
    buffer = StringIO()
    df[columns].to_csv(buffer, index=False, header=False, na_rep="")
    buffer.seek(0)
    with conn.cursor() as cur:
        with cur.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN WITH (FORMAT csv)") as copy:
            copy.write(buffer.read())


def upload(ea, fc):
    """Upload new EA readings (index time_utc) and replace overlapping rain forecasts."""
    ensure_schema()
    ea = ea.reset_index().rename(columns={"time_utc": "time"})
    with connect() as conn:
        newest = conn.execute("SELECT max(time) FROM river_readings").fetchone()[0]
        if newest is not None:
            # Only touch recent (uncompressed) chunks: re-upload the revision window.
            since = pd.Timestamp(newest) - pd.Timedelta(days=REVISION_DAYS)
            conn.execute("DELETE FROM river_readings WHERE time >= %s", (since,))
            ea = ea[ea["time"] >= since]
        ea["time"] = ea["time"].map(lambda t: t.isoformat())
        _copy(conn, "river_readings", ea, ["time", *EA_COLUMNS])

        fc = fc.copy()
        conn.execute("DELETE FROM rain_forecasts WHERE time BETWEEN %s AND %s", (fc["time"].min(), fc["time"].max()))
        _copy(conn, "rain_forecasts", fc, ["time", *FORECAST_COLUMNS])
    with connect(autocommit=True) as conn:
        conn.execute("CALL refresh_continuous_aggregate('river_hourly', NULL, NULL)")


def load_ea():
    """EA readings indexed by UTC time, matching data/ea_15min.csv."""
    with connect() as conn:
        rows = conn.execute(f"SELECT time, {', '.join(EA_COLUMNS)} FROM river_readings ORDER BY time").fetchall()
    df = pd.DataFrame(rows, columns=["time_utc", *EA_COLUMNS]).set_index("time_utc")
    df.index = pd.to_datetime(df.index, utc=True)
    return df.astype("float64")


def load_forecasts():
    """Hourly rain forecasts with a naive local 'time' column, matching data/rain_forecast_hourly.csv."""
    with connect() as conn:
        rows = conn.execute(f"SELECT time, {', '.join(FORECAST_COLUMNS)} FROM rain_forecasts ORDER BY time").fetchall()
    df = pd.DataFrame(rows, columns=["time", *FORECAST_COLUMNS])
    df["time"] = pd.to_datetime(df["time"])
    df[FORECAST_COLUMNS] = df[FORECAST_COLUMNS].astype("float64")
    return df


def load_flag_history(river="isis"):
    """OURCs flag periods: DataFrame of status, start, end (end NaT for the current flag)."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT status, start_local, end_local FROM ourcs_flag_history WHERE river = %s ORDER BY start_local",
            (river,),
        ).fetchall()
    df = pd.DataFrame(rows, columns=["status", "start", "end"])
    df["start"] = pd.to_datetime(df["start"], utc=True)
    df["end"] = pd.to_datetime(df["end"], utc=True)
    return df


def save_forecast(issued_at, rows):
    """Store one forecast: rows of dicts with target_date, horizon_days, flag, p_green, probabilities."""
    ensure_schema()
    with connect() as conn:
        conn.execute("DELETE FROM flag_forecasts WHERE issued_at = %s", (issued_at,))
        for r in rows:
            conn.execute(
                "INSERT INTO flag_forecasts VALUES (%s, %s, %s, %s, %s, %s)",
                (issued_at, r["target_date"], r["horizon_days"], r["flag"], r["p_green"], json.dumps(r["probabilities"])),
            )


def hourly_river(days=14):
    """Last `days` of hourly flow/level/rain from the continuous aggregate."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT hour, farmoor_flow_m3s, cherwell_flow_m3s, osney_rain_mm FROM river_hourly "
            "WHERE hour > now() - %s * INTERVAL '1 day' ORDER BY hour",
            (days,),
        ).fetchall()
    return [
        {"hour": h.isoformat(), "farmoor_flow_m3s": f, "cherwell_flow_m3s": c, "rain_mm": r}
        for h, f, c, r in rows
    ]
