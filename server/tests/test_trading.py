import os
import sys
import types

import pytest

SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SERVER)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_trading_fakes import FakeDB  # noqa: E402

# trading/mm_bot/trading_api do `import db`; point that at a proxy we can swap per test
db_proxy = types.ModuleType("db_fake_proxy")
sys.modules.setdefault("db", db_proxy)

import engine  # noqa: E402
import mm_bot  # noqa: E402
import risk  # noqa: E402
import trading  # noqa: E402
import trading_api  # noqa: E402

# If another test module imported the real db first, these modules are bound to it.
# Point them at the proxy explicitly so trading tests can never touch Timescale.
for _m in (trading, mm_bot, trading_api):
    _m.db = db_proxy

EXE = engine._resolve_path(None)
BACKENDS = ["py"] + (["cpp"] if os.path.exists(EXE) else [])


@pytest.fixture(params=BACKENDS)
def env(request):
    fake = FakeDB()
    for name in dir(fake):
        if not name.startswith("_"):
            setattr(db_proxy, name, getattr(fake, name))
    eng = engine.Engine(path=EXE if request.param == "cpp" else os.path.join(SERVER, "no_such.exe"))
    engine._engine = eng
    trading.startup()
    yield fake, eng
    eng.stop()
    engine._engine = None


def total_cash(fake):
    return sum(u["cash"] for u in fake.users.values())


def pos(fake, uid, mid):
    return fake.get_position(uid, mid)["qty"]


# ------------------------------------------------------------------ risk
def test_risk_rules():
    assert risk.locked_cash([], [{"market_id": 1, "side": "buy", "price": 60, "qty_remaining": 10}]) == 600
    # naked short sell needs (100-p)
    assert risk.locked_cash([], [{"market_id": 1, "side": "sell", "price": 60, "qty_remaining": 10}]) == 400
    # long covering a sell needs nothing
    assert risk.locked_cash([{"market_id": 1, "qty": 10}],
                            [{"market_id": 1, "side": "sell", "price": 60, "qty_remaining": 10}]) == 0
    # mixed: short position elsewhere + partly covered sell + buy
    assert risk.locked_cash([{"market_id": 1, "qty": 4}, {"market_id": 2, "qty": -3}],
                            [{"market_id": 1, "side": "sell", "price": 70, "qty_remaining": 10},
                             {"market_id": 2, "side": "buy", "price": 20, "qty_remaining": 5}]) == 6 * 30 + 300 + 100
    with pytest.raises(risk.RiskError):
        risk.check_order(500, [], [], 1, "buy", 60, 10)


# --------------------------------------------------------------- trading
def test_yes_vs_no_trade(env):
    fake, eng = env
    _, (m,) = fake.add_race(["Jane"])
    trading.open_market(m)
    a, b = fake.add_user("a"), fake.add_user("b")
    before = total_cash(fake)
    r1 = trading.place_order(a, m, "YES", "buy", "limit", 60, 10)
    assert r1["order"]["qty_remaining"] == 10 and r1["fills"] == []
    r2 = trading.place_order(b, m, "NO", "buy", "limit", 40, 10)
    assert r2["fills"] == [{"price": 60, "qty": 10}] and r2["order"] is None
    assert pos(fake, a, m) == 10 and pos(fake, b, m) == -10
    assert fake.users[a]["cash"] == 100000 - 600 and fake.users[b]["cash"] == 100000 + 600
    assert total_cash(fake) == before
    assert len(fake.trades) == 1 and fake.ticks[-1]["last_price"] == 60


def test_partial_fill(env):
    fake, eng = env
    _, (m,) = fake.add_race(["Jane"])
    a, b = fake.add_user("a"), fake.add_user("b")
    r = trading.place_order(a, m, "YES", "sell", "limit", 55, 30)
    trading.place_order(b, m, "YES", "buy", "limit", 55, 12)
    assert fake.orders[r["order"]["id"]]["qty_remaining"] == 18
    assert trading.get_book(m)["asks"] == [{"price": 55, "qty": 18}]


