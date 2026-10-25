"""B's HTTP endpoints against the real database, checking COLLAB 9 shapes."""
import re
from datetime import timedelta

import pytest
from flask import Flask

import data_api
import db
from conftest import RON, refresh_candles, trade

ISO = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
USER_KEYS = {"id", "username", "cash", "available", "is_admin"}
MARKET_KEYS = {"id", "race_id", "candidate", "question", "description", "resolution_criteria", "category",
               "source_name", "source_url", "closes_at", "status", "outcome", "price", "change_24h",
               "volume_24h", "ai_prior", "created_at"}
RACE_KEYS = {"id", "election", "position", "seats", "closes_at", "status", "source_name", "source_url",
             "resolution_criteria", "winners", "overround", "candidates"}


@pytest.fixture
def client(tag, monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAMES", f"someone,test_{tag}_boss")
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(data_api.bp)
    return app.test_client()


def register(client, name, password="secret1"):
    return client.post("/api/register", json={"username": name, "password": password})


# ---------------------------------------------------------------- auth

def test_register_login_logout_me(client, tag):
    name = f"test_{tag}_api"
    assert client.get("/api/me").status_code == 401
    r = register(client, name)
    assert r.status_code == 201
    user = r.get_json()["user"]
    assert set(user) == USER_KEYS and user["cash"] == 100000 and user["available"] == 100000
    assert user["is_admin"] is False
    assert client.get("/api/me").get_json()["user"]["id"] == user["id"]
    assert client.post("/api/logout").get_json() == {"ok": True}
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/login", json={"username": name, "password": "nope!!"}).status_code == 401
    r = client.post("/api/login", json={"username": name, "password": "secret1"})
    assert r.status_code == 200 and r.get_json()["user"]["username"] == name
    assert client.get("/api/me").status_code == 200
    stored = db.get_user_by_username(name)["pw_hash"]
    assert "secret1" not in stored


def test_register_validation(client, tag):
    assert register(client, "ab").status_code == 400
    assert register(client, "bad name!").status_code == 400
    assert register(client, "x" * 21).status_code == 400
    assert register(client, f"test_{tag}_pw", "12345").status_code == 400
    assert register(client, f"test_{tag}_dup").status_code == 201
    r = register(client, f"test_{tag}_dup")
    assert r.status_code == 400 and "error" in r.get_json()


def test_admin_username(client, tag):
    assert register(client, f"test_{tag}_boss").get_json()["user"]["is_admin"] is True


def test_house_cannot_log_in(client):
    assert client.post("/api/login", json={"username": "house", "password": "!unusable"}).status_code == 401


# ---------------------------------------------------------------- races / markets

@pytest.fixture
def race(make_race, house, make_user):
    u = make_user()
    r = make_race(position="President", created_ago=timedelta(days=2))
    now = db.utcnow()
    trade(r["markets"]["Bob"], 30, 4, now - timedelta(hours=30), u["id"], house)
    trade(r["markets"]["Bob"], 62, 5, now - timedelta(hours=2), u["id"], house)
    refresh_candles(now - timedelta(hours=31))
    return r


def test_races_list_and_detail(client, race, make_race):
    hidden = make_race(position="Librarian", status="proposed")
    races = client.get("/api/races").get_json()["races"]
    ids = [x["id"] for x in races]
    assert race["id"] in ids and hidden["id"] not in ids
    body = client.get(f"/api/races/{race['id']}").get_json()["race"]
    assert RACE_KEYS <= set(body)
    assert ISO.match(body["closes_at"])
    cands = body["candidates"]
    assert [c["candidate"] for c in cands] == ["Bob", "Alice", RON]  # sorted by price desc
    assert all(MARKET_KEYS <= set(c) for c in cands)
    assert body["overround"] == sum(c["price"] for c in cands) == 62 + 50 + 20
    assert body["winners"] == []
    bob = cands[0]
    assert bob["change_24h"] == 62 - 30 and bob["volume_24h"] == 5
    assert client.get(f"/api/races/{hidden['id']}").status_code == 404
    assert client.get("/api/races/999999999").status_code == 404
    assert client.get("/api/races?status=bogus").status_code == 400
    assert race["id"] in [x["id"] for x in client.get("/api/races?status=all").get_json()["races"]]
    assert race["id"] not in [x["id"] for x in client.get("/api/races?status=closed").get_json()["races"]]


def test_race_history(client, race):
    body = client.get(f"/api/races/{race['id']}/history?bucket=1h").get_json()
    assert body["bucket"] == "1h"
    series = {s["candidate"]: s for s in body["series"]}
    assert set(series) == {"Alice", "Bob", RON}
    for s in body["series"]:
        assert set(s) == {"market_id", "candidate", "points"}
        assert all(set(p) == {"t", "close"} and ISO.match(p["t"]) and isinstance(p["close"], int)
                   for p in s["points"])
    assert series["Bob"]["points"][-1]["close"] == 62
    since = db.iso(db.utcnow() - timedelta(hours=3))
    short = client.get(f"/api/races/{race['id']}/history?bucket=5m&since={since}").get_json()
    assert 35 <= len(short["series"][0]["points"]) <= 37
    assert client.get(f"/api/races/{race['id']}/history?bucket=2m").status_code == 400
    assert client.get(f"/api/races/{race['id']}/history?since=yesterday").status_code == 400


def test_markets_list(client, race):
    body = client.get(f"/api/markets?race_id={race['id']}").get_json()
    ms = {m["candidate"]: m for m in body["markets"]}
    assert set(ms) == {"Alice", "Bob", RON}
    for m in ms.values():
        assert MARKET_KEYS | {"sparkline"} <= set(m)
        assert 23 <= len(m["sparkline"]) <= 24 and all(isinstance(v, int) for v in m["sparkline"])
    assert ms["Bob"]["sparkline"][-1] == 62 and ms["Bob"]["sparkline"][0] == 30
    assert client.get("/api/markets?status=nope").status_code == 400


def test_market_detail_trades_history(client, race):
    bob = race["markets"]["Bob"]
    body = client.get(f"/api/markets/{bob}").get_json()
    assert MARKET_KEYS <= set(body["market"]) and body["market"]["price"] == 62
    assert body["race"]["id"] == race["id"] and "candidates" not in body["race"]
    trades = client.get(f"/api/markets/{bob}/trades").get_json()["trades"]
    assert [t["price"] for t in trades] == [62, 30]
    assert all(set(t) == {"time", "price", "qty", "taker_side"} and ISO.match(t["time"]) for t in trades)
    assert len(client.get(f"/api/markets/{bob}/trades?limit=1").get_json()["trades"]) == 1
    hist = client.get(f"/api/markets/{bob}/history?bucket=1h").get_json()
    assert hist["bucket"] == "1h"
    assert all(set(p) == {"t", "open", "high", "low", "close", "volume"} for p in hist["points"])
    assert None not in [p["close"] for p in hist["points"]]
    assert hist["points"][-1]["close"] == 62
    assert client.get(f"/api/markets/{bob}/history").get_json()["bucket"] == "5m"
    assert client.get("/api/markets/999999999").status_code == 404


# ---------------------------------------------------------------- time machine / leaderboard / stats

def test_timemachine(client, race, tag):
    db.insert_article({"source": "cherwell", "url": f"https://test.invalid/{tag}/tm", "title": "TM article",
                       "published_at": db.utcnow() - timedelta(hours=10)})
    t = db.iso(db.utcnow() - timedelta(hours=5))
    body = client.get(f"/api/timemachine?t={t}").get_json()
    assert set(body) == {"t", "races", "articles"} and body["t"] == t
    mine = [r for r in body["races"] if r["id"] == race["id"]][0]
    assert {c["candidate"]: c["price"] for c in mine["candidates"]} == {"Alice": 50, "Bob": 30, RON: 20}
    assert len(body["articles"]) <= 10
    assert all(set(a) == {"title", "url", "source", "published_at"} for a in body["articles"])
    assert "TM article" in [a["title"] for a in body["articles"]]
    now_body = client.get("/api/timemachine").get_json()
    mine = [r for r in now_body["races"] if r["id"] == race["id"]][0]
    assert mine["candidates"][0] == {"market_id": race["markets"]["Bob"], "candidate": "Bob", "price": 62}
    assert client.get("/api/timemachine?t=not-a-time").status_code == 400
    rng = client.get("/api/timemachine/range").get_json()
    assert ISO.match(rng["start"]) and ISO.match(rng["end"]) and rng["start"] <= db.iso(race["created"])


def test_leaderboard_endpoints(client, tag):
    register(client, f"test_{tag}_lb")
    body = client.get("/api/leaderboard").get_json()
    assert all(set(x) == {"rank", "username", "equity"} for x in body["leaders"])
    assert len(body["leaders"]) <= 20
    hist = client.get("/api/leaderboard/history?bucket=1h&top=3").get_json()
    assert len(hist["series"]) <= 3
    assert all(set(s) == {"username", "points"} for s in hist["series"])
    assert client.get("/api/leaderboard/history?bucket=1m").status_code == 400


def test_stats(client):
    body = client.get("/api/stats").get_json()
    assert {h["name"] for h in body["hypertables"]} == {"trades", "price_ticks", "portfolio_snapshots",
                                                        "admin_actions"}
    assert {c["name"] for c in body["continuous_aggregates"]} == {"candles_5m", "candles_1h"}
    assert all(c["realtime"] for c in body["continuous_aggregates"])
    assert len(body["jobs"]) == 4
    assert set(body["rows"]) == {"trades", "price_ticks", "portfolio_snapshots"}
