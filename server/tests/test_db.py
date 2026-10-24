"""db.py tests against the real Tiger Data service (owner B).

Only touches rows these tests create: usernames start with "test_", elections with
"TEST ". Cleanup deletes exactly those rows (never TRUNCATE).
Run from repo root:  .venv/bin/python -m pytest server/tests/test_db.py
"""
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from dotenv import load_dotenv

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
load_dotenv(SERVER.parent / ".env")

import db  # noqa: E402

S = db.SCHEMA


def _cleanup(election_prefix, username_prefix):
    with db.transaction() as c:
        races = [r["id"] for r in c.execute(f"SELECT id FROM {S}.races WHERE election LIKE %s",
                                             (election_prefix + "%",)).fetchall()]
        markets = [r["id"] for r in c.execute(f"SELECT id FROM {S}.markets WHERE race_id = ANY(%s)",
                                              (races,)).fetchall()]
        users = [r["id"] for r in c.execute(f"SELECT id FROM {S}.users WHERE username LIKE %s",
                                            (username_prefix + "%",)).fetchall()]
        c.execute(f"DELETE FROM {S}.trades WHERE market_id = ANY(%s)", (markets,))
        c.execute(f"DELETE FROM {S}.price_ticks WHERE market_id = ANY(%s)", (markets,))
        c.execute(f"DELETE FROM {S}.orders WHERE market_id = ANY(%s) OR user_id = ANY(%s)", (markets, users))
        c.execute(f"DELETE FROM {S}.positions WHERE market_id = ANY(%s) OR user_id = ANY(%s)", (markets, users))
        c.execute(f"DELETE FROM {S}.portfolio_snapshots WHERE user_id = ANY(%s)", (users,))
        c.execute(f"DELETE FROM {S}.markets WHERE id = ANY(%s)", (markets,))
        c.execute(f"DELETE FROM {S}.races WHERE id = ANY(%s)", (races,))
        c.execute(f"DELETE FROM {S}.users WHERE id = ANY(%s)", (users,))


@pytest.fixture(scope="module")
def tag():
    db.ensure_schema()
    t = uuid.uuid4().hex[:8]
    yield t
    _cleanup(f"TEST {t}", f"test_{t}")


@pytest.fixture(scope="module")
def race(tag):
    """A President race created 3 hours ago with two candidates + RON."""
    created = db.utcnow() - timedelta(hours=3)
    rid = db.create_race({"election": f"TEST {tag}", "position": "President", "seats": 1,
                          "resolution_criteria": "test", "closes_at": db.utcnow() + timedelta(days=7),
                          "status": "open", "created_at": created})
    ids = {}
    for name, prior in (("Alice Test", 50), ("Bob Test", 30), ("RON (Re-Open Nominations)", 20)):
        ids[name] = db.create_market({"race_id": rid, "candidate": name, "question": f"Will {name} win?",
                                      "resolution_criteria": "test", "closes_at": db.utcnow() + timedelta(days=7),
                                      "status": "open", "ai_prior": prior, "created_at": created})
    return {"id": rid, "markets": ids, "created": created}


def test_users(tag):
    u = db.create_user(f"test_{tag}_u", "hash")
    assert u["cash"] == 100000 and not u["is_bot"]
    assert db.get_user(u["id"])["username"] == f"test_{tag}_u"
    assert db.get_user_by_username(f"test_{tag}_u")["id"] == u["id"]
    db.adjust_cash(u["id"], -500)
    assert db.get_user(u["id"])["cash"] == 99500
    assert db.ensure_bot_user() == db.ensure_bot_user()
    assert all(not x["is_bot"] for x in db.list_users())


def test_race_crud_and_status_propagation(tag, race):
    assert db.find_race(f"TEST {tag}", "President")["id"] == race["id"]
    with pytest.raises(Exception):
        db.create_race({"election": f"TEST {tag}", "position": "President",
                        "resolution_criteria": "dup", "closes_at": db.utcnow()})
    other = db.create_race({"election": f"TEST {tag}", "position": "Treasurer",
                            "resolution_criteria": "t", "closes_at": db.utcnow() + timedelta(days=1)})
    m = db.create_market({"race_id": other, "candidate": "X", "question": "q?",
                          "resolution_criteria": "t", "closes_at": db.utcnow() + timedelta(days=1)})
    db.update_race(other, status="closed")
    assert db.get_race(other)["status"] == "closed"
    assert db.get_market(m)["status"] == "closed"
    mine = [r["id"] for r in db.list_races("all") if r["election"] == f"TEST {tag}"]
    assert mine.index(other) < mine.index(race["id"])  # earlier closes_at first
    assert len(db.race_markets(race["id"])) == 3
    assert db.position_rank("President") < db.position_rank("Secretary") < db.position_rank("Standing Committee")


