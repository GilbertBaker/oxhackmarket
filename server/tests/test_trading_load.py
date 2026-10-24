"""Load tests.
  test_concurrent_stress     always runs: many HTTP clients + the MM bot at once, then invariants.
  test_latency_benchmark     opt-in (TRADING_LOAD=1): every DB call costs DB_LATENCY_MS (default 30)
                             to approximate cloud Timescale round trips; prints timings.
  python -m pytest server/tests/test_trading_load.py -s            (TRADING_LOAD=1 for the benchmark)
"""
import os
from contextlib import contextmanager
import random
import statistics
import threading
import time

import pytest
import requests
from werkzeug.serving import make_server

import test_trading
from test_trading import env  # noqa: F401
from test_trading_tools import _test_app
import mm_bot
import trading

LOCKED = {"add_user", "add_race", "add_market"}


def _wrap_db(fake, latency=0.0):
    """Re-point the db proxy at `fake` with a per-call lock (FakeDB's dicts aren't thread-safe;
    Postgres statements are atomic) and an optional sleep per call, outside the lock."""
    lock = threading.RLock()
    calls = {"n": 0}

    def wrap(fn):
        def w(*a, **k):
            if latency:
                time.sleep(latency)
            with lock:
                calls["n"] += 1
                return fn(*a, **k)
        return w

    for name in dir(fake):
        if not name.startswith("_") and callable(getattr(fake, name)) and name not in LOCKED:
            if name == "transaction":
                setattr(test_trading.db_proxy, name, _locked_tx(fake, lock, latency))
                continue
            setattr(test_trading.db_proxy, name, wrap(getattr(fake, name)))
    return calls


def _locked_tx(fake, lock, latency):
    @contextmanager
    def tx():
        if latency:
            time.sleep(latency)   # BEGIN; COMMIT costs another round trip, close enough
        with lock, fake.transaction() as conn:
            yield conn
    return tx


def _mm_loop(stop, passes):
    while not stop.is_set():
        t = time.perf_counter()
        mm_bot.requote_all()
        passes.append(time.perf_counter() - t)


def test_concurrent_stress(env):  # noqa: F811
    fake, eng = env
    _wrap_db(fake)
    fake.add_user("admin", is_admin=True)
    for i in range(3):
        fake.add_race([f"R{i}A", f"R{i}B", f"R{i}C", "RON"], priors=[40, 30, 20, 10])
    mm_bot.requote_all()
    server = make_server("127.0.0.1", 0, _test_app(fake), threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    market_ids = [m["id"] for m in fake.markets.values()]
    cash_before = None
    codes, errors = {}, []
    stop = threading.Event()

    def client(i):
        rng = random.Random(i)
        s = requests.Session()
        s.post(url + "/api/register", json={"username": f"load{i}", "password": "x"})
        mine = []
        while not stop.is_set():
            try:
                if mine and rng.random() < 0.2:
                    r = s.delete(f"{url}/api/orders/{mine.pop(rng.randrange(len(mine)))}")
                else:
                    typ = "market" if rng.random() < 0.2 else "limit"
                    r = s.post(url + "/api/orders", json={
                        "market_id": rng.choice(market_ids), "outcome": rng.choice(["YES", "NO"]),
                        "side": rng.choice(["buy", "sell"]), "type": typ,
                        "price": rng.randint(5, 95), "qty": rng.randint(1, 40)})
                    if r.status_code == 201 and r.json()["order"]:
                        mine.append(r.json()["order"]["id"])
                codes[r.status_code] = codes.get(r.status_code, 0) + 1
            except Exception as e:   # noqa: BLE001
                errors.append(repr(e))

    passes = []
    try:
        threads = [threading.Thread(target=client, args=(i,)) for i in range(8)]
        threads.append(threading.Thread(target=_mm_loop, args=(stop, passes)))
        # register first so the cash total includes every user
        for t in threads[:-1]:
            t.start()
        time.sleep(0.5)
        cash_before = sum(u["cash"] for u in fake.users.values())
        threads[-1].start()
        time.sleep(4)
        stop.set()
        for t in threads:
            t.join(timeout=30)
    finally:
        server.shutdown()

    print(f"\nstress: status codes {codes}, mm passes {len(passes)}, trades {len(fake.trades)}")
    assert not errors, errors[:3]
    assert set(codes) <= {200, 201, 400}, codes        # no 500s
    assert codes.get(201, 0) > 50 and len(fake.trades) > 20 and passes
    assert sum(u["cash"] for u in fake.users.values()) == cash_before   # trades only move cash
    assert trading.check_invariants() == []


@pytest.mark.skipif(os.environ.get("TRADING_LOAD") != "1", reason="set TRADING_LOAD=1 to run the latency benchmark")
def test_latency_benchmark(env):  # noqa: F811
    fake, eng = env
    lat = float(os.environ.get("DB_LATENCY_MS", "30")) / 1000
    calls = _wrap_db(fake, lat)
    for i in range(5):   # 20 candidate markets, like a full Union election
        fake.add_race([f"R{i}A", f"R{i}B", f"R{i}C", "RON"], priors=[40, 30, 20, 10])
    users = [fake.add_user(f"u{i}") for i in range(4)]
    ids = list(fake.markets)

    t = time.perf_counter()
    mm_bot.requote_all()
    first = time.perf_counter() - t
    t = time.perf_counter()
    mm_bot.requote_all()
    idle = time.perf_counter() - t

    def timed(f):
        t0 = time.perf_counter()
        f()
        return time.perf_counter() - t0

    alone = [timed(lambda: trading.place_order(users[0], ids[0], "YES", "buy", "limit", 5, 1)) for _ in range(5)]
    cross = [timed(lambda: trading.place_order(users[1], ids[1], "YES", "buy", "market", None, 1)) for _ in range(5)]

    # user orders while the bot requotes everything (force requotes by moving fair values)
    stop, passes = threading.Event(), []
    for mid in ids:
        fake.trades.append({"market_id": mid, "price": 90, "qty": 1, "time": None})
    bot = threading.Thread(target=_mm_loop, args=(stop, passes))
    bot.start()
    rng = random.Random(0)
    busy = []
    for _ in range(15):
        busy.append(timed(lambda: trading.place_order(rng.choice(users), rng.choice(ids), "YES", "buy",
                                                      "limit", rng.randint(1, 30), 1)))
        for mid in rng.sample(ids, 5):   # keep moving fair values so the bot keeps working
            fake.trades.append({"market_id": mid, "price": rng.randint(5, 95), "qty": 1, "time": None})
    stop.set()
    bot.join()

    def ms(xs):
        return f"p50 {statistics.median(xs) * 1000:.0f} ms, max {max(xs) * 1000:.0f} ms"
    print(f"\n--- latency benchmark, {lat * 1000:.0f} ms per DB call, 20 markets ---")
    print(f"mm first pass (all quotes new): {first:.2f} s")
    print(f"mm pass, nothing changed:       {idle:.2f} s")
    print(f"mm passes under load:           {ms(passes)}")
    print(f"resting limit order, idle:      {ms(alone)}")
    print(f"market order (1 fill), idle:    {ms(cross)}")
    print(f"limit order while bot requotes: {ms(busy)}")
    print(f"total DB calls: {calls['n']}")
    assert trading.check_invariants() == []
