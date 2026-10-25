"""Hypertables, continuous aggregates, gap-filling, as-of queries, compression, equity."""
from datetime import timedelta

import pytest
from psycopg import sql

import db
from conftest import RON, autocommit, refresh_candles, trade

S = db.SCHEMA


def floor(t, width):
    return db._bucket_range(width, t, t + width)[0]


# ---------------------------------------------------------------- current price rule (COLLAB 5)

def test_current_price_rules(make_race, house, make_user):
    u = make_user()
    r = make_race(candidates=(("Tick", 40), ("OneSided", 40), ("Traded", 40), ("Prior", 33), ("NoPrior", None)))
    m = r["markets"]
    now = db.utcnow()
    db.record_tick(m["Tick"], 61, 64, None, time=now - timedelta(minutes=1))     # mid 62.5 -> 63
    trade(m["Tick"], 10, 1, now - timedelta(minutes=2), u["id"], house)
    trade(m["OneSided"], 55, 1, now - timedelta(minutes=2), u["id"], house)
    db.record_tick(m["OneSided"], 50, None, 55, time=now - timedelta(minutes=1))
    trade(m["Traded"], 71, 3, now - timedelta(minutes=5), u["id"], house)
    assert db.current_price(m["Tick"]) == 63
    assert db.current_price(m["OneSided"]) == 55
    assert db.current_price(m["Traded"]) == 71
    assert db.current_price(m["Prior"]) == 33
    assert db.current_price(m["NoPrior"]) == 50
    prices = {x["candidate"]: x["price"] for x in db.race_markets(r["id"])}
    assert prices == {"Tick": 63, "OneSided": 55, "Traded": 71, "Prior": 33, "NoPrior": 50}


def test_record_and_last_tick(make_race):
    r = make_race()
    mid = r["markets"]["Alice"]
    db.record_tick(mid, 40, 45, None, time=db.utcnow() - timedelta(seconds=30))
    db.record_tick(mid, 41, 44, 42)
    t = db.last_tick(mid)
    assert (t["best_bid"], t["best_ask"], t["last_price"]) == (41, 44, 42)


def test_recent_trades_newest_first(make_race, house, make_user):
    r, u = make_race(), make_user()
    mid = r["markets"]["Alice"]
    now = db.utcnow()
    for i, p in enumerate([40, 41, 42]):
        trade(mid, p, 1, now - timedelta(minutes=10 - i), u["id"], house)
    assert [x["price"] for x in db.recent_trades(mid)] == [42, 41, 40]
    assert [x["price"] for x in db.recent_trades(mid, limit=2)] == [42, 41]
    assert db.last_trade_price(mid) == 42


# ---------------------------------------------------------------- as-of queries

def test_price_at_history(make_race, house, make_user):
    u = make_user()
    r = make_race(created_ago=timedelta(hours=5))
    a = r["markets"]["Alice"]
    now = db.utcnow()
    trade(a, 40, 1, now - timedelta(hours=4), u["id"], house)
    db.record_tick(a, 58, 62, 40, time=now - timedelta(hours=2))
    trade(a, 70, 1, now - timedelta(hours=1), u["id"], house)
    assert db.price_at(a, r["created"] - timedelta(minutes=1)) is None      # not created yet
    assert db.price_at(a, r["created"] + timedelta(minutes=1)) == 50        # ai_prior
    assert db.price_at(a, now - timedelta(hours=3)) == 40                   # last trade
    assert db.price_at(a, now - timedelta(minutes=90)) == 60                # two-sided tick mid wins
    assert db.price_at(a, now) == 60                                        # tick still latest
    assert db.prices_at([a, r["markets"]["Bob"]], now - timedelta(hours=3)) == {a: 40, r["markets"]["Bob"]: 30}


