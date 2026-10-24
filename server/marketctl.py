"""marketctl: command-line client for a RUNNING OxMarket server (talks HTTP, so it
always sees the same engine/books as the web app).

  python server/marketctl.py races
  python server/marketctl.py race 1
  python server/marketctl.py book 3
  python server/marketctl.py order 3 YES buy 10 --price 60      (omit --price = market order)
  python server/marketctl.py orders | portfolio | cancel 1042
  python server/marketctl.py create-race --election "Michaelmas 2026" --position President \
        --closes-at 2026-11-27T18:00:00Z "Jane Doe:45" "John Roe:40"          (admin; RON added)
  python server/marketctl.py approve 1 | close 1 | resolve 1 3 [4 ...]           (admin)
  python server/marketctl.py check | requote                                     (admin)
  python server/marketctl.py sim --traders 6 --minutes 10                        (demo history)

Login: --user/--password or env MARKETCTL_USER / MARKETCTL_PASSWORD (default admin/adminpw).
--register creates the account first if it doesn't exist. Server: --url or MARKETCTL_URL.
"""
import argparse
import json
import os
import random
import sys
import time

import requests


class Client:
    def __init__(self, url):
        self.url = url.rstrip("/")
        self.s = requests.Session()

    def call(self, method, path, body=None, ok=(200, 201)):
        r = self.s.request(method, self.url + path, json=body, timeout=30)
        try:
            data = r.json()
        except ValueError:
            data = {"error": r.text[:200]}
        if r.status_code not in ok:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {data.get('error', data)}")
        return data

    def login(self, user, password, register=False):
        try:
            return self.call("POST", "/api/login", {"username": user, "password": password})
        except RuntimeError:
            if not register:
                raise
            return self.call("POST", "/api/register", {"username": user, "password": password})


def show(data):
    print(json.dumps(data, indent=2, default=str))


def fmt_race(r):
    lines = [f"race {r['id']}: {r['election']} {r['position']} (seats {r['seats']}, {r['status']}, "
             f"closes {r['closes_at']}, overround {r.get('overround')})"]
    for m in r.get("candidates", []):
        lines.append(f"   market {m['id']:>4}  {m.get('price', '?'):>3}p  {m['candidate']}")
    return "\n".join(lines)


def parse_candidate(s):
    name, _, prior = s.rpartition(":")
    if name and prior.isdigit():
        return {"name": name, "prior": int(prior)}
    return {"name": s}


# ------------------------------------------------------------------ sim traders
def sim(c, args):
    """Simulated traders with drifting private beliefs, trading through the public API.
    Produces real trades/ticks so the charts and Time Machine have history."""
    rng = random.Random(args.seed)
    traders = []
    for i in range(args.traders):
        t = Client(c.url)
        t.login(f"sim_trader_{i + 1}", "simtrader", register=True)
        traders.append(t)
    beliefs = {}   # (trader index, market id) -> belief in YES pence
    end = time.time() + args.minutes * 60
    n = 0
    while time.time() < end:
        races = c.call("GET", "/api/races?status=open")["races"]
        markets = [m for r in races for m in r["candidates"]]
        if not markets:
            print("no open markets; waiting")
            time.sleep(5)
            continue
        if rng.random() < 0.05:   # occasional "news": everyone shifts their view on one market
            m = rng.choice(markets)
            shock = rng.choice([-1, 1]) * rng.randint(5, 12)
            for k in list(beliefs):
                if k[1] == m["id"]:
                    beliefs[k] = max(2, min(98, beliefs[k] + shock))
            print(f"news: {m['candidate']} {shock:+d}")
        i = rng.randrange(len(traders))
        m = rng.choice(markets)
        key = (i, m["id"])
        b = beliefs.get(key, m.get("price") or 50) + rng.gauss(0, 2)
        b = beliefs[key] = max(2, min(98, b))
        book = c.call("GET", f"/api/markets/{m['id']}/book?depth=1")
        bid = book["bids"][0]["price"] if book["bids"] else None
        ask = book["asks"][0]["price"] if book["asks"] else None
        qty = rng.randint(5, 30)
        if ask is not None and b > ask + 1:
            order = {"outcome": "YES", "side": "buy", "price": ask}
        elif bid is not None and b < bid - 1:
            order = {"outcome": "NO", "side": "buy", "price": 100 - bid}
        else:   # rest a passive order near belief
            p = int(round(b)) + rng.choice([-1, 0])
            order = {"outcome": "YES", "side": "buy", "price": max(1, min(99, p))} if rng.random() < 0.5 \
                else {"outcome": "NO", "side": "buy", "price": max(1, min(99, 100 - p - 1))}
        try:
            r = traders[i].call("POST", "/api/orders",
                                {"market_id": m["id"], "type": "limit", "qty": qty, **order})
            n += 1
            if r["fills"]:
                print(f"sim_trader_{i + 1}: {order['outcome']} {qty} on {m['candidate']} -> {r['fills']}")
        except RuntimeError as e:
            print(f"sim_trader_{i + 1}: {e}")
        time.sleep(args.interval)
    print(f"sim done: {n} orders")


