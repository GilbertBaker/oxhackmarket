"""Trading service layer (COLLAB 7.1). Python/Postgres is the source of truth
for cash and positions; the C++ engine only matches. Every order goes:
validate -> map to YES terms -> risk check -> engine -> one DB transaction.
"""
import logging
import os
from datetime import datetime, timezone

import db
import risk
from engine import EngineError, get_engine

log = logging.getLogger("trading")

MAX_QTY = 100000
OUTCOMES = ("YES", "NO")
SIDES = ("buy", "sell")
TYPES = ("limit", "market")


class TradingError(Exception):
    pass


def _now():
    return datetime.now(timezone.utc)


def _engine():
    return get_engine()


# ---------------------------------------------------------------- lifecycle
def _replay(eng):
    """Rebuild every book from the DB: NEW for open/closed markets, then every
    open order with its original id, oldest first."""
    n_mkts = 0
    for status in ("open", "closed"):
        for m in db.list_markets(status=status):
            eng.new_market(m["id"])
            n_mkts += 1
    orders = sorted(db.open_orders(), key=lambda o: (o.get("created_at") or _now(), o["id"]))
    for o in orders:
        try:
            fills, resting = eng.limit(o["market_id"], o["id"], o["user_id"], o["side"], o["price"], o["qty_remaining"])
            if fills:
                log.error("replay of order %s produced fills %s (book was crossed in DB)", o["id"], fills)
        except EngineError as e:
            log.error("replay of order %s failed: %s", o["id"], e.reason)
    log.info("engine replay: %d markets, %d open orders", n_mkts, len(orders))


def startup():
    eng = _engine()
    eng.replay_hook = _replay
    with eng.lock:
        if not eng.running():
            eng.start()
        _replay(eng)


def start_background():
    if os.environ.get("DISABLE_JOBS") == "1":
        log.info("DISABLE_JOBS=1: market maker not started")
        return
    import mm_bot
    mm_bot.start()


def open_market(market_id):
    _engine().new_market(market_id)


def open_race(race_id):
    for m in db.race_markets(race_id):
        open_market(m["id"])


# --------------------------------------------------------------- read side
def get_book(market_id, depth=10):
    try:
        return _engine().book(market_id, depth)
    except EngineError as e:
        if e.reason == "unknown_market":
            return {"bids": [], "asks": []}
        raise


def _risk_inputs(user_id):
    user = db.get_user(user_id)
    if user is None:
        raise TradingError("Unknown user")
    positions = db.user_positions(user_id)
    orders = db.open_orders(user_id=user_id)
    return user, positions, orders


def available_cash(user_id):
    user, positions, orders = _risk_inputs(user_id)
    return risk.available(user["cash"], positions, orders)


# -------------------------------------------------------------- placement
def _to_yes(outcome, side, price):
    """Buy NO @ p == Sell YES @ (100-p); Sell NO @ p == Buy YES @ (100-p)."""
    if outcome == "YES":
        return side, price
    flipped = "sell" if side == "buy" else "buy"
    return flipped, (None if price is None else 100 - price)


def _order_json(fields):
    return {k: fields[k] for k in ("id", "market_id", "side", "price", "qty", "qty_remaining", "status")} | {
        "created_at": fields.get("created_at") or _now()}