def test_timemachine_status_and_candidates(make_race, house, make_user):
    u = make_user()
    now = db.utcnow()
    r = make_race(position="Treasurer", created_ago=timedelta(hours=6), closes_in=-timedelta(hours=2),
                  status="resolved", resolved_at=now - timedelta(hours=1))
    late = db.create_market({"race_id": r["id"], "candidate": "Late Entry", "question": "q",
                             "resolution_criteria": "t", "closes_at": r["closes"], "status": "resolved",
                             "ai_prior": 10, "created_at": now - timedelta(hours=3)})
    trade(r["markets"]["Bob"], 80, 5, now - timedelta(hours=4), u["id"], house)
    hidden = make_race(position="Secretary", status="proposed", created_ago=timedelta(hours=6))

    def snap(t):
        return {x["id"]: x for x in db.timemachine(t)}

    s = snap(now - timedelta(hours=5))
    assert r["id"] in s and hidden["id"] not in s
    race = s[r["id"]]
    assert race["status"] == "open"
    assert [(c["candidate"], c["price"]) for c in race["candidates"]] == [("Alice", 50), ("Bob", 30), (RON, 20)]
    assert set(race) == {"id", "election", "position", "seats", "status", "candidates"}

    race = snap(now - timedelta(hours=3, minutes=30))[r["id"]]
    assert race["candidates"][0] == {"market_id": r["markets"]["Bob"], "candidate": "Bob", "price": 80}
    assert "Late Entry" not in [c["candidate"] for c in race["candidates"]]

    race = snap(now - timedelta(minutes=90))[r["id"]]
    assert race["status"] == "closed"
    assert "Late Entry" in [c["candidate"] for c in race["candidates"]]
    assert snap(now)[r["id"]]["status"] == "resolved"
    assert r["id"] not in snap(r["created"] - timedelta(minutes=1))
    assert late


def test_timemachine_range(make_race):
    r = make_race(created_ago=timedelta(days=2))
    rng = db.timemachine_range()
    assert rng["start"] <= r["created"] and rng["end"] >= db.utcnow() - timedelta(seconds=5)


# ---------------------------------------------------------------- gap-filled history

@pytest.mark.parametrize("bucket", ["1m", "5m", "1h"])
def test_price_history_has_no_holes(make_race, house, make_user, bucket):
    u = make_user()
    width = db.BUCKETS[bucket]
    r = make_race(created_ago=width * 40)
    a = r["markets"]["Alice"]
    now = db.utcnow()
    trade(a, 45, 2, now - width * 30, u["id"], house)
    trade(a, 55, 3, now - width * 10, u["id"], house)
    refresh_candles(now - width * 31)   # back-dated trades: materialise the cagg windows
    pts = db.price_history(a, bucket, r["created"])
    assert len(pts) >= 40
    assert None not in [p["close"] for p in pts]
    ts = [db.parse_iso(p["t"]) for p in pts]
    assert all(b - a_ == width for a_, b in zip(ts, ts[1:])), "buckets must be contiguous"
    assert pts[0]["close"] == 50 and pts[-1]["close"] == 55
    assert sum(p["volume"] for p in pts) == 5
    for p in pts:
        assert p["low"] <= p["open"] <= p["high"] and p["low"] <= p["close"] <= p["high"]


def test_price_history_ohlcv(make_race, house, make_user):
    u = make_user()
    r = make_race(created_ago=timedelta(hours=2))
    a = r["markets"]["Alice"]
    width = timedelta(minutes=5)
    b0 = floor(db.utcnow() - timedelta(hours=1), width)
    for dt, price, qty in [(30, 40, 1), (60, 47, 2), (120, 38, 3), (200, 44, 4)]:
        trade(a, price, qty, b0 + timedelta(seconds=dt), u["id"], house)
    refresh_candles(b0)
    for bucket in ("1m", "5m"):
        pts = {p["t"]: p for p in db.price_history(a, bucket, r["created"])}
        if bucket == "5m":
            p = pts[db.iso(b0)]
            assert (p["open"], p["high"], p["low"], p["close"], p["volume"]) == (40, 47, 38, 44, 10)
        else:
            p = pts[db.iso(b0 + timedelta(minutes=2))]
            assert (p["open"], p["close"], p["volume"]) == (38, 38, 3)


def test_price_history_falls_back_to_ticks_then_prior(make_race):
    r = make_race(created_ago=timedelta(hours=2))
    b = r["markets"]["Bob"]
    db.record_tick(b, 20, 24, None, time=db.utcnow() - timedelta(hours=1))
    pts = db.price_history(b, "5m", r["created"])
    assert pts[0]["close"] == 30 and pts[-1]["close"] == 22 and None not in [p["close"] for p in pts]
    assert all(p["volume"] == 0 for p in pts)


def test_history_is_clamped(make_race):
    r = make_race(created_ago=timedelta(days=30))
    pts = db.price_history(r["markets"]["Alice"], "1m", r["created"])
    assert len(pts) <= db.MAX_BUCKETS + 1


