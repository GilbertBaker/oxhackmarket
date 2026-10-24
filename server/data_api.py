"""Auth, races, markets and TIME endpoints (owner B, COLLAB 9).

Other streams may import login_required, admin_required, current_user, and the
serializers race_json / market_json to build COLLAB 9 shaped objects.
"""
import logging
import os
import re
from datetime import timedelta
from functools import wraps

from flask import Blueprint, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

import db
import trading

log = logging.getLogger("data_api")
bp = Blueprint("data_api", __name__)

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
PUBLIC_STATUSES = ("open", "closed", "resolved")


def _err(msg, code=400):
    return jsonify(error=msg), code


# ---------------------------------------------------------------- auth helpers

def current_user():
    uid = session.get("user_id")
    return db.get_user(uid) if uid else None


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user():
            return _err("login required", 401)
        return fn(*a, **kw)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        user = current_user()
        if not user:
            return _err("login required", 401)
        if not user["is_admin"]:
            return _err("admin only", 403)
        return fn(*a, **kw)
    return wrapper


def user_json(user):
    try:
        available = trading.available_cash(user["id"])
    except Exception:
        log.exception("available_cash failed; falling back to cash")
        available = user["cash"]
    return {"id": user["id"], "username": user["username"], "cash": int(user["cash"]),
            "available": int(available), "is_admin": bool(user["is_admin"])}


def _admin_usernames():
    return {u.strip().lower() for u in os.environ.get("ADMIN_USERNAMES", "").split(",") if u.strip()}


# ---------------------------------------------------------------- serializers

def _sparklines(market_ids):
    since = db.utcnow() - timedelta(hours=24)
    series = db.close_series(market_ids, "1h", since)
    return {mid: [v for _, v in pts if v is not None] for mid, pts in series.items()}


def market_json(m, stats=None, sparkline=None):
    """Market row (with "price") -> COLLAB 9 Market. stats from db.market_stats."""
    price = m.get("price")
    if price is None:
        price = db.current_price(m["id"])
    out = {
        "id": m["id"], "race_id": m["race_id"], "candidate": m["candidate"],
        "question": m["question"], "description": m.get("description"),
        "resolution_criteria": m["resolution_criteria"], "category": m.get("category"),
        "source_name": m.get("source_name"), "source_url": m.get("source_url"),
        "closes_at": db.iso(m["closes_at"]), "status": m["status"], "outcome": m.get("outcome"),
        "price": int(price), "change_24h": 0, "volume_24h": 0, "ai_prior": m.get("ai_prior"),
        "created_at": db.iso(m.get("created_at")),
    }
    if stats:
        ago = stats.get("price_24h_ago")
        out["change_24h"] = int(price) - int(ago) if ago is not None else 0
        out["volume_24h"] = int(stats.get("volume_24h") or 0)
    if sparkline is not None:
        out["sparkline"] = sparkline
    return out


def race_json(race, markets=None, with_candidates=True):
    """Race row -> COLLAB 9 Race. markets = its markets with "price" (fetched if None)."""
    out = {
        "id": race["id"], "election": race["election"], "position": race["position"],
        "seats": race["seats"], "description": race.get("description"),
        "closes_at": db.iso(race["closes_at"]), "status": race["status"],
        "source_name": race.get("source_name"), "source_url": race.get("source_url"),
        "resolution_criteria": race["resolution_criteria"],
        "created_at": db.iso(race.get("created_at")),
        "resolved_at": db.iso(race.get("resolved_at")),
    }
    if markets is None:
        markets = db.race_markets(race["id"])
    out["winners"] = [m["id"] for m in markets if m.get("outcome") == "YES"]
    out["overround"] = sum(int(m["price"]) for m in markets)
    if with_candidates:
        stats = db.market_stats([m["id"] for m in markets])
        cands = [market_json(m, stats.get(m["id"])) for m in markets]
        cands.sort(key=lambda c: (-c["price"], c["candidate"]))
        out["candidates"] = cands
    return out


def _public_race(race_id):
    race = db.get_race(race_id)
    if not race or race["status"] not in PUBLIC_STATUSES:
        return None
    return race


def _since_arg(default):
    raw = request.args.get("since")
    if not raw:
        return default
    return db.parse_iso(raw)  # ValueError -> 400 via handler


def _bucket_arg(default="5m"):
    bucket = request.args.get("bucket", default)
    if bucket not in ("1m", "5m", "1h"):
        raise ValueError("bucket must be 1m, 5m or 1h")
    return bucket


@bp.errorhandler(ValueError)
def _bad_value(e):
    return _err(str(e) or "bad input", 400)


# ---------------------------------------------------------------- auth

@bp.post("/api/register")
def register():
    body = request.get_json(silent=True) or {}
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    if not USERNAME_RE.match(username):
        return _err("username must be 3-20 characters: letters, digits, underscore")
    if len(password) < 6:
        return _err("password must be at least 6 characters")
    if db.get_user_by_username(username):
        return _err("username already taken")
    try:
        user = db.create_user(username, generate_password_hash(password),
                              is_admin=username.lower() in _admin_usernames())
    except Exception:
        log.exception("create_user failed")
        return _err("username already taken")
    session.clear()
    session["user_id"] = user["id"]
    return jsonify(user=user_json(user)), 201


@bp.post("/api/login")
def login():
    body = request.get_json(silent=True) or {}
    user = db.get_user_by_username(str(body.get("username", "")).strip())
    if not user or user["is_bot"] or not check_password_hash(user["pw_hash"], str(body.get("password", ""))):
        return _err("wrong username or password", 401)
    session.clear()
    session["user_id"] = user["id"]
    return jsonify(user=user_json(user))


