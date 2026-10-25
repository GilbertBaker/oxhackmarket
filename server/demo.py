"""Demo prep: a known login plus synthetic price history for every open market.

    python server/demo.py                 demo login + 14 days of history (both idempotent)
    python server/demo.py --days 7        shorter history
    python server/demo.py --user-only     just the demo login
    python server/demo.py --history-only  just the history

Login: demo / oxhack2026 (an admin, so the admin portal works too).

History: per market, a random walk in log-odds space with a few "news" jumps, pinned
(Brownian bridge) so it lands exactly on the market's first real price, so there is no
step where the synthetic data meets the live data. Outcomes of a match / multi race are
renormalised at every step so they keep summing to the same total. Written as
price_ticks (mid +/- 1) plus house-vs-house trades, which give the candles volume
without moving anyone's cash or positions. Races and markets are back-dated to the start
of the window so charts, sparklines and the Time Machine all show it.

A race is skipped if it already has synthetic trades (house on both sides), so re-running
is safe. Run it with the app stopped or restart the app afterwards (the MM bot keeps
in-memory fair values).
"""
import argparse
import math
import random
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from werkzeug.security import generate_password_hash  # noqa: E402

import db  # noqa: E402

S = db.SCHEMA
DEMO_USER, DEMO_PASSWORD = "demo", "oxhack2026"
STEP = timedelta(minutes=10)
DAILY_VOL = 0.30          # log-odds per sqrt(day)


def ensure_demo_user():
    user = db.get_user_by_username(DEMO_USER)
    if user:
        db._exec(f"UPDATE {S}.users SET pw_hash = %s, is_admin = true WHERE id = %s",
                 (generate_password_hash(DEMO_PASSWORD), user["id"]))
        print(f"demo user exists (#{user['id']}), password reset")
    else:
        user = db.create_user(DEMO_USER, generate_password_hash(DEMO_PASSWORD), is_admin=True)
        print(f"created demo user #{user['id']}")
    print(f"  login: {DEMO_USER} / {DEMO_PASSWORD}")


def _logit(p):
    p = min(max(p / 100.0, 0.01), 0.99)
    return math.log(p / (1 - p))


def _walk(n, start, end, rng):
    """n+1 log-odds points from start to exactly end: random walk + jumps, bridged."""
    sigma = DAILY_VOL * math.sqrt(STEP / timedelta(days=1))
    jumps = {rng.randrange(1, n): rng.gauss(0, 0.6) for _ in range(rng.randint(1, 3))}
    b = [0.0]
    for i in range(1, n + 1):
        b.append(b[-1] + rng.gauss(0, sigma) + jumps.get(i, 0.0))
    return [start + (end - start) * i / n + b[i] - b[n] * i / n for i in range(n + 1)]


def _first_obs(market_ids):
    row = db._one(f"""SELECT least((SELECT min(time) FROM {S}.price_ticks WHERE market_id = ANY(%(ids)s)),
                                   (SELECT min(time) FROM {S}.trades WHERE market_id = ANY(%(ids)s))) AS t""",
                  {"ids": market_ids})
    return row["t"]


def synth_race(race, markets, start, house, rng):
    ids = [m["id"] for m in markets]
    end = _first_obs(ids) or db.utcnow()
    end = end - timedelta(seconds=1)
    if end - start < STEP * 10:
        print(f"  skip     #{race['id']} (window too short)")
        return 0, 0
    targets = db.prices_at(ids, end + timedelta(seconds=2))
    n = int((end - start) / STEP)
    paths = {}
    for m in markets:
        tgt = targets.get(m["id"]) or m.get("ai_prior") or 50
        open_ = _logit(m.get("ai_prior") or tgt) + rng.gauss(0, 0.7)
        paths[m["id"]] = [1 / (1 + math.exp(-x)) for x in _walk(n, open_, _logit(tgt), rng)]
    total = sum((targets.get(i) or 50) for i in ids) / 100.0
    normalise = race["kind"] != "binary" and len(ids) > 1

    ticks, trades = [], []
    for i in range(n + 1):
        t = start + STEP * i
        probs = {mid: paths[mid][i] for mid in ids}
        if normalise:
            s = sum(probs.values())
            probs = {mid: p * total / s for mid, p in probs.items()}
        for mid, p in probs.items():
            mid_p = int(min(max(round(p * 100), 2), 98)) if i < n else int(targets.get(mid) or 50)
            ticks.append((t, mid, max(mid_p - 1, 1), min(mid_p + 1, 99), mid_p))
            if rng.random() < 0.18:   # a trade roughly every hour per market
                tt = t + timedelta(seconds=rng.randint(0, int(STEP.total_seconds()) - 1))
                if tt < end:
                    side = rng.choice(("buy", "sell"))
                    px = min(max(mid_p + (1 if side == "buy" else -1), 1), 99)
                    trades.append((tt, mid, px, rng.choice((5, 10, 10, 20, 25, 50, 100)), house, house, side))

    with db.pool().connection() as c, c.transaction():
        with c.cursor() as cur:
            with cur.copy(f"COPY {S}.price_ticks (time, market_id, best_bid, best_ask, last_price) FROM STDIN") as cp:
                for r in ticks:
                    cp.write_row(r)
            with cur.copy(f"""COPY {S}.trades (time, market_id, price, qty, buyer_id, seller_id, taker_side)
                              FROM STDIN""") as cp:
                for r in trades:
                    cp.write_row(r)
            cur.execute(f"UPDATE {S}.races SET created_at = %s WHERE id = %s AND created_at > %s",
                        (start, race["id"], start))
            cur.execute(f"UPDATE {S}.markets SET created_at = %s WHERE id = ANY(%s) AND created_at > %s",
                        (start, ids, start))
    print(f"  history  #{race['id']} {race['position'][:60]}: {len(ticks)} ticks, {len(trades)} trades")
    return len(ticks), len(trades)


def synth_history(days, seed=None):
    rng = random.Random(seed)
    house = db.ensure_bot_user()
    start = db.utcnow().replace(second=0, microsecond=0) - timedelta(days=days)
    races = db._all(f"""SELECT id, kind, position FROM {S}.races
                        WHERE status IN ('open', 'closed') ORDER BY id""")
    done = {r["race_id"] for r in db._all(
        f"""SELECT DISTINCT m.race_id FROM {S}.trades t JOIN {S}.markets m ON m.id = t.market_id
            WHERE t.buyer_id = %(h)s AND t.seller_id = %(h)s""", {"h": house})}
    nt = ntr = 0
    for race in races:
        if race["id"] in done:
            print(f"  exists   #{race['id']} {race['position'][:60]}")
            continue
        markets = db._all(f"SELECT id, ai_prior FROM {S}.markets WHERE race_id = %s ORDER BY id", (race["id"],))
        if markets:
            a, b = synth_race(race, markets, start, house, rng)
            nt, ntr = nt + a, ntr + b
    if nt:
        print("refreshing candle aggregates...")
        db.refresh_candles(start)
    print(f"done: {nt} ticks, {ntr} trades since {start:%Y-%m-%d %H:%M} UTC")


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--user-only", action="store_true")
    ap.add_argument("--history-only", action="store_true")
    args = ap.parse_args(argv)
    db.ensure_schema()
    if not args.history_only:
        ensure_demo_user()
    if not args.user_only:
        synth_history(args.days, args.seed)


if __name__ == "__main__":
    main(sys.argv[1:])
