"""trading.py helpers (create/approve races, snapshots, invariants), mm_bot skip, and
marketctl end-to-end against a real local HTTP server."""
import threading

import pytest
from flask import Flask, jsonify, request, session
from werkzeug.serving import make_server

from test_trading import env  # noqa: F401
import marketctl
import mm_bot
import trading
import trading_api


def test_create_race_adds_ron_and_opens(env):  # noqa: F811
    fake, eng = env
    rid = trading.create_race("Michaelmas 2026", "President", "2026-11-27T18:00:00Z", "Cherwell",
                              [{"name": "Jane Doe", "prior": 50}, "John Roe"], add_ron=True)
    ms = fake.race_markets(rid)
    assert [m["candidate"] for m in ms] == ["Jane Doe", "John Roe", trading.RON_NAME]
    assert ms[0]["question"] == "Will Jane Doe be elected President (Michaelmas 2026)?"
    assert all(m["status"] == "open" for m in ms)
    assert trading.get_book(ms[0]["id"]) == {"bids": [], "asks": []}
    with pytest.raises(trading.TradingError, match="already exists"):
        trading.create_race("Michaelmas 2026", "President", "2026-11-27T18:00:00Z", "x", ["A"])
    with pytest.raises(trading.TradingError, match="already a candidate"):
        trading.add_candidate(rid, "jane doe")
    mid = trading.add_candidate(rid, "Late Entrant", 10)
    assert fake.markets[mid]["status"] == "open"


def test_cuppers_match(env):  # noqa: F811
    fake, eng = env
    rid = trading.create_race("Michaelmas 2026", "Men's Football Cuppers: Keble vs Worcester",
                              "2026-11-01T14:00:00Z", "Final score reported by the OUAFC",
                              [{"name": "Keble", "prior": 45}, {"name": "Draw", "prior": 20}, "Worcester"],
                              description="Round 1")
    ms = {m["candidate"]: m for m in fake.race_markets(rid)}
    assert set(ms) == {"Keble", "Draw", "Worcester"}            # no RON
    assert ms["Keble"]["question"] == "Will Keble beat Worcester (Men's Football Cuppers, Michaelmas 2026)?"
    assert ms["Worcester"]["question"] == "Will Worcester beat Keble (Men's Football Cuppers, Michaelmas 2026)?"
    assert ms["Draw"]["question"] == "Will Keble vs Worcester end in a draw (Men's Football Cuppers, Michaelmas 2026)?"
    assert all(m["category"] == "cuppers" for m in ms.values())
    assert fake.races[rid]["description"] == "Round 1"
    with pytest.raises(trading.TradingError, match="Duplicate"):
        trading.create_race("MT", "X: A vs B", "2026-11-01T14:00:00Z", "x", ["A", "a"])
    # resolve: one winner, everyone else NO
    u, v = fake.add_user("u"), fake.add_user("v")
    trading.place_order(u, ms["Draw"]["id"], "YES", "buy", "limit", 20, 10)
    trading.place_order(v, ms["Draw"]["id"], "NO", "buy", "limit", 80, 10)
    trading.resolve_race(rid, [ms["Draw"]["id"]])
    assert fake.users[u]["cash"] == 100000 - 200 + 1000 and fake.users[v]["cash"] == 100000 + 200 - 1000


def test_proposed_then_approve_or_reject(env):  # noqa: F811
    fake, eng = env
    rid = trading.create_race("MT26", "Librarian", "2026-11-27T18:00:00Z", "x", ["A", "B"], status="proposed")
    u = fake.add_user("u")
    m = fake.race_markets(rid)[0]["id"]
    with pytest.raises(trading.TradingError, match="proposed"):
        trading.place_order(u, m, "YES", "buy", "limit", 50, 1)
    trading.approve_race(rid)
    trading.place_order(u, m, "YES", "buy", "limit", 50, 1)
    rid2 = trading.create_race("MT26", "Treasurer", "2026-11-27T18:00:00Z", "x", ["C"], status="proposed")
    trading.reject_race(rid2)
    assert fake.races[rid2]["status"] == "rejected"
    with pytest.raises(trading.TradingError):
        trading.reject_race(rid)