def test_insufficient_funds_never_reaches_engine(env):
    fake, eng = env
    _, (m,) = fake.add_race(["Jane"])
    a = fake.add_user("a", cash=1000)
    with pytest.raises(trading.TradingError, match="Insufficient"):
        trading.place_order(a, m, "YES", "buy", "limit", 60, 100)
    assert trading.get_book(m) == {"bids": [], "asks": []}
    assert fake.orders == {}


def test_market_order_caps(env):
    fake, eng = env
    _, (m,) = fake.add_race(["Jane"])
    mm, a = fake.add_user("mm"), fake.add_user("a", cash=1500)
    for p in (50, 51, 52):
        trading.place_order(mm, m, "YES", "sell", "limit", p, 10)
    # 10@50 + 10@51 = 1010, remaining 490 buys 9@52
    r = trading.place_order(a, m, "YES", "buy", "market", None, 100)
    assert sum(f["qty"] for f in r["fills"]) == 29 and r["order"] is None
    assert fake.users[a]["cash"] == 1500 - 500 - 510 - 9 * 52
    rich = fake.add_user("rich")
    r = trading.place_order(rich, m, "YES", "buy", "market", None, 100)
    assert sum(f["qty"] for f in r["fills"]) == 1
    with pytest.raises(trading.TradingError, match="No liquidity"):
        trading.place_order(rich, m, "YES", "buy", "market", None, 5)


def test_cancel_releases_cash(env):
    fake, eng = env
    _, (m,) = fake.add_race(["Jane"])
    a = fake.add_user("a")
    r = trading.place_order(a, m, "YES", "buy", "limit", 50, 100)
    assert trading.available_cash(a) == 100000 - 5000
    trading.cancel_order(a, r["order"]["id"])
    assert trading.available_cash(a) == 100000
    assert trading.get_book(m)["bids"] == []
    with pytest.raises(trading.TradingError):
        trading.cancel_order(a, r["order"]["id"])


def test_resolve_market_yes(env):
    fake, eng = env
    _, (m,) = fake.add_race(["Jane"])
    a, b = fake.add_user("a"), fake.add_user("b")
    trading.place_order(a, m, "YES", "buy", "limit", 60, 10)
    trading.place_order(b, m, "YES", "sell", "limit", 60, 10)
    trading.place_order(a, m, "YES", "buy", "limit", 10, 5)   # resting, gets cancelled
    trading.resolve_market(m, "YES")
    assert fake.users[a]["cash"] == 100000 - 600 + 1000
    assert fake.users[b]["cash"] == 100000 + 600 - 1000
    assert pos(fake, a, m) == 0 and pos(fake, b, m) == 0
    assert fake.markets[m]["status"] == "resolved" and fake.open_orders() == []


def test_resolve_race_single_seat(env):
    fake, eng = env
    rid, ids = fake.add_race(["A", "B", "C", "RON (Re-Open Nominations)"])
    u, v = fake.add_user("u"), fake.add_user("v")
    for mid in ids:
        trading.place_order(u, mid, "YES", "buy", "limit", 30, 10)
        trading.place_order(v, mid, "YES", "sell", "limit", 30, 10)
    with pytest.raises(trading.TradingError):
        trading.resolve_race(rid, [ids[0], ids[1]])
    trading.resolve_race(rid, [ids[1]])
    assert fake.users[u]["cash"] == 100000 - 4 * 300 + 1000
    assert fake.users[v]["cash"] == 100000 + 4 * 300 - 1000
    assert [fake.markets[i]["outcome"] for i in ids] == ["NO", "YES", "NO", "NO"]
    assert fake.races[rid]["status"] == "resolved"
    assert all(fake.markets[i]["status"] == "resolved" for i in ids)


