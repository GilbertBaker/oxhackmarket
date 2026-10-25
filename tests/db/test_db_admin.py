"""Admin portal HTTP routes against the real database: create (3 kinds), drafts, edit,
approve / reject, close / resolve, settings, reference data, articles, audit log."""
from datetime import timedelta

import pytest
from flask import Flask

import admin_api
import data_api
import db
import trading
import trading_api


@pytest.fixture(scope="module")
def _engine(tag):
    trading.startup()   # once: replays open orders into this process's engine


@pytest.fixture
def app(tag, monkeypatch, _engine):
    monkeypatch.setenv("ADMIN_USERNAMES", f"test_{tag}_boss")
    a = Flask(__name__)
    a.secret_key = "test"
    a.register_blueprint(data_api.bp)
    a.register_blueprint(trading_api.bp)
    admin_api.init_app(a)
    return a


@pytest.fixture
def admin(app, tag):
    c = app.test_client()
    r = c.post("/api/register", json={"username": f"test_{tag}_boss", "password": "secret1"})
    if r.status_code != 201:
        c.post("/api/login", json={"username": f"test_{tag}_boss", "password": "secret1"})
    return c


def _close(days=5):
    return db.iso(db.utcnow() + timedelta(days=days))


def test_non_admin_refused(app, tag):
    c = app.test_client()
    assert c.get("/api/admin/races").status_code == 401
    c.post("/api/register", json={"username": f"test_{tag}_pleb", "password": "secret1"})
    assert c.get("/api/admin/races").status_code == 403
    assert c.post("/api/admin/races", json={}).status_code == 403
    assert c.get("/api/meta").status_code == 200


def test_binary_draft_lifecycle(admin, tag):
    term = db.current_term()["name"]
    q = f"TEST {tag} will it happen?"
    r = admin.post("/api/admin/races", json={"kind": "binary", "status": "proposed", "election": term,
                                             "category": "union", "position": q, "closes_at": _close(), "prior": 35})
    assert r.status_code == 201, r.get_json()
    race = r.get_json()["race"]
    rid = race["id"]
    assert (race["kind"], race["category"], race["status"]) == ("binary", "union", "proposed")
    assert [c["candidate"] for c in race["candidates"]] == ["Yes"] and race["candidates"][0]["ai_prior"] == 35
    assert rid in [x["id"] for x in admin.get("/api/admin/races?status=proposed").get_json()["races"]]
    assert admin.post("/api/admin/races", json={"kind": "binary", "election": term, "position": q,
                                                "closes_at": _close()}).status_code == 400   # duplicate
    r = admin.patch(f"/api/admin/races/{rid}", json={"description": "edited", "closes_at": _close(6)})
    assert r.status_code == 200 and r.get_json()["race"]["description"] == "edited"
    assert admin.patch(f"/api/admin/races/{rid}", json={"position": "x"}).status_code == 400
    assert admin.post(f"/api/admin/races/{rid}/approve").get_json()["race"]["status"] == "open"
    assert admin.post(f"/api/admin/races/{rid}/close").status_code == 200
    r = admin.post(f"/api/admin/races/{rid}/resolve", json={"winner_market_ids": []})
    assert r.status_code == 200 and r.get_json()["race"]["status"] == "resolved"
    assert db.race_markets(rid)[0]["outcome"] == "NO"
    assert admin.patch(f"/api/admin/races/{rid}", json={"description": "late"}).status_code == 400
    acts = admin.get(f"/api/admin/actions?race_id={rid}").get_json()["actions"]
    assert {"POST /api/admin/races", "POST /api/admin/races/<int:race_id>/approve",
            "POST /api/admin/races/<int:race_id>/resolve"} <= {a["action"] for a in acts}
    assert all(a["admin"] == f"test_{tag}_boss" for a in acts)


