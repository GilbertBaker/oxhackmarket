"""CRUD, constraints, transactions and concurrency for the plain tables."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import psycopg
import pytest

import db
from conftest import RON

S = db.SCHEMA


# ---------------------------------------------------------------- users

def test_create_and_get_user(make_user):
    u = make_user("alice")
    assert u["cash"] == 100000 and u["is_admin"] is False and u["is_bot"] is False
    assert db.get_user(u["id"])["username"] == u["username"]
    assert db.get_user_by_username(u["username"])["id"] == u["id"]
    assert db.get_user(-1) is None and db.get_user_by_username("test_nobody_here") is None


def test_admin_flag(make_user):
    assert make_user("adm", is_admin=True)["is_admin"] is True


def test_username_unique(make_user):
    u = make_user("dup")
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.create_user(u["username"], "x")


def test_adjust_cash(make_user):
    u = make_user()
    db.adjust_cash(u["id"], -1234)
    db.adjust_cash(u["id"], 34)
    assert db.get_user(u["id"])["cash"] == 100000 - 1200


def test_house_bot(house):
    assert db.ensure_bot_user() == house
    h = db.get_user(house)
    assert h["is_bot"] and h["cash"] >= 0 and h["username"] == "house"
    assert house not in [u["id"] for u in db.list_users()]
    assert house in [u["id"] for u in db.list_users(include_bots=True)]


# ---------------------------------------------------------------- races / markets

def test_race_create_get_find(make_race):
    r = make_race()
    race = db.get_race(r["id"])
    assert race["position"] == "President" and race["status"] == "open" and race["seats"] == 1
    assert db.find_race(r["election"], "President")["id"] == r["id"]
    assert db.find_race(r["election"], "Librarian") is None


def test_race_unique_election_position(make_race):
    r = make_race()
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.create_race({"election": r["election"], "position": "President",
                        "resolution_criteria": "x", "closes_at": db.utcnow()})


def test_market_unique_candidate(make_race):
    r = make_race()
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.create_market({"race_id": r["id"], "candidate": "Alice", "question": "q",
                          "resolution_criteria": "x", "closes_at": db.utcnow()})


def test_market_requires_existing_race():
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.create_market({"race_id": -1, "candidate": "X", "question": "q",
                          "resolution_criteria": "x", "closes_at": db.utcnow()})


def test_race_ordering(make_race):
    """closes_at first, then President, Librarian, Treasurer, Secretary, then others A-Z."""
    closes = db.utcnow() + timedelta(days=30)
    order = ["President", "Librarian", "Treasurer", "Secretary", "Alpha Committee", "Standing Committee"]
    ids = {p: make_race(position=p, closes_at=closes)["id"] for p in reversed(order)}
    early = make_race(position="Zeta Committee", closes_at=closes - timedelta(days=1))["id"]
    wanted = set(ids.values()) | {early}
    mine = [r["id"] for r in db.list_races("all") if r["id"] in wanted]
    assert mine == [early] + [ids[p] for p in order]


def test_list_races_status_filter(make_race):
    open_r = make_race()
    prop_r = make_race(status="proposed")
    open_ids = {r["id"] for r in db.list_races("open")}
    assert open_r["id"] in open_ids and prop_r["id"] not in open_ids
    both = {r["id"] for r in db.list_races(["open", "proposed"])}
    assert {open_r["id"], prop_r["id"]} <= both


def test_update_race_propagates_to_markets(make_race):
    r = make_race()
    new_close = db.utcnow() + timedelta(days=1)
    db.update_race(r["id"], status="closed", closes_at=new_close, description="changed")
    race = db.get_race(r["id"])
    assert race["status"] == "closed" and race["description"] == "changed"
    for m in db.race_markets(r["id"]):
        assert m["status"] == "closed"
        assert abs((m["closes_at"] - new_close).total_seconds()) < 1


def test_update_race_bad_column_changes_nothing(make_race):
    r = make_race()
    with pytest.raises(ValueError):
        db.update_race(r["id"], status="closed", not_a_column=1)
    assert db.get_race(r["id"])["status"] == "open"
    assert {m["status"] for m in db.race_markets(r["id"])} == {"open"}


def test_update_race_inside_rolled_back_transaction(make_race):
    r = make_race()
    with pytest.raises(RuntimeError):
        with db.transaction() as c:
            db.update_race(r["id"], status="resolved", conn=c)
            raise RuntimeError
    assert db.get_race(r["id"])["status"] == "open"
    assert {m["status"] for m in db.race_markets(r["id"])} == {"open"}


def test_market_crud_and_filters(make_race):
    r = make_race(position="Librarian")
    a = r["markets"]["Alice"]
    m = db.get_market(a)
    assert m["candidate"] == "Alice" and m["category"] == "union" and m["price"] == 50
    db.update_market(a, status="resolved", outcome="YES")
    assert db.get_market(a)["outcome"] == "YES"
    in_race = db.list_markets(race_id=r["id"])
    assert {m["candidate"] for m in in_race} == {"Alice", "Bob", RON}
    assert {m["candidate"] for m in db.list_markets(status="open", race_id=r["id"])} == {"Bob", RON}
    assert all("price" in m for m in in_race)
    with pytest.raises(ValueError):
        db.update_market(a, nonsense=1)


# ---------------------------------------------------------------- articles

def test_articles(tag):
    base = f"https://test.invalid/{tag}/"
    now = db.utcnow()
    a1 = db.insert_article({"source": "cherwell", "url": base + "1", "title": "Old",
                            "published_at": now - timedelta(days=2)})
    a2 = db.insert_article({"source": "oxstu", "url": base + "2", "title": "New",
                            "published_at": now - timedelta(hours=1)})
    assert a1 and a2
    assert db.insert_article({"source": "cherwell", "url": base + "1", "title": "dup"}) is None
    assert db.get_article(a1)["title"] == "Old"
    assert {a1, a2} <= {a["id"] for a in db.unprocessed_articles(1000)}
    db.mark_article_processed(a1)
    assert a1 not in {a["id"] for a in db.unprocessed_articles(1000)}
    before = [a["id"] for a in db.recent_articles(1000, before=now - timedelta(days=1))]
    assert a1 in before and a2 not in before
    recent = [a["id"] for a in db.recent_articles(1000)]
    assert recent.index(a2) < recent.index(a1)


# ---------------------------------------------------------------- orders / positions

def _order(oid, mid, uid, **kw):
    return {"id": oid, "market_id": mid, "user_id": uid, "side": "buy", "price": 40,
            "qty": 10, "qty_remaining": 10, "status": "open", **kw}


def test_orders(make_race, make_user):
    r, u = make_race(), make_user()
    mid = r["markets"]["Bob"]
    o1, o2 = db.next_order_id(), db.next_order_id()
    db.insert_order(_order(o1, mid, u["id"]))
    db.insert_order(_order(o2, mid, u["id"], side="sell", price=60))
    assert [o["id"] for o in db.open_orders(market_id=mid)] == [o1, o2]
    db.update_order(o1, 0, "filled")
    o = db.get_order(o1)
    assert o["status"] == "filled" and o["qty_remaining"] == 0 and o["updated_at"] >= o["created_at"]
    assert [o["id"] for o in db.open_orders(user_id=u["id"])] == [o2]
    assert [o["id"] for o in db.user_orders(u["id"])] == [o2, o1]
    assert [o["id"] for o in db.user_orders(u["id"], status="filled")] == [o1]


def test_positions(make_race, make_user):
    r, u, v = make_race(), make_user(), make_user()
    a, b = r["markets"]["Alice"], r["markets"]["Bob"]
    assert db.get_position(u["id"], a) == {"user_id": u["id"], "market_id": a, "qty": 0, "net_cost": 0}
    db.add_position(u["id"], a, 10, 500)
    db.add_position(u["id"], a, 5, 300)
    db.add_position(v["id"], a, -15, -800)
    db.add_position(u["id"], b, 3, 90)
    db.add_position(u["id"], b, -3, -90)  # back to zero
    p = db.get_position(u["id"], a)
    assert (p["qty"], p["net_cost"]) == (15, 800)
    assert sum(x["qty"] for x in db.market_positions(a)) == 0
    assert [x["user_id"] for x in db.market_positions(b)] == []
    rows = db.user_positions(u["id"])
    assert [x["market_id"] for x in rows] == [a]
    assert {"question", "candidate", "race_id", "status", "price"} <= set(rows[0])


def test_transaction_rollback(make_race, make_user):
    r, u = make_race(), make_user()
    mid = r["markets"]["Bob"]
    with pytest.raises(RuntimeError):
        with db.transaction() as c:
            oid = db.next_order_id(c)
            db.insert_order(_order(oid, mid, u["id"]), c)
            db.add_position(u["id"], mid, 10, 400, c)
            db.adjust_cash(u["id"], -400, c)
            db.insert_trade({"market_id": mid, "price": 40, "qty": 10, "buyer_id": u["id"],
                             "seller_id": u["id"], "taker_side": "buy"}, c)
            raise RuntimeError("boom")
    assert db.get_order(oid) is None
    assert db.get_position(u["id"], mid)["qty"] == 0
    assert db.get_user(u["id"])["cash"] == 100000
    assert db.last_trade_price(mid) is None


def test_transaction_commit_is_atomic_and_visible(make_race, make_user):
    r, u = make_race(), make_user()
    mid = r["markets"]["Bob"]
    with db.transaction() as c:
        oid = db.next_order_id(c)
        db.insert_order(_order(oid, mid, u["id"]), c)
        db.update_order(oid, 4, "open", c)
        db.add_position(u["id"], mid, 6, 240, c)
        db.adjust_cash(u["id"], -240, c)
    assert db.get_order(oid)["qty_remaining"] == 4
    assert db.get_position(u["id"], mid)["qty"] == 6
    assert db.get_user(u["id"])["cash"] == 100000 - 240


def test_concurrent_position_and_cash_updates(make_race, make_user):
    """20 threads x 10 updates through the pool: upserts and cash deltas must not lose writes."""
    r, u = make_race(), make_user()
    mid = r["markets"]["Alice"]

    def work(_):
        for _ in range(10):
            with db.transaction() as c:
                db.add_position(u["id"], mid, 1, 50, c)
                db.adjust_cash(u["id"], -50, c)

    with ThreadPoolExecutor(max_workers=20) as ex:
        list(ex.map(work, range(20)))
    p = db.get_position(u["id"], mid)
    assert (p["qty"], p["net_cost"]) == (200, 10000)
    assert db.get_user(u["id"])["cash"] == 100000 - 10000


# ---------------------------------------------------------------- AI briefs

def test_briefs(make_race, tag):
    r = make_race()
    b1 = db.save_brief({"race_id": r["id"], "kind": "brief", "text": "first"})
    b2 = db.save_brief({"race_id": r["id"], "kind": "brief", "text": "second"})
    db.save_brief({"race_id": r["id"], "kind": "resolution", "text": "res",
                   "suggested_winner_ids": [r["markets"]["Alice"]], "confidence": 80})
    assert b2 > b1
    assert db.latest_brief(r["id"])["text"] == "second"
    res = db.latest_brief(r["id"], kind="resolution")
    assert res["suggested_winner_ids"] == [r["markets"]["Alice"]] and res["confidence"] == 80
    db.save_brief({"kind": "digest", "text": f"TEST {tag} digest"})
    assert db.latest_brief()["text"] == f"TEST {tag} digest"
    assert db.latest_brief(kind="digest")["kind"] == "digest"
