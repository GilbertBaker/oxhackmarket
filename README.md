# OxMarket

A play-money prediction market on Oxford life, built at OxHack (theme: TIME): college
Cuppers matches, Oxford Union questions and Oxford news. Every outcome is its own YES/NO
market. Prices are in pence (1..99 = implied %). Anyone can sign up and gets £1,000 of play
money. The TimescaleDB-backed Time Machine shows the odds on everything at any moment.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r server/requirements.txt
cp .env.example .env               # then fill in the DB password, SECRET_KEY, etc.
python server/seed.py              # the 10 starting Oxford Union markets (idempotent)
python server/app.py               # http://localhost:5000
```

For a demo, also run `python server/demo.py` (with the app stopped). It creates the login
`demo` / `oxhack2026` (an admin) and backfills 14 days of synthetic price history (ticks +
house-vs-house trades) for every open market, back-dating the markets so charts, 24h moves
and the Time Machine have data. It is safe to re-run: markets that already have history are skipped.

Set `DISABLE_JOBS=0` in `.env` for the real thing: the market maker, price ticks,
auto-close, portfolio snapshots and the news scraper all run as background jobs.
Admins are the usernames listed in `ADMIN_USERNAMES` when they register.

## Admin portal (`/admin.html`)

Everything is stored in the database (schema `oxmarket`):

- **Markets**: drafts awaiting approval, markets awaiting a result (post the result to settle
  and pay out), and every market in every status with edit / approve / reject / stop trading.
- **Create market**: three kinds (`races.kind`). *Cuppers match* (Home / Draw / Away, from the
  competitions and teams tables), *Yes / No question* (one market), *Multiple choice* (named
  outcomes, N winners). Publish now or save as a draft.
- **News articles**: Cherwell and Oxford Student headlines scraped from their RSS feeds
  (`server/scraper.py`). Turn one into a market (linked to its source) or dismiss it. With a
  `GEMINI_API_KEY` and `AI_PROPOSALS=1` (off by default), Gemini proposes markets from new
  articles as drafts. Gemini also writes the match briefs and daily digest (`FAKE_AI=0`); the
  free tier is 20 requests/day per model, so briefs are rewritten at most every 6 hours.
- **Reference data**: terms, categories, competitions, teams and sports tables.
- **Settings**: the `settings` table (market maker size / spread / interval, job intervals,
  starting cash, default rules and odds). Changes apply within ~30 s, no restart.
- **Activity**: every admin write is logged to the `admin_actions` hypertable.

Tests: `python -m pytest server/tests -q` (no DB) and `python -m pytest tests/db -v` (real DB,
run separately; only touches rows tagged with a random per-run id).

On Intel Macs, `pip install --only-binary cryptography -r server/requirements.txt`
avoids a source build of `cryptography`.

## Engine & Trading (A)

### Building the engine

The C++ order book in `book/OrderBook` is built with CLion's bundled toolchain (no cmake/g++ on PATH needed). From Git Bash:

```bash
cd book/OrderBook
export PATH="/c/Program Files/JetBrains/CLion 2026.2.0.1/bin/mingw/bin:$PATH"
CM="/c/Program Files/JetBrains/CLion 2026.2.0.1/bin/cmake/win/x64/bin/cmake.exe"
"$CM" -S . -B build -G "MinGW Makefiles" -DCMAKE_BUILD_TYPE=Debug   # Debug keeps assert() live in the tests
"$CM" --build build -j 8
./build/OrderBookTests.exe                                          # "All tests passed."
./build/MarketServer.exe < tests/protocol_input.txt | diff --strip-trailing-cr tests/expected_output.txt - && echo PROTOCOL OK
```

`build/MarketServer.exe` (+ `libwinpthread-1.dll`, copied next to it by CMake) is what `ENGINE_PATH` points at.
If the exe is missing, `server/engine.py` logs an error and falls back to a pure-Python book with the same protocol, so the app still runs. Use the real engine for the demo.

Trading tests (fake in-memory db; run against both the Python fallback and the C++ exe if built):

```bash
python -m pytest server/tests/test_trading*.py -q
```

### Matching engine & market mechanics

- One YES order book per candidate market, inside one `MarketServer.exe` process that `server/engine.py` drives over stdin/stdout (protocol: `plan/COLLAB.txt` section 8).
- The engine only matches. Python/Postgres own cash and positions: `trading.place_order` validates, runs the risk check, sends the order to the engine and writes every fill in one DB transaction. If that transaction fails, the engine is restarted and replayed from the DB's open orders.
- **NO is a view on the YES book:** Buy NO @ p = Sell YES @ (100 − p), Sell NO @ p = Buy YES @ (100 − p).
- **Collateral:** an open buy locks price × qty, a short YES position locks 100 per share, and an open sell locks (100 − price) per share that a long position doesn't cover. `available = cash − locked`; orders that would make it negative are rejected before reaching the engine.
- Changes to the owner's C++ (kept minimal, no changes to the matching logic, AVL tree or lists):
  1. `TraderAlgo(book, noOfTraders = 100)`: the server creates 0 random traders.
  2. `TraderAlgo::getTrader` creates unknown traders on first use (it used to return nullptr and crash for web users).
  3. `OrderBook::fills`: each of the 4 matching loops appends a `Fill` so the server can report trades.
  4. `OrderTree::collectLowest/HighestLevels` + `OrderBook::getDepth`: same traversal as the print functions, for the BOOK command.
  5. `OrderBook::getRestingQty(orderID)`: needed because fully filled incoming orders are deleted.
  6. The "Invalid cancel ID" print goes to stderr so it can't corrupt the protocol stream.
- House market maker (`server/mm_bot.py`): every 10 s it quotes 50 @ fair ± 2 in every open candidate market. Fair = AI prior until the market trades, then an EMA of trades pulled 10% toward the prior, normalised so each race sums to 100 × seats, and skewed against the house's inventory.

## Data & TIME (B)

_TODO (B): schema, data APIs, jobs, seed data._

## TimescaleDB usage

_TODO (B7)_

## Content, AI & Web (C)

_TODO (C)_
