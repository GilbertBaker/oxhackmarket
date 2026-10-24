"""Background jobs (owner B, COLLAB 10): price ticks, auto-close, portfolio snapshots.

Each job runs in its own daemon thread and logs (never raises) on failure.
"""
import logging
import threading
import time
from datetime import timedelta

import db
import trading

log = logging.getLogger("jobs")

TICK_EVERY = 15
CLOSE_EVERY = 60
SNAPSHOT_EVERY = 300
TICK_MAX_AGE = timedelta(seconds=60)

_started = False


def snapshot_ticks():
    """Record best bid/ask/last for each open market; skip unchanged ticks under 60 s old."""
    n = 0
    now = db.utcnow()
    for m in db.list_markets(status="open"):
        book = trading.get_book(m["id"], 1)
        bid = book["bids"][0]["price"] if book.get("bids") else None
        ask = book["asks"][0]["price"] if book.get("asks") else None
        last = db.last_trade_price(m["id"])
        prev = db.last_tick(m["id"])
        if (prev and (prev["best_bid"], prev["best_ask"], prev["last_price"]) == (bid, ask, last)
                and now - prev["time"] < TICK_MAX_AGE):
            continue
        db.record_tick(m["id"], bid, ask, last)
        n += 1
    return n


def auto_close():
    """Close open races whose polls have closed."""
    closed = []
    now = db.utcnow()
    for race in db.list_races("open"):
        if race["closes_at"] <= now:
            log.info("auto-closing race %s (%s %s)", race["id"], race["election"], race["position"])
            trading.close_race(race["id"])
            closed.append(race["id"])
    return closed


def snapshot_portfolios():
    n = 0
    for user in db.list_users(include_bots=False):
        db.insert_portfolio_snapshot(user["id"], user["cash"], db.equity(user["id"]))
        n += 1
    return n


def _loop(name, fn, every, run_first=True):
    if not run_first:
        time.sleep(every)
    while True:
        started = time.monotonic()
        try:
            fn()
        except Exception:
            log.exception("job %s failed", name)
        time.sleep(max(1.0, every - (time.monotonic() - started)))


def start_background():
    global _started
    if _started:
        return
    _started = True
    for name, fn, every in (("ticks", snapshot_ticks, TICK_EVERY),
                            ("auto_close", auto_close, CLOSE_EVERY),
                            ("portfolio_snapshots", snapshot_portfolios, SNAPSHOT_EVERY)):
        threading.Thread(target=_loop, args=(name, fn, every), name=f"job-{name}", daemon=True).start()
    log.info("background jobs started")