def place_order(user_id, market_id, outcome, side, type, price, qty):
    # 1. validate
    outcome = (outcome or "").upper()
    if outcome not in OUTCOMES:
        raise TradingError("outcome must be YES or NO")
    if side not in SIDES:
        raise TradingError("side must be buy or sell")
    if type not in TYPES:
        raise TradingError("type must be limit or market")
    try:
        qty = int(qty)
    except (TypeError, ValueError):
        raise TradingError("qty must be an integer")
    if not 1 <= qty <= MAX_QTY:
        raise TradingError(f"qty must be between 1 and {MAX_QTY}")
    if type == "limit":
        try:
            price = int(price)
        except (TypeError, ValueError):
            raise TradingError("price must be an integer")
        if not 1 <= price <= 99:
            raise TradingError("price must be between 1 and 99")
    else:
        price = None
    market = db.get_market(market_id)
    if market is None:
        raise TradingError("Market not found")
    if market["status"] != "open":
        raise TradingError(f"Market is {market['status']}, not open for trading")

    # 2. YES terms
    yside, yprice = _to_yes(outcome, side, price)

    eng = _engine()
    with eng.lock:
        # an open market always has a book, even if nobody called open_market (NEW is idempotent)
        eng.new_market(market_id)

        # 3. risk
        user, positions, orders = _risk_inputs(user_id)
        if type == "limit":
            try:
                risk.check_order(user["cash"], positions, orders, market_id, yside, yprice, qty)
            except risk.RiskError as e:
                raise TradingError(str(e))
        else:
            book = eng.book(market_id, 1000)
            levels = book["asks"] if yside == "buy" else book["bids"]
            affordable, fillable = risk.max_affordable_market_qty(
                user["cash"], positions, orders, market_id, yside, levels, qty)
            if fillable == 0:
                raise TradingError("No liquidity")
            if affordable == 0:
                raise TradingError("Insufficient funds")
            qty = affordable

        # 4. engine
        order_id = None
        if type == "limit":
            order_id = db.next_order_id()
            fills, resting = eng.limit(market_id, order_id, user_id, yside, yprice, qty)
        else:
            fills, _unfilled = eng.market(market_id, user_id, yside, qty)
            resting = 0

        # 5. persist
        order_fields = None
        try:
            with db.transaction() as conn:
                for f in fills:
                    p, q = f["price"], f["qty"]
                    if yside == "buy":
                        buyer, seller = user_id, f["maker_trader_id"]
                    else:
                        buyer, seller = f["maker_trader_id"], user_id
                    db.adjust_cash(buyer, -p * q, conn=conn)
                    db.adjust_cash(seller, p * q, conn=conn)
                    db.add_position(buyer, market_id, q, p * q, conn=conn)
                    db.add_position(seller, market_id, -q, -p * q, conn=conn)
                    maker = db.get_order(f["maker_order_id"])
                    left = (maker["qty_remaining"] if maker else q) - q
                    db.update_order(f["maker_order_id"], left, "filled" if left <= 0 else "open", conn=conn)
                    db.insert_trade({"market_id": market_id, "price": p, "qty": q,
                                     "buyer_id": buyer, "seller_id": seller, "taker_side": yside,
                                     "maker_order_id": f["maker_order_id"], "taker_order_id": order_id},
                                    conn=conn)
                if type == "limit":
                    order_fields = {"id": order_id, "market_id": market_id, "user_id": user_id,
                                    "side": yside, "price": yprice, "qty": qty, "qty_remaining": resting,
                                    "status": "open" if resting > 0 else "filled"}
                    db.insert_order(order_fields, conn=conn)
                if fills:
                    top = eng.book(market_id, 1)
                    db.record_tick(market_id,
                                   top["bids"][0]["price"] if top["bids"] else None,
                                   top["asks"][0]["price"] if top["asks"] else None,
                                   fills[-1]["price"], conn=conn)
        except Exception:
            log.exception("DB write failed after engine match (market %s, order %s); resyncing engine",
                          market_id, order_id)
            eng.restart()
            raise TradingError("Internal error: order not placed")

    # 6. result
    order = _order_json(order_fields) if order_fields and resting > 0 else None
    return {"order": order, "fills": [{"price": f["price"], "qty": f["qty"]} for f in fills]}


def cancel_order(user_id, order_id, is_admin=False):
    eng = _engine()
    with eng.lock:
        o = db.get_order(order_id)
        if o is None:
            raise TradingError("Order not found")
        if o["user_id"] != user_id and not is_admin:
            raise TradingError("Not your order")
        if o["status"] != "open":
            raise TradingError(f"Order is {o['status']}")
        try:
            eng.cancel(o["market_id"], order_id)
        except EngineError as e:
            if e.reason != "unknown_market":
                raise TradingError(f"Engine error: {e.reason}")
        db.update_order(order_id, o["qty_remaining"], "cancelled")


# ------------------------------------------------------- close / resolve
def _cancel_all(market_id):
    eng = _engine()
    for o in db.open_orders(market_id=market_id):
        try:
            eng.cancel(market_id, o["id"])
        except EngineError:
            pass
        db.update_order(o["id"], o["qty_remaining"], "cancelled")


