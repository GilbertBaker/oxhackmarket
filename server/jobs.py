"""Background jobs (owner B, COLLAB 10): price ticks, auto-close, portfolio snapshots,
and (owner C) the Cherwell / Oxford Student feed scrape.

Each job runs in its own daemon thread and logs (never raises) on failure. Intervals come
from oxmarket.settings (jobs.*), so they can be changed in the admin portal without a restart.
Each pass does its reads and writes on ONE connection: the cloud DB is ~100 ms per round trip.
"""
import logging
import threading
import time
from datetime import timedelta

import db
import trading

log = logging.getLogger("jobs")

# defaults; live values are the jobs.* settings
TICK_EVERY = 15
CLOSE_EVERY = 60
SNAPSHOT_EVERY = 300
SCRAPE_EVERY_MIN = 30
TICK_MAX_AGE = timedelta(seconds=60)

_started = False


def _setting(key, default):
    v = db.get_setting(key, default) if hasattr(db, "get_setting") else default
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else default


def snapshot_ticks():
    """Record best bid/ask/last for each open market; skip unchanged ticks under 60 s old."""
    now = db.utcnow()
    with db.transaction() as conn:
        markets = db.list_markets(status="open", conn=conn)
        ids = [m["id"] for m in markets]
        if not ids:
            return 0
        prev = db.latest_ticks(ids, conn=conn)
        last = db.last_trade_prices(ids, conn=conn)
        rows = []
        for mid in ids:
            book = trading.get_book(mid, 1)
            bid = book["bids"][0]["price"] if book.get("bids") else None
            ask = book["asks"][0]["price"] if book.get("asks") else None
            p = prev.get(mid)
            if (p and (p["best_bid"], p["best_ask"], p["last_price"]) == (bid, ask, last.get(mid))
                    and now - p["time"] < TICK_MAX_AGE):
                continue
            rows.append((now, mid, bid, ask, last.get(mid)))
        db.record_ticks(rows, conn=conn)
    return len(rows)


def auto_close():
    """Close open races whose closing time has passed."""
    closed = []
    now = db.utcnow()
    for race in db.list_races("open"):
        if race["closes_at"] <= now:
            log.info("auto-closing race %s (%s %s)", race["id"], race["election"], race["position"])
            trading.close_race(race["id"])
            closed.append(race["id"])
    return closed


def snapshot_portfolios():
    """One equity snapshot per (non-bot) user, all in one transaction."""
    now = db.utcnow()
    with db.transaction() as conn:
        rows = [(now, u["id"], u["cash"], e) for u, e in db.all_equity(conn=conn)]
        db.insert_portfolio_snapshots(rows, conn=conn)
    return len(rows)


def scrape_feeds():
    import scraper   # lazy: keeps jobs importable without feedparser in the fake-DB tests
    return scraper.scrape_all()


def _loop(name, fn, every, run_first=True):
    """every = a callable returning seconds, re-read each pass (settings can change)."""
    if not run_first:
        time.sleep(every())
    while True:
        started = time.monotonic()
        try:
            fn()
        except Exception:
            log.exception("job %s failed", name)
        time.sleep(max(1.0, every() - (time.monotonic() - started)))


def start_background():
    global _started
    if _started:
        return
    _started = True
    for name, fn, every, first in (
            ("ticks", snapshot_ticks, lambda: _setting("jobs.tick_seconds", TICK_EVERY), True),
            ("auto_close", auto_close, lambda: _setting("jobs.autoclose_seconds", CLOSE_EVERY), True),
            ("portfolio_snapshots", snapshot_portfolios,
             lambda: _setting("jobs.portfolio_seconds", SNAPSHOT_EVERY), True),
            ("scrape", scrape_feeds, lambda: 60 * _setting("content.scrape_minutes", SCRAPE_EVERY_MIN), True)):
        threading.Thread(target=_loop, args=(name, fn, every, first), name=f"job-{name}", daemon=True).start()
    log.info("background jobs started")
