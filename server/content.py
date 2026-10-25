"""Content service (owner C): Gemini briefs, the daily digest, and article -> market
proposals.

Cuppers matches and results are posted by hand in the admin portal (COLLAB 14). News markets
come from scraped Cherwell / Oxford Student articles (scraper.py): with a Gemini key,
process_new_articles() turns each new article into a 'proposed' race for an admin to approve;
without one, admins create them from the portal's Articles tab.
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
BRIEF_MIN_AGE_S = 6 * 3600     # free-tier Gemini is 20 requests/day: don't rewrite a brief more often
DIGEST_EVERY_S = 24 * 3600


PROPOSE_BATCH = 10


def process_new_articles(limit=PROPOSE_BATCH):
    """Gemini proposals for unprocessed articles -> proposed races. Returns races created.
    Without Gemini, or unless AI_PROPOSALS=1, this is a no-op and the articles stay
    unprocessed (admin queue)."""
    if ai._fake() or os.environ.get("AI_PROPOSALS") != "1":
        return 0
    import trading
    made = 0
    for a in db.unprocessed_articles(limit=limit):
        try:
            spec = ai.propose_market(a)
            if spec:
                trading.create_race_from_spec(spec, article=a, status="proposed", created_by_ai=True)
                made += 1
        except Exception:
            log.exception("proposal for article %s failed", a["id"])
        db.mark_article_processed(a["id"])
    log.info("gemini proposed %d markets", made)
    return made


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
    """Refresh briefs for open matches with no brief yet, or a move of >= 5 points in 24h
    and a brief older than BRIEF_MIN_AGE_S (so restarts don't spend the Gemini quota)."""
    n = 0
    stale_before = db.utcnow() - timedelta(seconds=BRIEF_MIN_AGE_S)
    for r in db.list_races("open"):
        last = db.latest_brief(r["id"], "brief")
        if last is not None and last["created_at"] > stale_before:
            continue
        moved = any(abs(o["change"]) >= BRIEF_MOVE_POINTS for o in _outcomes(r["id"]))
        if moved or last is None:
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