def test_race_history_aligned_series(make_race, house, make_user):
    u = make_user()
    r = make_race(created_ago=timedelta(hours=3))
    now = db.utcnow()
    trade(r["markets"]["Alice"], 65, 1, now - timedelta(hours=2), u["id"], house)
    db.record_tick(r["markets"]["Bob"], 18, 22, None, time=now - timedelta(hours=1))
    late = db.create_market({"race_id": r["id"], "candidate": "Late", "question": "q", "resolution_criteria": "t",
                             "closes_at": r["closes"], "status": "open", "ai_prior": 5,
                             "created_at": now - timedelta(minutes=30)})
    series = {s["candidate"]: s for s in db.race_history(r["id"], "5m", r["created"])}
    assert set(series) == {"Alice", "Bob", RON, "Late"}
    full = {len(series[c]["points"]) for c in ("Alice", "Bob", RON)}
    assert len(full) == 1
    assert series["Alice"]["points"][-1]["close"] == 65
    assert series["Bob"]["points"][-1]["close"] == 20
    assert {p["close"] for p in series[RON]["points"]} == {20}
    # the late candidate only has points from when it existed
    assert 0 < len(series["Late"]["points"]) < full.pop()
    assert series["Late"]["market_id"] == late
    assert db.race_history(-1, "5m", r["created"]) == []


def test_bad_bucket():
    with pytest.raises(ValueError):
        db.price_history(1, "7m", db.utcnow())


# ---------------------------------------------------------------- continuous aggregates

def test_cagg_matches_raw_after_refresh(make_race, house, make_user):
    """Explicitly materialise candles_5m/1h and compare against a raw time_bucket query."""
    u = make_user()
    r = make_race(created_ago=timedelta(hours=4))
    a = r["markets"]["Alice"]
    now = db.utcnow()
    for i in range(24):
        trade(a, 30 + (i * 7) % 40, 1 + i % 3, now - timedelta(hours=3) + timedelta(minutes=7 * i), u["id"], house)
    lo, hi = floor(now - timedelta(hours=4), timedelta(hours=1)), floor(now, timedelta(hours=1))
    for view in ("candles_5m", "candles_1h"):
        autocommit(sql.SQL("CALL refresh_continuous_aggregate({}, {}, {})").format(
            sql.Literal(f"{S}.{view}"), sql.Literal(lo), sql.Literal(hi)))
    for view, width in (("candles_5m", "5 minutes"), ("candles_1h", "1 hour")):
        with db.pool().connection() as c:
            cagg = c.execute(f"""SELECT bucket, open, high, low, close, volume FROM {S}.{view}
                                 WHERE market_id = %s ORDER BY bucket""", (a,)).fetchall()
            raw = c.execute(f"""SELECT time_bucket(%s::interval, time) AS bucket, first(price, time) AS open,
                                       max(price) AS high, min(price) AS low, last(price, time) AS close,
                                       sum(qty) AS volume
                                FROM {S}.trades WHERE market_id = %s GROUP BY 1 ORDER BY 1""",
                            (width, a)).fetchall()
        assert cagg == raw, view
        assert sum(x["volume"] for x in cagg) == sum(1 + i % 3 for i in range(24))


def test_realtime_aggregation_sees_fresh_trades(make_race, house, make_user):
    u = make_user()
    r = make_race()
    a = r["markets"]["Alice"]
    trade(a, 77, 9, db.utcnow(), u["id"], house)
    with db.pool().connection() as c:
        row = c.execute(f"SELECT close, volume FROM {S}.candles_5m WHERE market_id = %s ORDER BY bucket DESC LIMIT 1",
                        (a,)).fetchone()
    assert row == {"close": 77, "volume": 9}


# ---------------------------------------------------------------- compression

