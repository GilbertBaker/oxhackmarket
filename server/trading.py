"""Trading service layer (COLLAB 7.1). Python/Postgres is the source of truth
for cash and positions; the C++ engine only matches. Every order goes:
validate -> map to YES terms -> risk check -> engine -> one DB transaction.
"""
import logging
import os
import re
from contextlib import contextmanager, nullcontext
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


@contextmanager
def _session(conn=None):
    """Reuse the caller's connection/transaction, or open one. One pooled connection per
    operation instead of one per db call: each checkout costs extra round trips to the cloud DB."""
    if conn is not None:
        yield conn
    else:
        with db.transaction() as c:
            yield c


def _batch(conn):
    """psycopg pipeline: statements that don't return rows go out together in one round
    trip (a fetch forces a sync, so reads stay correct). No-op for the test fake."""
    return conn.pipeline() if hasattr(conn, "pipeline") else nullcontext()


def _risk_inputs(user_id, conn=None):
    user = db.get_user(user_id, conn=conn)
    if user is None:
        raise TradingError("Unknown user")
    positions = db.user_positions(user_id, conn=conn)
    orders = db.open_orders(user_id=user_id, conn=conn)
    return user, positions, orders


def available_cash(user_id, conn=None):
    with _session(conn) as c:
        user, positions, orders = _risk_inputs(user_id, c)
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


# called as fn(market_id, fills) after every committed order with fills (mm_bot keeps its EMA here)
fill_listeners = []


def place_order(user_id, market_id, outcome, side, type, price, qty, *, _trusted_market=None, _conn=None):
    """_trusted_market / _conn are internal (mm_bot only): the caller already holds the open
    market row and a transaction, and the house account skips the risk check (its cash covers
    any quote)."""
    # 1. validate the request itself (no DB needed)
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

    # 2. YES terms
    yside, yprice = _to_yes(outcome, side, price)

    eng = _engine()
    # Lock first, then the transaction, so the commit happens before the lock is released:
    # the next order's risk check must see this order's cash and positions.
    with eng.lock:
        matched = False
        try:
            with _session(_conn) as conn:
                market = _trusted_market or db.get_market(market_id, conn=conn)
                if market is None:
                    raise TradingError("Market not found")
                if market["status"] != "open":
                    raise TradingError(f"Market is {market['status']}, not open for trading")
                # an open market always has a book, even if nobody called open_market (NEW is idempotent)
                eng.new_market(market_id)

                # 3. risk
                if _trusted_market is not None and type == "limit":
                    pass
                elif type == "limit":
                    user, positions, orders = _risk_inputs(user_id, conn)
                    try:
                        risk.check_order(user["cash"], positions, orders, market_id, yside, yprice, qty)
                    except risk.RiskError as e:
                        raise TradingError(str(e))
                else:
                    user, positions, orders = _risk_inputs(user_id, conn)
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
                    order_id = db.next_order_id(conn=conn)
                    fills, resting = eng.limit(market_id, order_id, user_id, yside, yprice, qty)
                    leftover = resting
                else:
                    fills, leftover = eng.market(market_id, user_id, yside, qty)
                    resting = 0
                matched = True

                # 5. persist. Price-time sweep: every maker except the last one touched is fully
                # filled, and so is the last one if the taker has qty left over. Only that last
                # maker needs a DB read (done before the batch, since it returns a row).
                last_left = 0
                if fills and leftover == 0:
                    maker = db.get_order(fills[-1]["maker_order_id"], conn=conn)
                    last_left = (maker["qty_remaining"] if maker else fills[-1]["qty"]) - fills[-1]["qty"]
                top = eng.book(market_id, 1) if fills else None
                order_fields = None
                with _batch(conn):
                    cash_d, pos_d = {}, {}
                    for i, f in enumerate(fills):
                        p, q = f["price"], f["qty"]
                        if yside == "buy":
                            buyer, seller = user_id, f["maker_trader_id"]
                        else:
                            buyer, seller = f["maker_trader_id"], user_id
                        cash_d[buyer] = cash_d.get(buyer, 0) - p * q
                        cash_d[seller] = cash_d.get(seller, 0) + p * q
                        pq, pc = pos_d.get(buyer, (0, 0))
                        pos_d[buyer] = (pq + q, pc + p * q)
                        pq, pc = pos_d.get(seller, (0, 0))
                        pos_d[seller] = (pq - q, pc - p * q)
                        left = last_left if i == len(fills) - 1 else 0
                        db.update_order(f["maker_order_id"], left, "filled" if left <= 0 else "open", conn=conn)
                        db.insert_trade({"market_id": market_id, "price": p, "qty": q,
                                         "buyer_id": buyer, "seller_id": seller, "taker_side": yside,
                                         "maker_order_id": f["maker_order_id"], "taker_order_id": order_id},
                                        conn=conn)
                    # one cash + one position write per user, however many levels were swept
                    for uid, d in cash_d.items():
                        if d:
                            db.adjust_cash(uid, d, conn=conn)
                    for uid, (dq, dc) in pos_d.items():
                        if dq or dc:
                            db.add_position(uid, market_id, dq, dc, conn=conn)
                    if type == "limit":
                        order_fields = {"id": order_id, "market_id": market_id, "user_id": user_id,
                                        "side": yside, "price": yprice, "qty": qty, "qty_remaining": resting,
                                        "status": "open" if resting > 0 else "filled"}
                        db.insert_order(order_fields, conn=conn)
                    if fills:
                        db.record_tick(market_id,
                                       top["bids"][0]["price"] if top["bids"] else None,
                                       top["asks"][0]["price"] if top["asks"] else None,
                                       fills[-1]["price"], conn=conn)
        except TradingError:
            raise
        except Exception:
            if not matched:
                raise
            log.exception("DB write failed after engine match (market %s); resyncing engine", market_id)
            eng.restart()
            raise TradingError("Internal error: order not placed")

        if fills:
            for fn in fill_listeners:
                try:
                    fn(market_id, fills)
                except Exception:
                    log.exception("fill listener failed")

    # 6. result
    order = _order_json(order_fields) if order_fields and resting > 0 else None
    return {"order": order, "fills": [{"price": f["price"], "qty": f["qty"]} for f in fills]}


