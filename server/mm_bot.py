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
YIELD = 0.01   # seconds between requoted markets

_thread = None
_ema = {}          # market_id -> EMA of trade prices (None = never traded); fed by trading fills
_ema_lock = threading.Lock()


def _on_fills(market_id, fills):
    with _ema_lock:
        ema = _ema.get(market_id)
        for f in fills:
            ema = f["price"] if ema is None else ALPHA * f["price"] + (1 - ALPHA) * ema
        _ema[market_id] = ema


trading.fill_listeners.append(_on_fills)


def reset_state():
    """Forget cached EMAs (tests / after a DB reset)."""
    with _ema_lock:
        _ema.clear()


def _trade_ema(market_id, conn=None):
    """EMA from memory; on first sight of a market, seeded once from its recent trades."""
    with _ema_lock:
        if market_id in _ema:
            return _ema[market_id]
    ema = None
    for t in reversed(db.recent_trades(market_id, limit=20, conn=conn)):   # newest first -> oldest first
        ema = t["price"] if ema is None else ALPHA * t["price"] + (1 - ALPHA) * ema
    with _ema_lock:
        _ema.setdefault(market_id, ema)
        return _ema[market_id]


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def raw_fair(market, n_candidates, seats, conn=None):
    """EMA of trades pulled toward the prior, or the prior / uniform if untraded."""
    prior = market.get("ai_prior") or (100.0 * seats / max(1, n_candidates))
    ema = _trade_ema(market["id"], conn)
    if ema is None:
        return float(prior)
    return (1 - PRIOR_PULL) * ema + PRIOR_PULL * prior


def fair_values(markets, seats, conn=None):
    """{market_id: fair} normalised to sum 100*seats, clamped to 2..98."""
    raws = {m["id"]: max(0.5, raw_fair(m, len(markets), seats, conn)) for m in markets}
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


def _target(bid, ask):
    return [("buy", bid, SIZE), ("sell", ask, SIZE)]


def _current(orders):
    return sorted((o["side"], o["price"], o["qty_remaining"]) for o in orders)


def requote_market(house_id, market, fair, mine=None, pos=None):
    """Returns True if the quotes were replaced, False if the current ones were kept.
    mine/pos = the house's open orders / position here, read once per pass by requote_all.
    They may be slightly stale; they're only used to decide whether to requote. The engine
    lock is taken only when something changes, and the orders are re-read under it."""
    market_id = market["id"]
    if pos is None:
        pos = db.get_position(house_id, market_id)["qty"]
    if mine is None:
        mine = db.open_orders(market_id=market_id, user_id=house_id)
    bid, ask = quotes(fair, pos)
    if _current(mine) == _target(bid, ask):
        return False   # already quoting exactly this: no lock, no DB writes
    # one lock + one transaction for the whole requote; the commit happens before the lock is released
    with trading._engine().lock, db.transaction() as conn:
        mine = db.open_orders(market_id=market_id, user_id=house_id, conn=conn)   # fresh, under the lock
        with trading._batch(conn):
            for o in mine:
                try:
                    trading.cancel_order(house_id, o["id"], is_admin=True, _order=o, _conn=conn)
                except trading.TradingError:
                    pass
        trading.place_order(house_id, market_id, "YES", "buy", "limit", bid, SIZE,
                            _trusted_market=market, _conn=conn)
        trading.place_order(house_id, market_id, "YES", "sell", "limit", ask, SIZE,
                            _trusted_market=market, _conn=conn)
    time.sleep(YIELD)   # Python locks aren't fair: let waiting user orders in before the next market
    return True


def requote_all():
    """One pass over every open race. Returns the number of markets requoted."""
    # all reads for the pass on one connection (each checkout costs round trips to the cloud DB)
    plan = []
    with db.transaction() as conn:
        house_id = db.ensure_bot_user(conn=conn)
        by_market = {}
        for o in db.open_orders(user_id=house_id, conn=conn):
            by_market.setdefault(o["market_id"], []).append(o)
        positions = {p["market_id"]: int(p["qty"]) for p in db.user_positions(house_id, conn=conn)}
        for race in db.list_races(status="open", conn=conn):
            try:
                markets = [m for m in db.race_markets(race["id"], conn=conn) if m["status"] == "open"]
                if markets:
                    plan.append((markets, fair_values(markets, int(race.get("seats") or 1), conn)))
            except Exception:
                log.exception("fair values failed for race %s", race["id"])
    changed = 0
    for markets, fairs in plan:
        for m in markets:
            try:
                changed += requote_market(house_id, m, fairs[m["id"]],
                                          by_market.get(m["id"], []), positions.get(m["id"], 0))
            except Exception:
                log.exception("requote failed for market %s", m["id"])
    return changed


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
