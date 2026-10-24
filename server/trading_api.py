"""Trading HTTP endpoints (COLLAB 9 "Trading [A]")."""
from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, jsonify, request, session

import db
import trading

bp = Blueprint("trading_api", __name__)


def _iso(v):
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return v


def _err(msg, code=400):
    return jsonify({"error": msg}), code


def _current_user():
    uid = session.get("user_id")
    return db.get_user(uid) if uid is not None else None


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        user = _current_user()
        if user is None:
            return _err("Not logged in", 401)
        return f(user, *a, **kw)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        user = _current_user()
        if user is None:
            return _err("Not logged in", 401)
        if not user.get("is_admin"):
            return _err("Admin only", 403)
        return f(user, *a, **kw)
    return wrapper


def user_json(user, conn=None):
    return {"id": user["id"], "username": user["username"], "cash": user["cash"],
            "available": trading.available_cash(user["id"], conn=conn), "is_admin": bool(user.get("is_admin"))}


def order_json(o):
    return {"id": o["id"], "market_id": o["market_id"], "side": o["side"], "price": o["price"],
            "qty": o["qty"], "qty_remaining": o["qty_remaining"], "status": o["status"],
            "created_at": _iso(o.get("created_at"))}


def _market_json(m):
    out = {k: _iso(v) for k, v in m.items()}
    if "price" not in out:
        out["price"] = db.current_price(m["id"])
    return out


def race_json(race):
    """COLLAB 9 Race. Uses B's serializer (data_api.race_json: adds change_24h, volume_24h,
    sparkline) when it's available and bound to the same db; own minimal version otherwise."""
    try:
        import data_api
        if getattr(data_api, "db", None) is db:
            return data_api.race_json(race)
    except ImportError:
        pass
    markets = sorted((_market_json(m) for m in db.race_markets(race["id"])),
                     key=lambda m: m.get("price") or 0, reverse=True)
    out = {k: _iso(v) for k, v in race.items()}
    out["candidates"] = markets
    out["winners"] = [m["id"] for m in markets if m.get("outcome") == "YES"]
    out["overround"] = sum(m.get("price") or 0 for m in markets)
    return out


def _int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# ----------------------------------------------------------------- book
@bp.get("/api/markets/<int:market_id>/book")
def book(market_id):
    if db.get_market(market_id) is None:
        return _err("Market not found", 404)
    depth = max(1, min(50, _int(request.args.get("depth"), 10)))
    return jsonify(trading.get_book(market_id, depth))


# --------------------------------------------------------------- orders
@bp.post("/api/orders")
@login_required
def place_order(user):
    body = request.get_json(silent=True) or {}
    try:
        res = trading.place_order(user["id"], _int(body.get("market_id"), -1), body.get("outcome"),
                                  body.get("side"), body.get("type", "limit"), body.get("price"),
                                  body.get("qty"))
    except trading.TradingError as e:
        return _err(str(e))
    order = order_json(res["order"]) if res["order"] else None
    with db.transaction() as conn:
        fresh = user_json(db.get_user(user["id"], conn=conn), conn)
    return jsonify({"order": order, "fills": res["fills"], "user": fresh}), 201


@bp.delete("/api/orders/<int:order_id>")
@login_required
def cancel_order(user, order_id):
    try:
        trading.cancel_order(user["id"], order_id, is_admin=bool(user.get("is_admin")))
    except trading.TradingError as e:
        msg = str(e)
        return _err(msg, 404 if msg == "Order not found" else 400)
    return jsonify({"ok": True})


@bp.get("/api/orders")
@login_required
def list_orders(user):
    status = request.args.get("status", "open")
    if status == "all" and hasattr(db, "user_orders"):
        orders = db.user_orders(user["id"])
    else:
        orders = db.open_orders(user_id=user["id"])
    return jsonify({"orders": [order_json(o) for o in orders]})


@bp.get("/api/portfolio")
@login_required
def portfolio(user):
    positions, races = [], {}
    with db.transaction() as conn:   # one connection for the whole page
        for p in db.user_positions(user["id"], conn=conn):
            qty = int(p["qty"])
            if qty == 0:
                continue
            # B's user_positions already joins the market fields + price; fall back if absent
            m = p if "question" in p else (db.get_market(p["market_id"], conn=conn) or {})
            price = p["price"] if p.get("price") is not None else db.current_price(p["market_id"], conn=conn)
            rid = m.get("race_id")
            if rid and rid not in races:
                races[rid] = db.get_race(rid, conn=conn)
            race = races.get(rid)
            positions.append({
                "market_id": p["market_id"], "question": m.get("question"), "status": m.get("status"),
                "race_id": rid, "candidate": m.get("candidate"),
                "position": race["position"] if race else None,
                "qty": qty, "label": "YES" if qty > 0 else "NO", "shares": abs(qty),
                "price": price, "value": qty * price, "net_cost": int(p.get("net_cost") or 0)})
        body = {"user": user_json(user, conn), "equity": db.equity(user["id"], conn=conn), "positions": positions}
    return jsonify(body)


# ---------------------------------------------------------------- admin
@bp.post("/api/admin/races/<int:race_id>/close")
@admin_required
def admin_close_race(user, race_id):
    try:
        trading.close_race(race_id)
    except trading.TradingError as e:
        return _err(str(e), 404 if "not found" in str(e) else 400)
    return jsonify({"race": race_json(db.get_race(race_id))})


@bp.post("/api/admin/races/<int:race_id>/resolve")
@admin_required
def admin_resolve_race(user, race_id):
    body = request.get_json(silent=True) or {}
    try:
        trading.resolve_race(race_id, body.get("winner_market_ids") or [])
    except trading.TradingError as e:
        return _err(str(e), 404 if "not found" in str(e) else 400)
    return jsonify({"race": race_json(db.get_race(race_id))})


@bp.post("/api/admin/markets/<int:market_id>/close")
@admin_required
def admin_close_market(user, market_id):
    try:
        trading.close_market(market_id)
    except trading.TradingError as e:
        return _err(str(e), 404 if "not found" in str(e) else 400)
    return jsonify({"market": _market_json(db.get_market(market_id))})


@bp.post("/api/admin/markets/<int:market_id>/resolve")
@admin_required
def admin_resolve_market(user, market_id):
    body = request.get_json(silent=True) or {}
    try:
        trading.resolve_market(market_id, body.get("outcome"))
    except trading.TradingError as e:
        return _err(str(e), 404 if "not found" in str(e) else 400)
    return jsonify({"market": _market_json(db.get_market(market_id))})


@bp.get("/api/admin/invariants")
@admin_required
def admin_invariants(user):
    problems = trading.check_invariants()
    return jsonify({"ok": not problems, "problems": problems})


@bp.post("/api/admin/mm/requote")
@admin_required
def admin_mm_requote(user):
    import mm_bot
    return jsonify({"requoted": mm_bot.requote_all()})