def cancel_order(user_id, order_id, is_admin=False, *, _order=None, _conn=None):
    """_order / _conn are internal: an open-order row the caller just read under the lock,
    and the caller's transaction."""
    eng = _engine()
    with eng.lock, _session(_conn) as conn:
        o = _order or db.get_order(order_id, conn=conn)
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
        db.update_order(order_id, o["qty_remaining"], "cancelled", conn=conn)


# ------------------------------------------------------- close / resolve
def _cancel_all(market_id, conn):
    eng = _engine()
    orders = db.open_orders(market_id=market_id, conn=conn)
    with _batch(conn):
        for o in orders:
            try:
                eng.cancel(market_id, o["id"])
            except EngineError:
                pass
            db.update_order(o["id"], o["qty_remaining"], "cancelled", conn=conn)


def close_market(market_id, conn=None):
    with _engine().lock, _session(conn) as c:
        m = db.get_market(market_id, conn=c)
        if m is None:
            raise TradingError("Market not found")
        _cancel_all(market_id, c)
        if m["status"] in ("open", "proposed"):
            db.update_market(market_id, status="closed", conn=c)


def close_race(race_id, conn=None):
    with _engine().lock, _session(conn) as c:
        race = db.get_race(race_id, conn=c)
        if race is None:
            raise TradingError("Race not found")
        for m in db.race_markets(race_id, conn=c):
            close_market(m["id"], conn=c)
        if race["status"] in ("open", "proposed"):
            db.update_race(race_id, status="closed", conn=c)


def _settle(market_id, outcome, conn):
    paid = 0
    positions = db.market_positions(market_id, conn=conn)
    with _batch(conn):
        for p in positions:
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
    with _engine().lock, db.transaction() as conn:
        m = db.get_market(market_id, conn=conn)
        if m is None:
            raise TradingError("Market not found")
        if m["status"] not in ("open", "closed"):
            raise TradingError(f"Market is {m['status']}")
        close_market(market_id, conn=conn)
        _settle(market_id, outcome, conn)


def _is_ron(market):
    return market["candidate"].strip().upper().startswith("RON")