@bp.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@bp.get("/api/me")
def me():
    user = current_user()
    if not user:
        return _err("not logged in", 401)
    return jsonify(user=user_json(user))


# ---------------------------------------------------------------- races

@bp.get("/api/races")
def races():
    status = request.args.get("status", "open")
    if status == "all":
        status = list(PUBLIC_STATUSES)
    elif status not in PUBLIC_STATUSES:
        return _err("status must be open, closed, resolved or all")
    return jsonify(races=[race_json(r) for r in db.list_races(status)])


@bp.get("/api/races/<int:race_id>")
def race_detail(race_id):
    race = _public_race(race_id)
    if not race:
        return _err("race not found", 404)
    return jsonify(race=race_json(race))


@bp.get("/api/races/<int:race_id>/history")
def race_history(race_id):
    race = _public_race(race_id)
    if not race:
        return _err("race not found", 404)
    bucket = _bucket_arg()
    since = _since_arg(race["created_at"])
    return jsonify(bucket=bucket, series=db.race_history(race_id, bucket, since))


# ---------------------------------------------------------------- markets

@bp.get("/api/markets")
def markets():
    status = request.args.get("status", "open")
    if status not in PUBLIC_STATUSES + ("all",):
        return _err("status must be open, closed, resolved or all")
    race_id = request.args.get("race_id", type=int)
    rows = db.list_markets(status=status, race_id=race_id)
    rows = [m for m in rows if m["status"] in PUBLIC_STATUSES]
    ids = [m["id"] for m in rows]
    stats = db.market_stats(ids)
    sparks = _sparklines(ids) if ids else {}
    return jsonify(markets=[market_json(m, stats.get(m["id"]), sparks.get(m["id"], [])) for m in rows])


def _public_market(market_id):
    m = db.get_market(market_id)
    if not m or m["status"] not in PUBLIC_STATUSES:
        return None
    return m


@bp.get("/api/markets/<int:market_id>")
def market_detail(market_id):
    m = _public_market(market_id)
    if not m:
        return _err("market not found", 404)
    stats = db.market_stats([market_id]).get(market_id)
    race = db.get_race(m["race_id"])
    return jsonify(market=market_json(m, stats),
                   race=race_json(race, with_candidates=False) if race else None)


@bp.get("/api/markets/<int:market_id>/trades")
def market_trades(market_id):
    if not _public_market(market_id):
        return _err("market not found", 404)
    limit = max(1, min(request.args.get("limit", 50, type=int), 500))
    return jsonify(trades=[{"time": db.iso(t["time"]), "price": t["price"], "qty": t["qty"],
                            "taker_side": t["taker_side"]}
                           for t in db.recent_trades(market_id, limit)])


@bp.get("/api/markets/<int:market_id>/history")
def market_history(market_id):
    m = _public_market(market_id)
    if not m:
        return _err("market not found", 404)
    bucket = _bucket_arg()
    since = _since_arg(m["created_at"])
    return jsonify(bucket=bucket, points=db.price_history(market_id, bucket, since))


# ---------------------------------------------------------------- time machine

@bp.get("/api/timemachine/range")
def timemachine_range():
    r = db.timemachine_range()
    return jsonify(start=db.iso(r["start"]), end=db.iso(r["end"]))


@bp.get("/api/timemachine")
def timemachine():
    raw = request.args.get("t")
    t = db.parse_iso(raw) if raw else db.utcnow()
    articles = [{"title": a["title"], "url": a["url"], "source": a["source"],
                 "published_at": db.iso(a["published_at"] or a["scraped_at"])}
                for a in db.recent_articles(limit=10, before=t)]
    return jsonify(t=db.iso(t), races=db.timemachine(t), articles=articles)


# ---------------------------------------------------------------- leaderboard

@bp.get("/api/leaderboard")
def leaderboard():
    return jsonify(leaders=[{"rank": r["rank"], "username": r["username"], "equity": r["equity"]}
                            for r in db.leaderboard(20)])


@bp.get("/api/leaderboard/history")
def leaderboard_history():
    bucket = request.args.get("bucket", "1h")
    if bucket not in ("5m", "1h", "1d"):
        return _err("bucket must be 5m, 1h or 1d")
    top = max(1, min(request.args.get("top", 5, type=int), 20))
    leaders = db.leaderboard(top)
    hist = db.equity_history([r["user_id"] for r in leaders], bucket)
    return jsonify(series=[{"username": r["username"], "points": hist.get(r["user_id"], [])}
                           for r in leaders])


# ---------------------------------------------------------------- under the hood

@bp.get("/api/stats")
def stats():
    s = db.timescale_stats()
    comp = s["compression"] or {}
    before = comp.get("before_compression_total_bytes")
    after = comp.get("after_compression_total_bytes")
    return jsonify(
        hypertables=[{"name": h["hypertable_name"], "chunks": h["num_chunks"],
                      "compression": bool(h["compression_enabled"])} for h in s["hypertables"]],
        compression={"chunks_compressed": comp.get("number_compressed_chunks"),
                     "chunks_total": comp.get("total_chunks"),
                     "before_bytes": before, "after_bytes": after,
                     "ratio": round(before / after, 2) if before and after else None},
        continuous_aggregates=[{"name": c["view_name"], "realtime": not c["materialized_only"]}
                               for c in s["continuous_aggregates"]],
        jobs=[{"id": j["job_id"], "proc": j["proc_name"], "table": j["hypertable_name"],
               "every_s": int(j["schedule_interval"].total_seconds()) if j["schedule_interval"] else None,
               "last_status": j["last_run_status"],
               "last_success": db.iso(j["last_successful_finish"]),
               "next_start": db.iso(j["next_start"])} for j in s["jobs"]],
        rows=s["rows"])
