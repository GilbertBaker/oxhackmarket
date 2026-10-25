"""Shared fixtures for the database test suite (owner B).

These tests run against the REAL Tiger Data service in .env. They only touch rows they
create, all tagged with a random per-run tag:
  usernames   test_<tag>_...
  elections   TEST <tag> ...
  article urls https://test.invalid/<tag>/...
and delete exactly those rows at the end (never TRUNCATE, never outside oxmarket).

Run from the repo root (separately from A's trading tests, which swap in a fake db module):
    .venv/bin/python -m pytest tests/db -v
"""
import itertools
import os
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))
load_dotenv(ROOT / ".env")
os.environ["DISABLE_JOBS"] = "1"

import db  # noqa: E402

if not hasattr(db, "pool"):
    pytest.exit("sys.modules['db'] is a fake (A's trading tests ran first); run tests/db on its own",
                returncode=2)

S = db.SCHEMA
RON = "RON (Re-Open Nominations)"
_race_counter = itertools.count()
_user_counter = itertools.count()


def autocommit(query, params=None):
    """Run one statement outside a transaction (needed for CALL refresh_continuous_aggregate)."""
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
        return c.execute(query, params)


def cleanup(tag):
    with db.transaction() as c:
        # admin tests post races under the real term name, tagged in the position instead
        races = [r["id"] for r in c.execute(f"SELECT id FROM {S}.races WHERE election LIKE %s OR position LIKE %s",
                                             (f"TEST {tag}%", f"%{tag}%")).fetchall()]
        markets = [r["id"] for r in c.execute(f"SELECT id FROM {S}.markets WHERE race_id = ANY(%s)",
                                              (races,)).fetchall()]
        users = [r["id"] for r in c.execute(f"SELECT id FROM {S}.users WHERE username LIKE %s",
                                            (f"test_{tag}%",)).fetchall()]
        c.execute(f"DELETE FROM {S}.trades WHERE market_id = ANY(%s)", (markets,))
        c.execute(f"DELETE FROM {S}.price_ticks WHERE market_id = ANY(%s)", (markets,))
        c.execute(f"DELETE FROM {S}.orders WHERE market_id = ANY(%s) OR user_id = ANY(%s)", (markets, users))
        c.execute(f"DELETE FROM {S}.positions WHERE market_id = ANY(%s) OR user_id = ANY(%s)", (markets, users))
        c.execute(f"DELETE FROM {S}.portfolio_snapshots WHERE user_id = ANY(%s)", (users,))
        c.execute(f"DELETE FROM {S}.ai_briefs WHERE race_id = ANY(%s) OR market_id = ANY(%s) OR text LIKE %s",
                  (races, markets, f"TEST {tag}%"))
        c.execute(f"DELETE FROM {S}.admin_actions WHERE race_id = ANY(%s) OR market_id = ANY(%s)", (races, markets))
        c.execute(f"DELETE FROM {S}.markets WHERE id = ANY(%s)", (markets,))
        c.execute(f"DELETE FROM {S}.races WHERE id = ANY(%s)", (races,))
        c.execute(f"DELETE FROM {S}.articles WHERE url LIKE %s", (f"https://test.invalid/{tag}/%",))
        c.execute(f"UPDATE {S}.settings SET updated_by = NULL WHERE updated_by = ANY(%s)", (users,))
        c.execute(f"DELETE FROM {S}.admin_actions WHERE admin_id = ANY(%s)", (users,))
        c.execute(f"DELETE FROM {S}.users WHERE id = ANY(%s)", (users,))


@pytest.fixture(scope="session")
def tag():
    db.ensure_schema()
    db.ensure_bot_user()
    t = uuid.uuid4().hex[:6]
    try:
        yield t
    finally:
        cleanup(t)


@pytest.fixture(scope="session")
def house(tag):
    return db.ensure_bot_user()


@pytest.fixture
def make_user(tag):
    def _make(suffix=None, **kw):
        return db.create_user(f"test_{tag}_{suffix or next(_user_counter)}", "!test", **kw)
    return _make


@pytest.fixture
def make_race(tag):
    """Factory: a race plus candidate markets, all created `created_ago` in the past."""
    def _make(position="President", candidates=(("Alice", 50), ("Bob", 30), (RON, 20)),
              created_ago=timedelta(hours=3), closes_in=timedelta(days=7), status="open",
              seats=1, closes_at=None, **race_fields):
        now = db.utcnow()
        created, closes = now - created_ago, closes_at or now + closes_in
        election = f"TEST {tag} {next(_race_counter)}"
        rid = db.create_race({"election": election, "position": position, "seats": seats,
                              "resolution_criteria": "test", "closes_at": closes, "status": status,
                              "created_at": created, **race_fields})
        markets = {}
        for name, prior in candidates:
            markets[name] = db.create_market({
                "race_id": rid, "candidate": name, "question": f"Will {name} be elected {position}?",
                "resolution_criteria": "test", "closes_at": closes, "status": status,
                "ai_prior": prior, "created_at": created})
        return {"id": rid, "election": election, "markets": markets, "created": created, "closes": closes}
    return _make


def refresh_candles(start, end=None):
    """Materialise candles_5m/1h over [start, end]. Back-dated trades land in windows the
    refresh policy has already materialised, so they only show up after this."""
    db.refresh_candles(start, end)


def trade(market_id, price, qty, t, buyer, seller, taker_side="buy"):
    db.insert_trade({"time": t, "market_id": market_id, "price": price, "qty": qty,
                     "buyer_id": buyer, "seller_id": seller, "taker_side": taker_side})