def resolve_race(race_id, winner_market_ids):
    try:
        winners = [int(w) for w in winner_market_ids]
    except (TypeError, ValueError):
        raise TradingError("winner_market_ids must be a list of market ids")
    # all in ONE transaction: a failure leaves nothing half-closed or half-paid
    with _engine().lock, db.transaction() as conn:
        race = db.get_race(race_id, conn=conn)
        if race is None:
            raise TradingError("Race not found")
        if race["status"] not in ("open", "closed"):
            raise TradingError(f"Race is {race['status']}")
        markets = db.race_markets(race_id, conn=conn)
        by_id = {m["id"]: m for m in markets}
        if len(set(winners)) != len(winners):
            raise TradingError("Duplicate winners")
        for w in winners:
            if w not in by_id:
                raise TradingError(f"Market {w} is not in this race")
        if race.get("kind") == "binary":
            # one 'Yes' market: winners [its id] = YES, [] = NO
            if len(winners) > 1:
                raise TradingError("A yes/no market has at most one winner")
        else:
            ron_only = len(winners) == 1 and _is_ron(by_id[winners[0]])
            if len(winners) != int(race["seats"]) and not ron_only:
                raise TradingError(f"Need exactly {race['seats']} winner(s), got {len(winners)}")

        close_race(race_id, conn=conn)
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


_MATCH = re.compile(r"^(?P<comp>.+?):\s*(?P<home>.+?)\s+vs\.?\s+(?P<away>.+)$", re.IGNORECASE)


def parse_match(position):
    """'<Competition>: <Home> vs <Away>' -> (comp, home, away), else None (COLLAB 14, Cuppers)."""
    m = _MATCH.match((position or "").strip())
    return (m["comp"].strip(), m["home"].strip(), m["away"].strip()) if m else None


def question_for(candidate, position, election, kind=None):
    """Question text for one outcome market. kind 'binary': the position IS the question.
    'multi': '<position>: <candidate>?'. 'match' / None: Cuppers or Union wording."""
    if kind == "binary":
        return position
    if kind == "multi":
        return f"{position.rstrip('?').rstrip()}: {candidate}?"
    match = parse_match(position)
    if match:
        comp, home, away = match
        if candidate.lower() == "draw":
            return f"Will {home} vs {away} end in a draw ({comp}, {election})?"
        if candidate.lower() == home.lower():
            return f"Will {home} beat {away} ({comp}, {election})?"
        if candidate.lower() == away.lower():
            return f"Will {away} beat {home} ({comp}, {election})?"
        return f"Will {candidate} win {position} ({election})?"
    return f"Will {candidate} be elected {position} ({election})?"


def add_candidate(race_id, name, prior=None, conn=None, **extra):
    """Create one candidate market in a race (opened in the engine if the race is open and
    no caller transaction is given). Returns the market id. Raises TradingError if the race
    is missing or the name is taken."""
    with _session(conn) as c:
        race = db.get_race(race_id, conn=c)
        if race is None:
            raise TradingError("Race not found")
        name = (name or "").strip()
        if not name:
            raise TradingError("Candidate name required")
        if any(m["candidate"].lower() == name.lower() for m in db.race_markets(race_id, conn=c)):
            raise TradingError(f"{name} is already a candidate")
        if prior is not None:
            prior = max(1, min(99, int(prior)))
        fields = {"race_id": race_id, "candidate": name,
                  "question": question_for(name, race["position"], race["election"], race.get("kind")),
                  "resolution_criteria": race["resolution_criteria"],
                  "category": race.get("category")
                  or ("cuppers" if parse_match(race["position"]) else "union"),
                  "closes_at": race["closes_at"], "status": race["status"], "ai_prior": prior,
                  "source_name": race.get("source_name"), "source_url": race.get("source_url"),
                  "article_id": race.get("article_id")}
        fields.update(extra)
        market_id = db.create_market(fields, conn=c)
    if fields["status"] == "open" and conn is None:
        open_market(market_id)
    return market_id


BINARY_NAME = "Yes"


