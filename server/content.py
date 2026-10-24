"""Content service (owner C): Gemini briefs and the daily digest for Cuppers matches.

Matches and results are posted by hand in the admin portal (COLLAB 14), so there is no
scraping: process_new_articles() is kept for the COLLAB 7.3 contract and does nothing.
"""
import logging
import os
import threading
import time
from datetime import timedelta

import ai
import db

log = logging.getLogger("content")

BRIEF_EVERY_S = 30 * 60
BRIEF_MOVE_POINTS = 5
DIGEST_EVERY_S = 24 * 3600


def process_new_articles():
    return 0


def _outcomes(race_id, hours=24):
    """[{"market_id","candidate","price","change"}]: current price and the move over `hours`."""
    markets = db.race_markets(race_id)
    stats = db.market_stats([m["id"] for m in markets]) if hours == 24 else {}
    out = []
    for m in markets:
        price = int(m["price"])
        ago = (stats.get(m["id"]) or {}).get("price_24h_ago")
        out.append({"market_id": m["id"], "candidate": m["candidate"], "price": price,
                    "change": price - int(ago) if ago is not None else 0})
    return out


def refresh_brief(race_id):
    race = db.get_race(race_id)
    if race is None:
        raise ValueError("race not found")
    text = ai.race_brief(race, _outcomes(race_id))
    db.save_brief({"race_id": race_id, "kind": "brief", "text": text})
    return db.latest_brief(race_id, "brief")


def refresh_digest():
    races = [{"position": r["position"], "closes_at": db.iso(r["closes_at"]), "candidates": _outcomes(r["id"])}
             for r in db.list_races("open")]
    db.save_brief({"race_id": None, "kind": "digest", "text": ai.daily_digest(races)})
    return db.latest_brief(None, "digest")


def _brief_pass():
    """Refresh briefs for open matches with no brief yet or a move of >= 5 points in 24h."""
    n = 0
    for r in db.list_races("open"):
        moved = any(abs(o["change"]) >= BRIEF_MOVE_POINTS for o in _outcomes(r["id"]))
        if moved or db.latest_brief(r["id"], "brief") is None:
            refresh_brief(r["id"])
            n += 1
    return n


def _digest_due():
    d = db.latest_brief(None, "digest")
    return d is None or d["created_at"] < db.utcnow() - timedelta(seconds=DIGEST_EVERY_S)


def _loop(name, every, fn, first_delay):
    time.sleep(first_delay)
    while True:
        try:
            fn()
        except Exception:
            log.exception("%s failed", name)
        time.sleep(every)


def start_background():
    if os.environ.get("DISABLE_JOBS") == "1":
        return
    threading.Thread(target=_loop, args=("briefs", BRIEF_EVERY_S, _brief_pass, 30), daemon=True, name="briefs").start()
    threading.Thread(target=_loop, args=("digest", 600, lambda: _digest_due() and refresh_digest(), 20),
                     daemon=True, name="digest").start()
    log.info("content jobs started (briefs every %ds, digest daily)", BRIEF_EVERY_S)
