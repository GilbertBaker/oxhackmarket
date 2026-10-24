"""Live trading test against the REAL Timescale db (oxmarket schema) and the real C++ engine.
Opt-in:  TRADING_LIVE=1 python -m pytest server/tests/test_trading_live.py -s

Creates its own race/users tagged "TESTA<n>", only quotes that race (never touches other
races' books), and deletes exactly the rows it created afterwards, restoring the house's cash.
"""
import importlib.util
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
pytestmark = pytest.mark.skipif(os.environ.get("TRADING_LIVE") != "1",
                                reason="set TRADING_LIVE=1 to run against the real database")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(SERVER.parent / ".env")

import engine  # noqa: E402
import mm_bot  # noqa: E402
import trading  # noqa: E402


def _real_db():
    """The real db module, even if another test module put a fake under sys.modules['db']."""
    mod = sys.modules.get("db")
    if mod is not None and getattr(mod, "__file__", None) == str(SERVER / "db.py"):
        return mod
    spec = importlib.util.spec_from_file_location("db_real", SERVER / "db.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def live():
    db = _real_db()
    saved = {m: m.db for m in (trading, mm_bot)}
    for m in saved:
        m.db = db
    eng = engine.Engine()
    assert os.path.exists(eng.path), "build MarketServer.exe first"
    engine._engine = eng
    trading.startup()

    tag = f"TESTA{random.randint(10000, 99999)}"
    house = db.ensure_bot_user()
    house_cash = db.get_user(house)["cash"]
    users = [db.create_user(f"{tag}_{i}", "!test")["id"] for i in range(3)]
    # Cuppers-style match: markets are [Alpha (home), Draw, Beta (away)]
    race_id = trading.create_race(f"TEST A {tag}", "Test Cuppers: Alpha vs Beta",
                                  datetime.now(timezone.utc) + timedelta(days=30), "test",
                                  [{"name": "Alpha", "prior": 50}, {"name": "Draw", "prior": 15},
                                   {"name": "Beta", "prior": 35}])
    markets = [m["id"] for m in db.race_markets(race_id)]
    try:
        yield {"db": db, "eng": eng, "house": house, "users": users, "race": race_id, "markets": markets}
    finally:
        with db.transaction() as c:
            S = db.SCHEMA
            for m in markets:
                for o in db.open_orders(market_id=m, conn=c):
                    eng.cancel(m, o["id"])
            c.execute(f"DELETE FROM {S}.trades WHERE market_id = ANY(%s)", (markets,))
            c.execute(f"DELETE FROM {S}.price_ticks WHERE market_id = ANY(%s)", (markets,))
            c.execute(f"DELETE FROM {S}.orders WHERE market_id = ANY(%s) OR user_id = ANY(%s)", (markets, users))
            c.execute(f"DELETE FROM {S}.positions WHERE market_id = ANY(%s) OR user_id = ANY(%s)", (markets, users))
            c.execute(f"DELETE FROM {S}.portfolio_snapshots WHERE user_id = ANY(%s)", (users,))
            c.execute(f"DELETE FROM {S}.markets WHERE id = ANY(%s)", (markets,))
            c.execute(f"DELETE FROM {S}.races WHERE id = %s", (race_id,))
            c.execute(f"DELETE FROM {S}.users WHERE id = ANY(%s)", (users,))
            drift = db.get_user(house, conn=c)["cash"] - house_cash
            db.adjust_cash(house, -drift, conn=c)
        eng.stop()
        engine._engine = None
        for m, d in saved.items():
            m.db = d


def _quote_race(live):
    db = live["db"]
    ms = [m for m in db.race_markets(live["race"]) if m["status"] == "open"]
    fairs = mm_bot.fair_values(ms, 1)
    return sum(mm_bot.requote_market(live["house"], m, fairs[m["id"]]) for m in ms)


def _check_mine(live):
    db, problems = live["db"], []
    for m in live["markets"]:
        total = sum(int(p["qty"]) for p in db.market_positions(m))
        if total:
            problems.append(f"market {m}: positions sum {total}")
        want = {}
        for o in db.open_orders(market_id=m):
            if o["side"] == "buy":
                want[o["price"]] = want.get(o["price"], 0) + o["qty_remaining"]
        have = {lvl["price"]: lvl["qty"] for lvl in trading.get_book(m, 100)["bids"]}
        if have != want:
            problems.append(f"market {m}: engine bids {have} != db {want}")
    for u in live["users"]:
        if trading.available_cash(u) < 0:
            problems.append(f"user {u}: negative available")
    return problems


def _t(f):
    t = time.perf_counter()
    r = f()
    return r, (time.perf_counter() - t) * 1000


def test_live_trading_round_trip(live):
    db, eng = live["db"], live["eng"]
    alice, bob, ron = live["markets"]   # Alpha, Draw, Beta
    u1, u2, u3 = live["users"]
    timings = {}

    t0 = time.perf_counter()
    db.get_user(u1)
    timings["one db round trip"] = (time.perf_counter() - t0) * 1000

    n, timings["mm quote race (3 markets, new)"] = _t(lambda: _quote_race(live))
    assert n == 3
    _, timings["mm pass, nothing changed"] = _t(lambda: _quote_race(live))
    book = trading.get_book(alice)
    assert book["bids"] and book["asks"]
    mids = [sum(x[0]["price"] for x in (trading.get_book(m)["bids"], trading.get_book(m)["asks"])) / 2
            for m in live["markets"]]
    assert abs(sum(mids) - 100) <= 4, mids

    # resting limit, then YES vs NO crossing between two users (house quotes on Bob pulled first,
    # otherwise the NO order correctly fills against the house's better bid)
    for o in db.open_orders(market_id=bob, user_id=live["house"]):
        trading.cancel_order(live["house"], o["id"], is_admin=True)
    r, timings["resting limit order"] = _t(lambda: trading.place_order(u1, bob, "YES", "buy", "limit", 5, 10))
    assert r["order"]["qty_remaining"] == 10
    cash1, cash2 = db.get_user(u1)["cash"], db.get_user(u2)["cash"]
    trading.place_order(u1, bob, "YES", "buy", "limit", 10, 10)
    r, timings["crossing order (NO vs YES)"] = _t(lambda: trading.place_order(u2, bob, "NO", "buy", "limit", 95, 10))
    assert r["fills"] == [{"price": 10, "qty": 10}], r
    assert db.get_user(u1)["cash"] == cash1 - 100 and db.get_user(u2)["cash"] == cash2 + 100
    assert db.get_position(u1, bob)["qty"] == 10 and db.get_position(u2, bob)["qty"] == -10

    # market order against the house's ask
    r, timings["market order vs house"] = _t(lambda: trading.place_order(u3, alice, "YES", "buy", "market", None, 20))
    assert sum(f["qty"] for f in r["fills"]) == 20
    assert _check_mine(live) == []

    # engine crash -> replay from the real DB
    eng.kill()
    _, timings["order after engine crash (incl. replay)"] = _t(
        lambda: trading.place_order(u3, alice, "YES", "buy", "limit", 3, 1))
    assert _check_mine(live) == []

    # close + resolve: Alpha wins
    trading.close_race(live["race"])
    with pytest.raises(trading.TradingError):
        trading.place_order(u1, alice, "YES", "buy", "limit", 50, 1)
    before = {u: db.get_user(u)["cash"] for u in (u1, u2, u3)}
    _, timings["resolve race"] = _t(lambda: trading.resolve_race(live["race"], [alice]))
    assert db.get_user(u3)["cash"] == before[u3] + 20 * 100      # 20 Alpha YES pay 100 each
    assert db.get_user(u1)["cash"] == before[u1]                 # Draw YES pays 0
    assert db.get_user(u2)["cash"] == before[u2]                 # short Draw: NO wins, nothing owed
    assert all(db.get_market(m)["status"] == "resolved" for m in live["markets"])
    assert db.get_race(live["race"])["status"] == "resolved"
    assert all(db.get_position(u, m)["qty"] == 0 for u in live["users"] for m in live["markets"])
    assert _check_mine(live) == []

    print("\n--- live timings (Timescale + C++ engine) ---")
    for k, v in timings.items():
        print(f"{k:42s} {v:7.0f} ms")
    print("global check_invariants:", trading.check_invariants() or "OK")