def close_market(market_id):
    eng = _engine()
    with eng.lock:
        m = db.get_market(market_id)
        if m is None:
            raise TradingError("Market not found")
        _cancel_all(market_id)
        if m["status"] in ("open", "proposed"):
            db.update_market(market_id, status="closed")


def close_race(race_id):
    race = db.get_race(race_id)
    if race is None:
        raise TradingError("Race not found")
    with _engine().lock:
        for m in db.race_markets(race_id):
            close_market(m["id"])
        if race["status"] in ("open", "proposed"):
            db.update_race(race_id, status="closed")


def _settle(market_id, outcome, conn):
    paid = 0
    for p in db.market_positions(market_id, conn=conn):
        q = int(p["qty"])
        if q == 0:
            continue
        if outcome == "YES":
            db.adjust_cash(p["user_id"], 100 * q, conn=conn)
            paid += 100 * q
        db.add_position(p["user_id"], market_id, -q, 0, conn=conn)
    db.update_market(market_id, status="resolved", outcome=outcome, resolved_at=_now(), conn=conn)
    log.info("market %s resolved %s, net paid %d", market_id, outcome, paid)


def resolve_market(market_id, outcome):
    outcome = (outcome or "").upper()
    if outcome not in OUTCOMES:
        raise TradingError("outcome must be YES or NO")
    m = db.get_market(market_id)
    if m is None:
        raise TradingError("Market not found")
    if m["status"] not in ("open", "closed"):
        raise TradingError(f"Market is {m['status']}")
    with _engine().lock:
        close_market(market_id)
        with db.transaction() as conn:
            _settle(market_id, outcome, conn)


def _is_ron(market):
    return market["candidate"].strip().upper().startswith("RON")


def resolve_race(race_id, winner_market_ids):
    race = db.get_race(race_id)
    if race is None:
        raise TradingError("Race not found")
    if race["status"] not in ("open", "closed"):
        raise TradingError(f"Race is {race['status']}")
    markets = db.race_markets(race_id)
    by_id = {m["id"]: m for m in markets}
    try:
        winners = [int(w) for w in winner_market_ids]
    except (TypeError, ValueError):
        raise TradingError("winner_market_ids must be a list of market ids")
    if len(set(winners)) != len(winners):
        raise TradingError("Duplicate winners")
    for w in winners:
        if w not in by_id:
            raise TradingError(f"Market {w} is not in this race")
    ron_only = len(winners) == 1 and _is_ron(by_id[winners[0]])
    if len(winners) != int(race["seats"]) and not ron_only:
        raise TradingError(f"Need exactly {race['seats']} winner(s), got {len(winners)}")

    with _engine().lock:
        close_race(race_id)
        with db.transaction() as conn:
            for m in markets:
                if m["status"] == "resolved":
                    continue
                _settle(m["id"], "YES" if m["id"] in winners else "NO", conn)
            db.update_race(race_id, status="resolved", resolved_at=_now(), conn=conn)


# ------------------------------------------- market creation (for B seed + C admin)
RON_NAME = "RON (Re-Open Nominations)"
RON_PRIOR = 5


def _parse_time(v):
    if isinstance(v, str):
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    return v


def question_for(candidate, position, election):
    return f"Will {candidate} be elected {position} ({election})?"


def add_candidate(race_id, name, prior=None, **extra):
    """Create one candidate market in a race (opened in the engine if the race is open).
    Returns the market id. Raises TradingError if the race is missing or the name is taken."""
    race = db.get_race(race_id)
    if race is None:
        raise TradingError("Race not found")
    name = (name or "").strip()
    if not name:
        raise TradingError("Candidate name required")
    if any(m["candidate"].lower() == name.lower() for m in db.race_markets(race_id)):
        raise TradingError(f"{name} is already a candidate")
    if prior is not None:
        prior = max(1, min(99, int(prior)))
    fields = {"race_id": race_id, "candidate": name,
              "question": question_for(name, race["position"], race["election"]),
              "resolution_criteria": race["resolution_criteria"], "category": "union",
              "closes_at": race["closes_at"], "status": race["status"], "ai_prior": prior,
              "source_name": race.get("source_name"), "source_url": race.get("source_url"),
              "article_id": race.get("article_id")}
    fields.update(extra)
    market_id = db.create_market(fields)
    if fields["status"] == "open":
        open_market(market_id)
    return market_id


