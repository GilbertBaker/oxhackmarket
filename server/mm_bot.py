"""House market maker: keeps a two-sided quote in every open candidate market,
with fair values normalised so a race's candidates sum to 100 * seats."""
import logging
import threading
import time

import db
import trading

log = logging.getLogger("mm_bot")

INTERVAL = 10
SIZE = 50
HALF_SPREAD = 2
ALPHA = 0.3
PRIOR_PULL = 0.1

_thread = None


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def raw_fair(market, n_candidates, seats):
    """EMA of recent trades pulled toward the AI prior, or the prior / uniform if untraded."""
    prior = market.get("ai_prior") or (100.0 * seats / max(1, n_candidates))
    trades = db.recent_trades(market["id"], limit=20)   # newest first
    if not trades:
        return float(prior)
    ema = None
    for t in reversed(trades):
        ema = t["price"] if ema is None else ALPHA * t["price"] + (1 - ALPHA) * ema
    return (1 - PRIOR_PULL) * ema + PRIOR_PULL * prior


def fair_values(markets, seats):
    """{market_id: fair} normalised to sum 100*seats, clamped to 2..98."""
    raws = {m["id"]: max(0.5, raw_fair(m, len(markets), seats)) for m in markets}
    total = sum(raws.values())
    return {mid: _clamp(r * 100.0 * seats / total, 2, 98) for mid, r in raws.items()}


def quotes(fair, house_position):
    skew = _clamp(-house_position / 50.0, -3, 3)
    mid = round(fair + skew)
    bid = _clamp(mid - HALF_SPREAD, 1, 98)
    ask = _clamp(mid + HALF_SPREAD, 2, 99)
    if ask <= bid:
        ask = bid + 1
    return bid, ask


def requote_market(house_id, market_id, fair):
    with trading._engine().lock:
        for o in db.open_orders(market_id=market_id, user_id=house_id):
            try:
                trading.cancel_order(house_id, o["id"], is_admin=True)
            except trading.TradingError:
                pass
        pos = db.get_position(house_id, market_id)["qty"]
        bid, ask = quotes(fair, pos)
        trading.place_order(house_id, market_id, "YES", "buy", "limit", bid, SIZE)
        trading.place_order(house_id, market_id, "YES", "sell", "limit", ask, SIZE)


def requote_all():
    house_id = db.ensure_bot_user()
    for race in db.list_races(status="open"):
        try:
            markets = [m for m in db.race_markets(race["id"]) if m["status"] == "open"]
            if not markets:
                continue
            fairs = fair_values(markets, int(race.get("seats") or 1))
        except Exception:
            log.exception("fair values failed for race %s", race["id"])
            continue
        for m in markets:
            try:
                requote_market(house_id, m["id"], fairs[m["id"]])
            except Exception:
                log.exception("requote failed for market %s", m["id"])


def _loop():
    while True:
        try:
            requote_all()
        except Exception:
            log.exception("mm bot pass failed")
        time.sleep(INTERVAL)


def start():
    global _thread
    if _thread is None:
        _thread = threading.Thread(target=_loop, name="mm_bot", daemon=True)
        _thread.start()
        log.info("market maker started (every %ss)", INTERVAL)