def test_match_and_multi_create_and_reject(admin, tag):
    term = db.current_term()["name"]
    r = admin.post("/api/admin/races", json={
        "kind": "match", "election": term, "position": f"Men's Football Cuppers: Keble vs TEST{tag}",
        "closes_at": _close(), "status": "proposed",
        "candidates": [{"name": "Keble", "prior": 40}, {"name": "Draw", "prior": 25}, {"name": f"TEST{tag}", "prior": 35}]})
    assert r.status_code == 201, r.get_json()
    race = r.get_json()["race"]
    assert race["kind"] == "match" and race["category"] == "cuppers"
    assert admin.post(f"/api/admin/races/{race['id']}/reject").get_json()["race"]["status"] == "rejected"
    r = admin.post("/api/admin/races", json={
        "kind": "match", "election": term, "position": f"Quidditch Cuppers: A{tag} vs B{tag}", "closes_at": _close(),
        "candidates": [{"name": f"A{tag}", "prior": 50}, {"name": f"B{tag}", "prior": 50}]})
    assert r.status_code == 400 and "competition" in r.get_json()["error"]
    r = admin.post("/api/admin/races", json={
        "kind": "multi", "election": term, "position": f"TEST {tag} who wins?", "closes_at": _close(),
        "candidates": [{"name": "Ann", "prior": 50}, {"name": "Bo", "prior": 30}, {"name": "Cy", "prior": 20}]})
    assert r.status_code == 201
    race = r.get_json()["race"]
    assert race["status"] == "open" and race["category"] == "news"
    assert sorted(c["question"] for c in race["candidates"])[0] == f"TEST {tag} who wins: Ann?"
    ann = next(c["id"] for c in race["candidates"] if c["candidate"] == "Ann")
    admin.post(f"/api/admin/races/{race['id']}/close")
    assert admin.post(f"/api/admin/races/{race['id']}/resolve", json={"winner_market_ids": [ann]}).status_code == 200


def test_settings_roundtrip(admin):
    before = {s["key"]: s["value"] for s in admin.get("/api/admin/settings").get_json()["settings"]}
    assert before["mm.half_spread"] >= 1
    try:
        r = admin.put("/api/admin/settings", json={"mm.half_spread": 3})
        assert r.status_code == 200
        assert {s["key"]: s["value"] for s in r.get_json()["settings"]}["mm.half_spread"] == 3
        assert admin.put("/api/admin/settings", json={"mm.half_spread": "3"}).status_code == 400
        assert admin.put("/api/admin/settings", json={"made.up": 1}).status_code == 400
    finally:
        admin.put("/api/admin/settings", json={"mm.half_spread": before["mm.half_spread"]})


def test_reference_data_and_meta(admin, tag):
    r = admin.post("/api/admin/ref/teams", json={"name": f"TEST {tag} Hall", "kind": "other"})
    assert r.status_code == 201
    tid = r.get_json()["row"]["id"]
    try:
        assert admin.post("/api/admin/ref/teams", json={"name": f"TEST {tag} Hall"}).status_code == 400   # duplicate
        assert f"TEST {tag} Hall" in admin.get("/api/meta").get_json()["teams"]
        assert admin.patch(f"/api/admin/ref/teams/{tid}", json={"active": False}).status_code == 200
        assert f"TEST {tag} Hall" not in admin.get("/api/meta").get_json()["teams"]
        assert admin.get("/api/admin/ref/nope").status_code == 404
    finally:
        db._exec(f"DELETE FROM {db.SCHEMA}.teams WHERE id = %s", (tid,))


def test_articles_queue(admin, tag):
    aid = db.insert_article({"source": "Cherwell", "url": f"https://test.invalid/{tag}/admin", "title": f"TEST {tag}"})
    arts = admin.get("/api/admin/articles?pending=1&limit=200").get_json()["articles"]
    assert aid in [a["id"] for a in arts]
    term = db.current_term()["name"]
    r = admin.post("/api/admin/races", json={"kind": "binary", "election": term, "position": f"TEST {tag} from news?",
                                             "closes_at": _close(), "prior": 50, "article_id": aid})
    assert r.status_code == 201
    race = r.get_json()["race"]
    assert race["article_id"] == aid and race["source_url"] == f"https://test.invalid/{tag}/admin"
    assert aid not in [a["id"] for a in admin.get("/api/admin/articles?pending=1&limit=200").get_json()["articles"]]