def create_race(election, position, closes_at, resolution_criteria, candidates, seats=1,
                status="open", add_ron=True, **extra):
    """One call to make a whole race: the race row, one market per candidate, RON, and
    (if status == 'open') the engine books. candidates = [{"name", "prior"}] or [names].
    extra = any other races column (description, source_name, source_url, article_id,
    ai_reason, created_by_ai). Returns the race id."""
    if status not in ("proposed", "open"):
        raise TradingError("status must be proposed or open")
    if not candidates:
        raise TradingError("At least one candidate required")
    seats = int(seats)
    if seats < 1:
        raise TradingError("seats must be >= 1")
    existing = db.find_race(election, position)
    if existing:
        raise TradingError(f"Race already exists (id {existing['id']})")
    cands = [c if isinstance(c, dict) else {"name": c} for c in candidates]
    if add_ron and not any(c["name"].strip().upper().startswith("RON") for c in cands):
        cands.append({"name": RON_NAME, "prior": RON_PRIOR})
    fields = {"election": election, "position": position, "seats": seats,
              "closes_at": _parse_time(closes_at), "resolution_criteria": resolution_criteria,
              "status": status}
    fields.update(extra)
    race_id = db.create_race(fields)
    for c in cands:
        add_candidate(race_id, c["name"], c.get("prior"))
    log.info("race %s created: %s %s, %d candidates, status %s",
             race_id, election, position, len(cands), status)
    return race_id


def approve_race(race_id):
    """proposed -> open: status on the race and all its markets, then open the books."""
    race = db.get_race(race_id)
    if race is None:
        raise TradingError("Race not found")
    if race["status"] not in ("proposed", "open"):
        raise TradingError(f"Race is {race['status']}")
    db.update_race(race_id, status="open")
    open_race(race_id)


def reject_race(race_id):
    race = db.get_race(race_id)
    if race is None:
        raise TradingError("Race not found")
    if race["status"] != "proposed":
        raise TradingError(f"Race is {race['status']}, only proposed races can be rejected")
    db.update_race(race_id, status="rejected")


# --------------------------------------------- live market info (for B's tick job)
def market_snapshot(market_id):
    """{"market_id","best_bid","best_ask","last_price"}; bid/ask None when that side is empty."""
    top = get_book(market_id, 1)
    return {"market_id": market_id,
            "best_bid": top["bids"][0]["price"] if top["bids"] else None,
            "best_ask": top["asks"][0]["price"] if top["asks"] else None,
            "last_price": db.last_trade_price(market_id)}


def snapshot_open_markets():
    return [market_snapshot(m["id"]) for m in db.list_markets(status="open")]


def record_ticks():
    """Write one price_tick per open market. Convenience for B's 15 s job. Returns count."""
    snaps = snapshot_open_markets()
    for s in snaps:
        db.record_tick(s["market_id"], s["best_bid"], s["best_ask"], s["last_price"])
    return len(snaps)


# ------------------------------------------------------------------ invariants
def check_invariants():
    """Returns a list of human-readable problems ([] = all good):
    per-market positions sum to 0, no negative available cash, engine book == DB open orders."""
    problems = []
    eng = _engine()
    with eng.lock:
        markets = db.list_markets(status="open") + db.list_markets(status="closed") \
            + db.list_markets(status="resolved")
        for m in markets:
            total = sum(int(p["qty"]) for p in db.market_positions(m["id"]))
            if total != 0:
                problems.append(f"market {m['id']}: positions sum to {total}")
            if m["status"] == "resolved":
                continue
            want = {"buy": {}, "sell": {}}
            for o in db.open_orders(market_id=m["id"]):
                lv = want[o["side"]]
                lv[o["price"]] = lv.get(o["price"], 0) + int(o["qty_remaining"])
            book = get_book(m["id"], 1000)
            have_b = {l["price"]: l["qty"] for l in book["bids"]}
            have_s = {l["price"]: l["qty"] for l in book["asks"]}
            if have_b != want["buy"] or have_s != want["sell"]:
                problems.append(f"market {m['id']}: engine book {have_b}/{have_s} != DB {want['buy']}/{want['sell']}")
        for u in db.list_users(include_bots=True):
            avail = available_cash(u["id"])
            if avail < 0:
                problems.append(f"user {u['id']} ({u['username']}): available cash {avail}")
    return problems