def test_resolve_race_ron_and_multi_seat(env):
    fake, eng = env
    rid, ids = fake.add_race(["A", "B", "RON"], seats=2)
    trading.resolve_race(rid, [ids[2]])   # RON alone is allowed
    rid2, ids2 = fake.add_race([f"C{i}" for i in range(8)], seats=5)
    with pytest.raises(trading.TradingError):
        trading.resolve_race(rid2, ids2[:4])
    trading.resolve_race(rid2, ids2[:5])
    assert sum(fake.markets[i]["outcome"] == "YES" for i in ids2) == 5


def test_closed_market_rejects_orders(env):
    fake, eng = env
    rid, (m,) = fake.add_race(["Jane"])
    a = fake.add_user("a")
    trading.place_order(a, m, "YES", "buy", "limit", 40, 10)
    trading.close_race(rid)
    trading.close_race(rid)   # idempotent
    assert fake.markets[m]["status"] == "closed" and fake.open_orders() == []
    with pytest.raises(trading.TradingError, match="closed"):
        trading.place_order(a, m, "YES", "buy", "limit", 40, 10)


def test_db_failure_resyncs_engine(env):
    fake, eng = env
    _, (m,) = fake.add_race(["Jane"])
    a, b = fake.add_user("a"), fake.add_user("b")
    trading.place_order(a, m, "YES", "sell", "limit", 50, 10)
    fake.fail_next_transaction = True
    with pytest.raises(trading.TradingError):
        trading.place_order(b, m, "YES", "buy", "limit", 50, 10)
    assert trading.get_book(m)["asks"] == [{"price": 50, "qty": 10}]
    assert fake.trades == []


def test_engine_restart_replays(env):
    fake, eng = env
    if eng.proc is None:
        pytest.skip("C++ engine only")
    _, (m,) = fake.add_race(["Jane"])
    a = fake.add_user("a")
    trading.place_order(a, m, "YES", "buy", "limit", 40, 10)
    trading.place_order(a, m, "YES", "sell", "limit", 70, 5)
    eng.kill()
    b = fake.add_user("b")
    r = trading.place_order(b, m, "YES", "sell", "limit", 40, 4)
    assert r["fills"] == [{"price": 40, "qty": 4}]
    assert trading.get_book(m) == {"bids": [{"price": 40, "qty": 6}], "asks": [{"price": 70, "qty": 5}]}


def test_mm_bot_normalised(env):
    fake, eng = env
    rid, ids = fake.add_race(["A", "B", "C", "RON"], priors=[50, 30, 30, 10])
    mm_bot.requote_all()
    mids = []
    for mid in ids:
        bk = trading.get_book(mid)
        assert bk["bids"] and bk["asks"]
        mids.append((bk["bids"][0]["price"] + bk["asks"][0]["price"]) / 2)
    assert abs(sum(mids) - 100) <= 4
    mm_bot.requote_all()   # requoting replaces, doesn't stack
    house = fake.ensure_bot_user()
    assert len(fake.open_orders(market_id=ids[0], user_id=house)) == 2


def test_invariants_after_random_trading(env):
    import random
    fake, eng = env
    rng = random.Random(1)
    rid, ids = fake.add_race(["A", "B", "RON"])
    users = [fake.add_user(f"u{i}", cash=5000) for i in range(5)]
    for _ in range(300):
        try:
            trading.place_order(rng.choice(users), rng.choice(ids), rng.choice(["YES", "NO"]),
                                rng.choice(["buy", "sell"]), rng.choice(["limit", "limit", "market"]),
                                rng.randint(1, 99), rng.randint(1, 40))
        except trading.TradingError:
            pass
    for mid in ids:
        assert sum(p["qty"] for p in fake.market_positions(mid)) == 0
    for u in users:
        assert trading.available_cash(u) >= 0
    # engine and DB agree on resting orders
    for mid in ids:
        bk = trading.get_book(mid, 100)
        db_bids = {}
        for o in fake.open_orders(market_id=mid):
            if o["side"] == "buy":
                db_bids[o["price"]] = db_bids.get(o["price"], 0) + o["qty_remaining"]
        assert {l["price"]: l["qty"] for l in bk["bids"]} == db_bids