def test_transaction_rollback(tag, race):
    u = db.create_user(f"test_{tag}_tx", "hash")
    mid = race["markets"]["Bob Test"]
    with pytest.raises(RuntimeError):
        with db.transaction() as c:
            oid = db.next_order_id(c)
            db.insert_order({"id": oid, "market_id": mid, "user_id": u["id"], "side": "buy", "price": 40,
                             "qty": 10, "qty_remaining": 10, "status": "open"}, c)
            db.add_position(u["id"], mid, 10, 400, c)
            db.adjust_cash(u["id"], -400, c)
            raise RuntimeError("boom")
    assert db.get_order(oid) is None
    assert db.get_position(u["id"], mid)["qty"] == 0
    assert db.get_user(u["id"])["cash"] == 100000
    with db.transaction() as c:
        oid = db.next_order_id(c)
        db.insert_order({"id": oid, "market_id": mid, "user_id": u["id"], "side": "buy", "price": 40,
                         "qty": 10, "qty_remaining": 10, "status": "open"}, c)
        db.update_order(oid, 4, "open", c)
    assert db.get_order(oid)["qty_remaining"] == 4
    assert [o["id"] for o in db.open_orders(user_id=u["id"])] == [oid]


def _trade(mid, price, qty, t, buyer, seller):
    db.insert_trade({"time": t, "market_id": mid, "price": price, "qty": qty,
                     "buyer_id": buyer, "seller_id": seller, "taker_side": "buy"})


def test_time_series(tag, race):
    house = db.ensure_bot_user()
    u = db.create_user(f"test_{tag}_ts", "hash")
    alice, bob = race["markets"]["Alice Test"], race["markets"]["Bob Test"]
    now = db.utcnow()
    t1, t2 = now - timedelta(hours=2), now - timedelta(minutes=30)
    _trade(alice, 40, 10, t1, u["id"], house)
    _trade(alice, 45, 5, t1 + timedelta(minutes=1), u["id"], house)
    _trade(alice, 60, 7, t2, u["id"], house)
    db.record_tick(bob, 20, 30, None, time=t1)

    # current price: Alice has no ticks -> last trade; Bob's tick mid = 25; RON -> ai_prior
    assert db.current_price(alice) == 60
    assert db.current_price(bob) == 25
    assert db.current_price(race["markets"]["RON (Re-Open Nominations)"]) == 20
    assert db.last_trade_price(alice) == 60
    assert [x["price"] for x in db.recent_trades(alice)] == [60, 45, 40]

    # as-of prices
    assert db.price_at(alice, t1 + timedelta(minutes=5)) == 45
    assert db.price_at(alice, race["created"] + timedelta(minutes=1)) == 50  # prior before any trade
    assert db.price_at(alice, race["created"] - timedelta(hours=1)) is None  # didn't exist yet

    # candles via continuous aggregate (real-time aggregation covers unmaterialised rows)
    hist = db.price_history(alice, "5m", race["created"])
    closes = [p["close"] for p in hist]
    assert None not in closes, "gap-filled history must have no holes"
    assert closes[0] == 50 and closes[-1] == 60
    first = next(p for p in hist if p["volume"])
    assert (first["open"], first["high"], first["low"], first["close"], first["volume"]) == (40, 45, 40, 45, 15)
    h1 = db.price_history(alice, "1m", now - timedelta(minutes=45))
    assert None not in [p["close"] for p in h1] and h1[0]["close"] == 45

    # race history: one hole-free series per candidate
    series = {s["candidate"]: s for s in db.race_history(race["id"], "5m", race["created"])}
    assert set(series) == {"Alice Test", "Bob Test", "RON (Re-Open Nominations)"}
    lens = {len(s["points"]) for s in series.values()}
    assert len(lens) == 1
    assert series["Bob Test"]["points"][-1]["close"] == 25
    assert all(p["close"] == 20 for p in series["RON (Re-Open Nominations)"]["points"])

    # time machine at t1+5m: Alice 45 leads Bob 25 (tick), RON 20
    tm = [r for r in db.timemachine(t1 + timedelta(minutes=5)) if r["id"] == race["id"]][0]
    assert tm["status"] == "open"
    assert [(c["candidate"], c["price"]) for c in tm["candidates"]] == [
        ("Alice Test", 45), ("Bob Test", 25), ("RON (Re-Open Nominations)", 20)]
    assert not [r for r in db.timemachine(race["created"] - timedelta(minutes=1)) if r["id"] == race["id"]]


def test_equity_with_short(tag, race):
    u = db.create_user(f"test_{tag}_eq", "hash")
    bob = race["markets"]["Bob Test"]  # current price 25 (tick mid)
    alice = race["markets"]["Alice Test"]  # current price 60
    db.add_position(u["id"], alice, 10, 400)
    db.add_position(u["id"], bob, -20, -500)
    assert db.equity(u["id"]) == 100000 + 10 * 60 - 20 * 25
    lb = {r["username"]: r for r in db.leaderboard(1000)}
    assert lb[f"test_{tag}_eq"]["equity"] == 100100
    assert "house" not in lb
    db.insert_portfolio_snapshot(u["id"], 100000, 100050, time=db.utcnow() - timedelta(hours=2))
    db.insert_portfolio_snapshot(u["id"], 100000, 100100)
    hist = db.equity_history([u["id"]], "1h")[u["id"]]
    assert hist[-1]["equity"] == 100100 and len(hist) >= 2
