from flask import Flask

from test_trading import env  # noqa: F401  (fixture; also installs the fake db proxy)
import trading_api


def make_client(uid):
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(trading_api.bp)
    c = app.test_client()
    if uid is not None:
        with c.session_transaction() as s:
            s["user_id"] = uid
    return c


def test_order_flow_http(env):  # noqa: F811
    fake, eng = env
    rid, (m, ron) = fake.add_race(["Jane", "RON"])
    a, b = fake.add_user("a"), fake.add_user("b")
    admin = fake.add_user("admin", is_admin=True)

    assert make_client(None).post("/api/orders", json={}).status_code == 401
    ca, cb = make_client(a), make_client(b)

    r = ca.post("/api/orders", json={"market_id": m, "outcome": "YES", "side": "buy",
                                     "type": "limit", "price": 60, "qty": 10})
    assert r.status_code == 201
    body = r.get_json()
    assert body["order"]["qty_remaining"] == 10 and body["user"]["available"] == 100000 - 600

    r = cb.post("/api/orders", json={"market_id": m, "outcome": "NO", "side": "buy",
                                     "type": "limit", "price": 40, "qty": 4})
    assert r.get_json()["fills"] == [{"price": 60, "qty": 4}]

    assert ca.get(f"/api/markets/{m}/book").get_json() == {"bids": [{"price": 60, "qty": 6}], "asks": []}
    orders = ca.get("/api/orders").get_json()["orders"]
    assert len(orders) == 1 and orders[0]["created_at"].endswith("Z")

    pf = cb.get("/api/portfolio").get_json()
    assert pf["positions"][0]["label"] == "NO" and pf["positions"][0]["shares"] == 4

    r = ca.post("/api/orders", json={"market_id": m, "outcome": "YES", "side": "buy",
                                     "type": "limit", "price": 60, "qty": 10 ** 9})
    assert r.status_code == 400 and "error" in r.get_json()

    assert cb.delete(f"/api/orders/{orders[0]['id']}").status_code == 400   # not b's order
    assert ca.delete(f"/api/orders/{orders[0]['id']}").get_json() == {"ok": True}

    assert ca.post(f"/api/admin/races/{rid}/resolve", json={"winner_market_ids": [m]}).status_code == 403
    cadm = make_client(admin)
    race = cadm.post(f"/api/admin/races/{rid}/resolve", json={"winner_market_ids": [m]}).get_json()["race"]
    assert race["status"] == "resolved" and race["winners"] == [m]
    assert fake.users[a]["cash"] == 100000 - 240 + 400