def test_compressed_chunk_still_queryable(make_race):
    """Ticks in an old chunk that only holds our rows: compress it, read through it, decompress."""
    r = make_race(created_ago=timedelta(days=21))
    mid = r["markets"]["Alice"]
    t0 = floor(db.utcnow() - timedelta(days=20), timedelta(days=1)) + timedelta(hours=12)
    for i in range(50):
        db.record_tick(mid, 40 + i % 5, 50 + i % 5, None, time=t0 + timedelta(minutes=i))
    with db.pool().connection() as c:
        ch = c.execute("""SELECT chunk_schema, chunk_name, range_start, range_end, is_compressed
                          FROM timescaledb_information.chunks
                          WHERE hypertable_schema = %s AND hypertable_name = 'price_ticks'
                            AND range_start <= %s AND range_end > %s""", (S, t0, t0)).fetchone()
        others = c.execute(f"""SELECT count(*) AS n FROM {S}.price_ticks
                               WHERE time >= %s AND time < %s AND market_id <> %s""",
                           (ch["range_start"], ch["range_end"], mid)).fetchone()["n"]
    if others:
        pytest.skip("chunk holds rows we didn't create; not compressing it")
    name = f"{ch['chunk_schema']}.{ch['chunk_name']}"
    try:
        if not ch["is_compressed"]:
            autocommit(sql.SQL("SELECT compress_chunk({})").format(sql.Literal(name)))
        with db.pool().connection() as c:
            state = c.execute("SELECT is_compressed FROM timescaledb_information.chunks "
                              "WHERE chunk_schema = %s AND chunk_name = %s",
                              (ch["chunk_schema"], ch["chunk_name"])).fetchone()
        assert state["is_compressed"] is True
        # last tick at i=49: bid 44, ask 54 -> mid 49
        assert db.price_at(mid, t0 + timedelta(hours=2)) == 49
        assert db.price_at(mid, t0 + timedelta(minutes=2, seconds=30)) == 47
        pts = db.price_history(mid, "1h", t0 - timedelta(hours=1))
        assert None not in [p["close"] for p in pts]
        stats = db.timescale_stats()
        assert stats["compression"]["number_compressed_chunks"] >= 1
    finally:
        autocommit(sql.SQL("SELECT decompress_chunk({}, if_compressed => true)").format(sql.Literal(name)))


# ---------------------------------------------------------------- equity / leaderboard

def test_equity_with_long_and_short(make_race, make_user):
    u = make_user()
    r = make_race()
    a, b = r["markets"]["Alice"], r["markets"]["Bob"]  # prices 50, 30 (ai_prior)
    db.add_position(u["id"], a, 10, 400)
    db.add_position(u["id"], b, -20, -500)
    assert db.equity(u["id"]) == 100000 + 10 * 50 - 20 * 30
    db.record_tick(b, 8, 12, None)  # Bob falls to 10: the short gains
    assert db.equity(u["id"]) == 100000 + 10 * 50 - 20 * 10
    assert db.equity(-1) == 0


def test_leaderboard(make_race, make_user):
    rich, poor = make_user("rich"), make_user("poor")
    db.adjust_cash(rich["id"], 10 ** 9)
    db.adjust_cash(poor["id"], -10 ** 5)
    board = db.leaderboard(10000)
    names = [x["username"] for x in board]
    assert names[0] == rich["username"] and board[0]["equity"] == 100000 + 10 ** 9
    assert names.index(poor["username"]) > 0
    assert [x["rank"] for x in board] == list(range(1, len(board) + 1))
    assert all(board[i]["equity"] >= board[i + 1]["equity"] for i in range(len(board) - 1))
    assert "house" not in names


def test_equity_history(make_user):
    u = make_user()
    hour = floor(db.utcnow() - timedelta(hours=3), timedelta(hours=1))
    for minutes, eq in [(5, 100100), (50, 100200), (65, 99000), (130, 101000)]:
        db.insert_portfolio_snapshot(u["id"], 100000, eq, time=hour + timedelta(minutes=minutes))
    hist = db.equity_history([u["id"]], "1h")[u["id"]]
    assert [p["equity"] for p in hist] == [100200, 99000, 101000]
    assert hist[0]["t"] == db.iso(hour)
    assert db.equity_history([], "1h") == {}


def test_market_stats(make_race, house, make_user):
    u = make_user()
    r = make_race(created_ago=timedelta(days=2))
    a = r["markets"]["Alice"]
    now = db.utcnow()
    trade(a, 40, 5, now - timedelta(hours=30), u["id"], house)
    trade(a, 55, 7, now - timedelta(hours=1), u["id"], house)
    s = db.market_stats([a, r["markets"]["Bob"]])
    assert s[a]["price_24h_ago"] == 40 and s[a]["volume_24h"] == 7
    assert s[r["markets"]["Bob"]]["price_24h_ago"] == 30 and s[r["markets"]["Bob"]]["volume_24h"] == 0
    young = make_race(created_ago=timedelta(hours=1))
    assert db.market_stats([young["markets"]["Alice"]])[young["markets"]["Alice"]]["price_24h_ago"] is None
