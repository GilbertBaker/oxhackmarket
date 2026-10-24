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

## 2026-10-24 23:40 [A] Engine MVP: C++ MarketServer + Python trading layer
- A1: the existing book builds with CLion's cmake 4.3.1 + MinGW 15.2 (no CMakeLists changes needed). OrderBookTests pass. Gotcha: a Release build defines NDEBUG, which turns the tests' `assert`s into no-ops, so build Debug for tests.
- A2: minimal C++ changes, each one necessary: lazy trader creation (getTrader returned nullptr for unknown ids, which would crash for the first web user), a `Fill` vector filled by the 4 matching loops (there was no other way to observe trades), depth collection mirroring the existing print traversal, `getRestingQty`, and the cancel error print moved to stderr. 3 new C++ tests.
- A3: `MarketServer.exe` speaks the COLLAB 8 protocol. Each market's book lives behind a unique_ptr because `book.algo` points into the struct and the map can rehash. Full `-static` linking fails with this MinGW (winpthread/msvcrt symbols), so we link libgcc/libstdc++ statically and CMake copies libwinpthread-1.dll next to the exe. The scripted protocol test (tests/protocol_input.txt) diffs clean.
- A4-A8: engine.py (re-entrant lock, auto restart + replay from DB, pure-Python fallback book if the exe is missing so B/C can run without building C++), risk.py (collateral rule), trading.py (all COLLAB 7.1 functions), trading_api.py (all "Trading [A]" endpoints), mm_bot.py (race-normalised quotes).
- 28 pytest tests on an in-memory fake db, run against BOTH engines: YES vs NO crossing, partial fills, risk rejection that never reaches the engine, market orders capped across 3 levels, cancels, settlement, single/multi-seat and RON race resolution, DB failure -> engine resync, killing the engine process -> replay, MM quotes summing to ~100, and a 300-order random run checking sum(positions)=0, available >= 0 and engine/DB book agreement.

## 2026-10-25 00:30 [A] Integration helpers, faster MM bot, invariants, marketctl CLI
- trading.py now exposes one-call helpers for everyone else: create_race (race + candidate markets + RON + books), add_candidate, approve/reject_race, market_snapshot / record_ticks for the price-tick job, and check_invariants.
- The MM bot skips markets where the quote it wants is already resting at full size, so a quiet 10 s pass makes no DB writes and barely holds the engine lock. This matters on cloud Timescale, where every order costs ~10 round trips.
- server/marketctl.py is an HTTP client, not an in-process tool. The engine's books live in the server process, and a CLI with its own engine would silently diverge from them. Its `sim` command drives simulated traders (drifting beliefs + occasional "news" shocks) through the public API to create realistic price history for the TIME demo.
- 40 tests, including marketctl end to end over a real local HTTP server (create race -> MM quotes -> market order -> 3-trader sim -> invariant check OK).

## 2026-10-25 01:15 [A] Latency + concurrency hardening
- Benchmark (test_trading_load.py, TRADING_LOAD=1): every fake DB call sleeps 30 ms to mimic cloud Timescale round trips, with 20 candidate markets.
  Before: a user's order took 1.4 s while the MM bot was requoting, because Python locks aren't fair (the bot re-grabbed the engine lock market after market), and an idle bot pass cost 2 s of locked DB reads.
  After: 0.46 s p50 (0.7 s max) for user orders under bot load, an idle pass takes 0.9 s and never takes the lock, and total DB calls fell 959 -> 531.
- How: the bot reads the house's orders and positions once per pass, decides off-lock, locks only for markets that changed (re-reading under the lock so it never cancels from stale data), and sleeps 10 ms between markets. place_order only reads the last maker order a sweep touched (every earlier maker is fully filled by definition) and makes one cash/position write per user instead of per fill. The house bot skips the redundant market re-read and the risk check (its cash covers any quote).
- Stress test (always on): 8 HTTP clients firing limit/market orders and cancels at a threaded server while the MM loop runs, for both engines. Result: about 700 requests and 363 trades across the two runs, zero 5xx, total cash conserved exactly, check_invariants() clean.

## 2026-10-24 23:55 [C] C6 frontend: Cuppers match markets (white + navy, Polymarket-style)
- The owner changed the scope during the build: Union elections out, Cuppers in, and every market is now a single match with one outcome market per result (home win / draw / away win, or just the two teams where a draw can't happen). Matches and results are posted by hand in the admin portal, so there is no scraping. The race/candidate schema fits this without changes (race = match, candidates = outcomes); see COLLAB 14.
- web/: 8 pages (index, race, market, timemachine, leaderboard, portfolio, admin, login), css/style.css, js/api.js (fetch wrapper + mock switch), js/common.js (nav, formatting, match parsing, polling that pauses when the tab is hidden, live countdowns, Chart.js setup), one script per page. Plain HTML/CSS/JS; Chart.js 4.4.1 from cdnjs is the only library.
- Design: white canvas, dark-blue accent (#0b2a66), green/red only for YES/NO. The home page is a fixture list grouped by day with Polymarket-style match cards (odds bars, 24h change arrows, one-click outcome buttons). Prices always appear both ways ("YES 62p · NO 38p" = 62%).
- Market page: the ticket works in the chosen outcome's terms (Buy NO @ p is sent as outcome NO, price p; the server converts to Sell YES @ 100-p). It shows a live cost preview, estimates market orders by walking the book, has a depth ladder whose levels fill the ticket when clicked, a trade tape, and your orders with cancel. On phones the ticket sits above the chart.
- Time Machine: slider + play over /api/timemachine. Rows re-sort with a FLIP animation when the favourite changes, and diamonds on the track mark lead changes computed from hourly race history (3-point hysteresis so noise doesn't flood the track).
- Mock mode (js/mock.js): a fake Michaelmas fixture list with results, one match awaiting its result, upcoming matches, live price jitter, a working order book, orders/positions/cash, and admin create/close/resolve. Checked in headless Chrome: screenshots at 1280px and 375px, plus a scripted flow (buy NO -> NO position, rest + cancel, insufficient funds error, create -> close -> resolve, time machine).