def test_snapshots_and_ticks(env):  # noqa: F811
    fake, eng = env
    rid, (m, ron) = fake.add_race(["Jane", "RON"])
    a, b = fake.add_user("a"), fake.add_user("b")
    trading.place_order(a, m, "YES", "buy", "limit", 40, 10)
    trading.place_order(b, m, "YES", "sell", "limit", 45, 10)
    trading.place_order(b, m, "YES", "sell", "limit", 40, 3)
    assert trading.market_snapshot(m) == {"market_id": m, "best_bid": 40, "best_ask": 45, "last_price": 40}
    n_before = len(fake.ticks)
    assert trading.record_ticks() == 2
    assert len(fake.ticks) == n_before + 2
    assert fake.ticks[-1] == {"market_id": ron, "best_bid": None, "best_ask": None, "last_price": None}


def test_check_invariants_detects_drift(env):  # noqa: F811
    fake, eng = env
    rid, (m,) = fake.add_race(["Jane"])
    a = fake.add_user("a")
    trading.place_order(a, m, "YES", "buy", "limit", 40, 10)
    assert trading.check_invariants() == []
    fake.positions[(a, m)] = {"qty": 3, "net_cost": 0}       # corrupt the DB on purpose
    fake.users[a]["cash"] = 100
    probs = trading.check_invariants()
    assert any("positions sum" in p for p in probs) and any("available cash" in p for p in probs)
    eng.cancel(m, next(iter(fake.orders)))                   # engine loses an order
    assert any("engine book" in p for p in trading.check_invariants())


def test_mm_bot_skips_unchanged_quotes(env):  # noqa: F811
    fake, eng = env
    fake.add_race(["A", "B", "RON"], priors=[50, 40, 10])
    assert mm_bot.requote_all() == 3
    n_orders = len(fake.orders)
    assert mm_bot.requote_all() == 0           # nothing moved -> no cancels, no new orders
    assert len(fake.orders) == n_orders


# --------------------------------------------------------------- marketctl e2e
def _test_app(fake):
    """My blueprint + tiny stand-ins for B's auth/races endpoints and C's create-race."""
    app = Flask(__name__)
    app.secret_key = "t"
    app.register_blueprint(trading_api.bp)

    @app.post("/api/login")
    def login():
        u = fake.get_user_by_username(request.json["username"])
        if u is None:
            return jsonify({"error": "no such user"}), 400
        session["user_id"] = u["id"]
        return jsonify({"user": trading_api.user_json(u)})

    @app.post("/api/register")
    def register():
        u = fake.create_user(request.json["username"], "x")
        session["user_id"] = u["id"]
        return jsonify({"user": trading_api.user_json(u)}), 201

    @app.get("/api/races")
    def races():
        st = request.args.get("status", "open")
        return jsonify({"races": [trading_api.race_json(r) for r in fake.list_races(st)]})

    @app.post("/api/admin/races")
    def admin_races():
        b = request.json
        rid = trading.create_race(b["election"], b["position"], b["closes_at"], b["resolution_criteria"],
                                  b["candidates"], seats=b.get("seats", 1))
        return jsonify({"race": trading_api.race_json(fake.get_race(rid))}), 201
    return app


def test_marketctl_end_to_end(env, capsys):  # noqa: F811
    fake, eng = env
    fake.add_user("admin", is_admin=True)
    server = make_server("127.0.0.1", 0, _test_app(fake), threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        def run(*a):
            return marketctl.main(["--url", url, "--user", "admin", "--password", "x", *a])
        run("create-race", "--election", "MT26", "--position", "President",
            "--closes-at", "2026-11-27T18:00:00Z", "Jane Doe:55", "John Roe:40")
        out = capsys.readouterr().out
        assert "Jane Doe" in out and "RON" not in out
        mid = next(m["id"] for m in fake.markets.values() if m["candidate"] == "Jane Doe")
        mm_bot.requote_all()
        run("book", str(mid))
        assert "BID" in capsys.readouterr().out
        run("order", str(mid), "YES", "buy", "5")             # market order vs the house
        assert '"fills"' in capsys.readouterr().out
        run("sim", "--traders", "3", "--minutes", "0.05", "--interval", "0", "--seed", "1")
        assert "sim done" in capsys.readouterr().out
        assert len(fake.trades) > 1
        assert run("check") == 0
    finally:
        server.shutdown()