def create_race(election, position, closes_at, resolution_criteria, candidates=None, seats=1,
                status="open", add_ron=False, kind="match", category=None, **extra):
    """One call to make a whole race in ONE transaction: the race row, one market per
    candidate (question text + category derived from the position), and (if status ==
    'open') the engine books. candidates = [{"name", "prior"}] or [names].
    Cuppers (COLLAB 14): position "<Competition>: <Home> vs <Away>", candidates Home /
    Draw / Away, no RON. add_ron=True restores the old Union-election behaviour.
    kind: 'match' (default), 'multi' (any named outcomes, `seats` winners) or 'binary' (one
    YES/NO question: a single market named 'Yes'; candidates may be omitted or give its prior).
    category: races/markets category (default 'cuppers' for a parseable match, else 'other').
    extra = any other races column (description, source_name, ...). Returns the race id."""
    if status not in ("proposed", "open"):
        raise TradingError("status must be proposed or open")
    if kind not in ("match", "multi", "binary"):
        raise TradingError("kind must be match, multi or binary")
    if kind == "binary":
        given = [c if isinstance(c, dict) else {"name": c} for c in (candidates or [])]
        if len(given) > 1:
            raise TradingError("A yes/no market has exactly one outcome")
        candidates = [{"name": BINARY_NAME, "prior": given[0].get("prior") if given else 50}]
        seats, add_ron = 1, False
    if category is None:
        category = "cuppers" if kind == "match" and parse_match(position) else "other"
    if not candidates:
        raise TradingError("At least one candidate required")
    seats = int(seats)
    if seats < 1:
        raise TradingError("seats must be >= 1")
    cands = [c if isinstance(c, dict) else {"name": c} for c in candidates]
    names = [c["name"].strip().lower() for c in cands]
    if len(set(names)) != len(names):
        raise TradingError("Duplicate candidate names")
    if add_ron and not any(n.upper().startswith("RON") for n in names):
        cands.append({"name": RON_NAME, "prior": RON_PRIOR})
    fields = {"election": election, "position": position, "seats": seats,
              "closes_at": _parse_time(closes_at), "resolution_criteria": resolution_criteria,
              "status": status, "kind": kind, "category": category}
    fields.update(extra)
    with db.transaction() as conn:
        existing = db.find_race(election, position, conn=conn)
        if existing:
            raise TradingError(f"Race already exists (id {existing['id']})")
        race_id = db.create_race(fields, conn=conn)
        market_ids = [add_candidate(race_id, c["name"], c.get("prior"), conn=conn) for c in cands]
    if status == "open":
        for mid in market_ids:   # after commit, so a rolled-back race never has books
            open_market(mid)
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


def create_race_from_spec(spec, article=None, status="proposed", created_by_ai=False):
    """A market proposal ({question, kind, prior | outcomes, closes_at, resolution_criteria,
    category, reason}, e.g. from ai.propose_market) -> race id, in the current term and
    linked to its source article. Raises TradingError on a bad spec."""
    kind = spec.get("kind") or "binary"
    question = str(spec.get("question") or "").strip()
    if not question:
        raise TradingError("question required")
    try:
        closes_at = _parse_time(spec.get("closes_at"))
    except (TypeError, ValueError):
        raise TradingError("closes_at must be an ISO timestamp")
    if closes_at is None:
        raise TradingError("closes_at required")
    if closes_at.tzinfo is None:
        closes_at = closes_at.replace(tzinfo=timezone.utc)
    if closes_at <= _now():
        raise TradingError("closes_at is in the past")
    if kind == "binary":
        cands = [{"name": BINARY_NAME, "prior": int(spec.get("prior") or 50)}]
    else:
        cands = [{"name": str(o["name"]).strip(), "prior": int(o.get("prior") or 0) or None}
                 for o in spec.get("outcomes") or []]
        if len(cands) < 2:
            raise TradingError("a multi-outcome market needs at least 2 outcomes")
    term = db.current_term()
    criteria = str(spec.get("resolution_criteria") or "").strip() or \
        db.get_setting("defaults.criteria.generic", "Resolves by admin decision.")
    extra = {"description": spec.get("description"), "ai_reason": spec.get("reason"),
             "created_by_ai": bool(created_by_ai)}
    if article:
        extra.update(source_name=article.get("source"), source_url=article.get("url"),
                     article_id=article.get("id"))
    return create_race(term["name"] if term else "General", question, closes_at, criteria, cands,
                       status=status, kind=kind, category=spec.get("category") or "news", **extra)


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