# ------------------------------------------------------------------------- main
def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default=os.environ.get("MARKETCTL_URL", f"http://localhost:{os.environ.get('PORT', '5000')}"))
    p.add_argument("--user", default=os.environ.get("MARKETCTL_USER", "admin"))
    p.add_argument("--password", default=os.environ.get("MARKETCTL_PASSWORD", "adminpw"))
    p.add_argument("--register", action="store_true", help="register the user if login fails")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("races").add_argument("--status", default="open")
    sub.add_parser("race").add_argument("race_id", type=int)
    sub.add_parser("book").add_argument("market_id", type=int)
    o = sub.add_parser("order")
    o.add_argument("market_id", type=int)
    o.add_argument("outcome", choices=["YES", "NO"])
    o.add_argument("side", choices=["buy", "sell"])
    o.add_argument("qty", type=int)
    o.add_argument("--price", type=int)
    sub.add_parser("orders").add_argument("--status", default="open")
    sub.add_parser("portfolio")
    sub.add_parser("cancel").add_argument("order_id", type=int)
    cr = sub.add_parser("create-race")
    cr.add_argument("--election", required=True)
    cr.add_argument("--position", required=True)
    cr.add_argument("--closes-at", required=True)
    cr.add_argument("--seats", type=int, default=1)
    cr.add_argument("--criteria", default="Official results announced by the Oxford Union / reported by Cherwell")
    cr.add_argument("candidates", nargs="+", help='"Name" or "Name:prior"')
    sub.add_parser("approve").add_argument("race_id", type=int)
    sub.add_parser("close").add_argument("race_id", type=int)
    rs = sub.add_parser("resolve")
    rs.add_argument("race_id", type=int)
    rs.add_argument("winner_market_ids", type=int, nargs="+")
    sub.add_parser("check")
    sub.add_parser("requote")
    sm = sub.add_parser("sim")
    sm.add_argument("--traders", type=int, default=6)
    sm.add_argument("--minutes", type=float, default=10)
    sm.add_argument("--interval", type=float, default=1.0)
    sm.add_argument("--seed", type=int, default=None)
    a = p.parse_args(argv)

    c = Client(a.url)
    public = a.cmd in ("races", "race", "book")
    if not public:
        c.login(a.user, a.password, register=a.register)

    if a.cmd == "races":
        for r in c.call("GET", f"/api/races?status={a.status}")["races"]:
            print(fmt_race(r))
    elif a.cmd == "race":
        print(fmt_race(c.call("GET", f"/api/races/{a.race_id}")["race"]))
    elif a.cmd == "book":
        b = c.call("GET", f"/api/markets/{a.market_id}/book")
        for l in reversed(b["asks"]):
            print(f"        ASK {l['price']:>3} x {l['qty']}")
        for l in b["bids"]:
            print(f"  BID {l['price']:>3} x {l['qty']}")
    elif a.cmd == "order":
        body = {"market_id": a.market_id, "outcome": a.outcome, "side": a.side, "qty": a.qty,
                "type": "limit" if a.price is not None else "market"}
        if a.price is not None:
            body["price"] = a.price
        show(c.call("POST", "/api/orders", body))
    elif a.cmd == "orders":
        show(c.call("GET", f"/api/orders?status={a.status}"))
    elif a.cmd == "portfolio":
        show(c.call("GET", "/api/portfolio"))
    elif a.cmd == "cancel":
        show(c.call("DELETE", f"/api/orders/{a.order_id}"))
    elif a.cmd == "create-race":
        r = c.call("POST", "/api/admin/races", {
            "election": a.election, "position": a.position, "seats": a.seats, "closes_at": a.closes_at,
            "resolution_criteria": a.criteria, "candidates": [parse_candidate(x) for x in a.candidates]})
        print(fmt_race(r["race"]))
    elif a.cmd == "approve":
        print(fmt_race(c.call("POST", f"/api/admin/races/{a.race_id}/approve", {})["race"]))
    elif a.cmd == "close":
        print(fmt_race(c.call("POST", f"/api/admin/races/{a.race_id}/close")["race"]))
    elif a.cmd == "resolve":
        print(fmt_race(c.call("POST", f"/api/admin/races/{a.race_id}/resolve",
                              {"winner_market_ids": a.winner_market_ids})["race"]))
    elif a.cmd == "check":
        r = c.call("GET", "/api/admin/invariants")
        print("OK: all invariants hold" if r["ok"] else "\n".join(["PROBLEMS:"] + r["problems"]))
        return 0 if r["ok"] else 1
    elif a.cmd == "requote":
        show(c.call("POST", "/api/admin/mm/requote"))
    elif a.cmd == "sim":
        sim(c, a)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, requests.ConnectionError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
