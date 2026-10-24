"""In-memory stand-in for db.py (only the functions trading/mm_bot/trading_api use).
No tests in here; imported by the other test_trading* files."""
import copy
import itertools
from contextlib import contextmanager
from datetime import datetime, timezone


class FakeDB:
    def __init__(self):
        self.users, self.races, self.markets, self.orders = {}, {}, {}, {}
        self.positions = {}   # (user_id, market_id) -> {"qty","net_cost"}
        self.trades, self.ticks = [], []
        self._ids = itertools.count(1)
        self._order_ids = itertools.count(1000)
        self.fail_next_transaction = False

    # ---------------------------------------------------------- helpers
    def _state(self):
        return (self.users, self.races, self.markets, self.orders, self.positions, self.trades, self.ticks)

    def add_user(self, username, cash=100000, is_admin=False, is_bot=False):
        uid = next(self._ids)
        self.users[uid] = {"id": uid, "username": username, "cash": cash, "is_admin": is_admin,
                           "is_bot": is_bot, "pw_hash": "x"}
        return uid

    def add_race(self, candidates, seats=1, status="open", priors=None):
        rid = next(self._ids)
        self.races[rid] = {"id": rid, "election": "Michaelmas 2026", "position": f"Pos{rid}", "seats": seats,
                           "status": status, "closes_at": datetime(2026, 11, 27, tzinfo=timezone.utc),
                           "resolution_criteria": "x", "resolved_at": None}
        ids = []
        for i, c in enumerate(candidates):
            ids.append(self.add_market(rid, c, status, (priors or [None] * len(candidates))[i]))
        return rid, ids

    def add_market(self, race_id, candidate, status="open", ai_prior=None):
        mid = next(self._ids)
        self.markets[mid] = {"id": mid, "race_id": race_id, "candidate": candidate,
                             "question": f"Will {candidate} win?", "status": status, "outcome": None,
                             "ai_prior": ai_prior, "created_at": datetime.now(timezone.utc),
                             "resolved_at": None}
        return mid

    # ------------------------------------------------------- db.py API
    @contextmanager
    def transaction(self):
        snap = copy.deepcopy(self._state())
        try:
            if self.fail_next_transaction:
                self.fail_next_transaction = False
                raise RuntimeError("simulated DB failure")
            yield object()
        except Exception:
            (self.users, self.races, self.markets, self.orders, self.positions, self.trades, self.ticks) = snap
            raise

    def ensure_bot_user(self):
        for u in self.users.values():
            if u["username"] == "house":
                return u["id"]
        return self.add_user("house", cash=100000000, is_bot=True)

    def get_user(self, uid):
        return dict(self.users[uid]) if uid in self.users else None

    def adjust_cash(self, uid, delta, conn=None):
        self.users[uid]["cash"] += delta

    def get_race(self, rid):
        return dict(self.races[rid]) if rid in self.races else None

    def list_races(self, status=None):
        return [dict(r) for r in self.races.values() if status in (None, "all", r["status"])]

    def update_race(self, rid, conn=None, **fields):
        self.races[rid].update(fields)
        if "status" in fields:
            for m in self.markets.values():
                if m["race_id"] == rid:
                    m["status"] = fields["status"]

    def race_markets(self, rid):
        return [dict(m, price=self.current_price(m["id"])) for m in self.markets.values() if m["race_id"] == rid]

    def get_market(self, mid):
        return dict(self.markets[mid]) if mid in self.markets else None

    def list_markets(self, status=None, category=None, race_id=None):
        return [dict(m) for m in self.markets.values()
                if status in (None, m["status"]) and race_id in (None, m["race_id"])]

    def update_market(self, mid, conn=None, **fields):
        self.markets[mid].update(fields)

    def current_price(self, mid):
        for t in reversed(self.ticks):
            if t["market_id"] == mid and t["best_bid"] and t["best_ask"]:
                return round((t["best_bid"] + t["best_ask"]) / 2)
        p = self.last_trade_price(mid)
        if p is not None:
            return p
        return self.markets[mid].get("ai_prior") or 50

    def next_order_id(self):
        return next(self._order_ids)

    def insert_order(self, fields, conn=None):
        self.orders[fields["id"]] = dict(fields, created_at=datetime.now(timezone.utc))

    def update_order(self, oid, qty_remaining, status, conn=None):
        self.orders[oid].update(qty_remaining=qty_remaining, status=status)

    def get_order(self, oid):
        return dict(self.orders[oid]) if oid in self.orders else None

    def open_orders(self, market_id=None, user_id=None):
        return [dict(o) for o in self.orders.values() if o["status"] == "open"
                and market_id in (None, o["market_id"]) and user_id in (None, o["user_id"])]

    def get_position(self, uid, mid, conn=None):
        p = self.positions.get((uid, mid), {"qty": 0, "net_cost": 0})
        return {"user_id": uid, "market_id": mid, **p}

    def user_positions(self, uid):
        return [{"user_id": u, "market_id": m, **p} for (u, m), p in self.positions.items() if u == uid]

    def market_positions(self, mid, conn=None):
        return [{"user_id": u, "market_id": m, **p} for (u, m), p in self.positions.items() if m == mid]

    def add_position(self, uid, mid, dq, dc, conn=None):
        p = self.positions.setdefault((uid, mid), {"qty": 0, "net_cost": 0})
        p["qty"] += dq
        p["net_cost"] += dc

    def insert_trade(self, fields, conn=None):
        self.trades.append(dict(fields, time=datetime.now(timezone.utc)))

    def record_tick(self, mid, best_bid, best_ask, last_price, conn=None):
        self.ticks.append({"market_id": mid, "best_bid": best_bid, "best_ask": best_ask, "last_price": last_price})

    def recent_trades(self, mid, limit=50):
        return [t for t in reversed(self.trades) if t["market_id"] == mid][:limit]

    def last_trade_price(self, mid):
        t = self.recent_trades(mid, 1)
        return t[0]["price"] if t else None

    def equity(self, uid):
        return self.users[uid]["cash"] + sum(p["qty"] * self.current_price(m)
                                             for (u, m), p in self.positions.items() if u == uid)
