# OxMarket DEVLOG

Append-only. Never rewrite another stream's entries. Format (COLLAB 12):

    ## YYYY-MM-DD HH:MM [A|B|C] Short title
    - what was built / decided / broke and how it was fixed
    - prize-relevant notes (Timescale feature used, Gemini prompt, etc.)

## 2026-10-24 21:30 [B] B1 skeleton
- app.py (logging to console + server/logs/app.log, blueprints, guarded startup steps,
  JSON 404/500 for /api/*, use_reloader=False), .gitignore, .env.example, requirements.txt,
  README outline. STUB trading.py / trading_api.py / content.py / content_api.py / web/index.html
  so the app boots before A and C deliver.
- Dev machine is an Intel Mac: `pip install --only-binary cryptography ...` avoids building
  cryptography from source (pulled in by google-genai).

## 2026-10-24 22:30 [B] B2 read-only inspection + schema applied
- Service: v225ef4gzi.k9zhkdj7kx.tsdb.cloud.timescale.com:35489 (NOT the yuaqhx7mx1 host the plan
  originally listed). PostgreSQL 18.6, timescaledb 2.31.0, timescaledb_toolkit 1.26.0.
- Read-only inspection BEFORE any DDL: schemas = public + timescale internals only;
  public holds just the pg_buffercache / pg_buffercache_numa views; no hypertables, no caggs;
  jobs = the two built-in ones (policy_telemetry, policy_job_stat_history_retention);
  `oxmarket` did not exist. timescaledb already installed, so no CREATE EXTENSION.
- schema.sql applied twice with no errors. Afterwards: hypertables trades, price_ticks,
  portfolio_snapshots (1-day chunks); caggs candles_5m / candles_1h with real-time aggregation;
  jobs 1001/1002 (cagg refresh every 1 min / 5 min), 1003 (compression of price_ticks > 1 day,
  segmentby market_id, orderby time DESC), 1004 (retention of price_ticks after 90 days).
  Objects outside oxmarket unchanged (public still only pg_buffercache views).
- Gotcha: continuous aggregates can't be created inside a transaction, so ensure_schema()
  splits schema.sql and runs it in autocommit. `ALTER TABLE ... SET (timescaledb.compress)`
  is guarded by a DO block because it fails on re-run once chunks are compressed.

## 2026-10-24 22:40 [B] B3 db.py + tests, B4 data_api first pass
- db.py implements every COLLAB 7.2 function, plus extras: close_series, prices_at,
  market_stats, last_tick, insert_portfolio_snapshot, user_orders, timemachine_range,
  timescale_stats, iso/parse_iso.
- Timescale features in queries: time_bucket_gapfill + locf (price_history, race_history,
  sparklines); candles_5m/1h caggs for 5m/1h candles vs raw trades for 1m; first()/last()
  for OHLC; as-of LATERAL lookup (ORDER BY time DESC LIMIT 1 with time <= t) for the Time Machine;
  time_bucket + last(equity, time) for leaderboard history.
- Decision: price_at / timemachine use the SAME rule as current_price (two-sided tick mid,
  else last trade, else ai_prior), restricted to time <= t, so the Time Machine at "now"
  matches live prices. The plan suggested trade-first; logged in COLLAB 14.
- server/tests/test_db.py: 5 tests against the live service (users, race CRUD + status
  propagation + UNIQUE, transaction rollback, cagg candles, hole-free gapfill, price_at,
  timemachine, equity with a short, leaderboard, equity history). All pass (34 s, network bound).
  Cleanup deletes only rows tagged test_<uuid> / "TEST <uuid>"; verified all tables back to 0
  rows (except the house user).
- Endpoints smoke-tested with curl: register/login/logout/me with cookie, races, markets,
  timemachine(+range), leaderboard(+history), /api/stats, JSON 404, 400 on a bad ISO time.
