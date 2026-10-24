"""Content & admin-match endpoints (owner C, COLLAB 9 "Content & AI" + COLLAB 14).

Cuppers matches are posted by hand: a match is a race whose candidates are the possible
results ("<Home>", "Draw", "<Away>"). Results use A's close/resolve endpoints.
"""
import logging
import re
from datetime import timedelta

from flask import Blueprint, jsonify, request

import content
import db
import trading
from data_api import _public_race, admin_required, login_required, race_json, market_json

log = logging.getLogger("content_api")
bp = Blueprint("content_api", __name__)

TERM_RE = re.compile(r"^(Michaelmas|Hilary|Trinity) \d{4}$")
BRIEF_COOLDOWN = timedelta(minutes=10)


def _err(msg, code=400):
    return jsonify(error=msg), code


def _brief_json(b):
    return {"text": b["text"], "created_at": db.iso(b["created_at"])} if b else None


def _question(name, position, election):
    comp, _, title = position.partition(": ")
    home, sep, away = title.partition(" vs ")
    if not sep:
        return f"Will {name} win {position} ({election})?"
    if name.lower() == "draw":
        return f"Will {title} end in a draw ({comp}, {election})?"
    other = away if name == home else home
    return f"Will {name} beat {other} ({comp}, {election})?"


# ---------------------------------------------------------------- public
@bp.get("/api/articles")
def articles():
    limit = max(1, min(50, request.args.get("limit", 20, type=int)))
    return jsonify(articles=[{"id": a["id"], "title": a["title"], "url": a["url"], "source": a["source"],
                              "published_at": db.iso(a.get("published_at")), "summary": a.get("summary")}
                             for a in db.recent_articles(limit)])


@bp.get("/api/races/<int:race_id>/brief")
def race_brief(race_id):
    if not _public_race(race_id):
        return _err("not found", 404)
    return jsonify(brief=_brief_json(db.latest_brief(race_id, "brief")))


@bp.post("/api/races/<int:race_id>/brief/refresh")
@login_required
def race_brief_refresh(race_id):
    if not _public_race(race_id):
        return _err("not found", 404)
    last = db.latest_brief(race_id, "brief")
    if last and last["created_at"] > db.utcnow() - BRIEF_COOLDOWN:
        return _err("This analysis was refreshed in the last 10 minutes. Try again later.")
    return jsonify(brief=_brief_json(content.refresh_brief(race_id)))


@bp.get("/api/digest")
def digest():
    return jsonify(digest=_brief_json(db.latest_brief(None, "digest")))


# ---------------------------------------------------------------- admin
def _validate(body):
    """Returns (fields, candidates) or raises ValueError."""
    election = str(body.get("election") or "").strip()
    position = str(body.get("position") or "").strip()
    if not TERM_RE.match(election):
        raise ValueError('election must look like "Michaelmas 2026"')
    if not position:
        raise ValueError("position (the match) is required")
    seats = body.get("seats", 1)
    if not isinstance(seats, int) or not 1 <= seats <= 10:
        raise ValueError("seats must be 1..10")
    try:
        closes_at = trading._parse_time(body.get("closes_at"))
    except (TypeError, ValueError):
        raise ValueError("closes_at must be an ISO timestamp")
    if closes_at is None or closes_at.tzinfo is None:
        raise ValueError("closes_at must be an ISO timestamp with a timezone (e.g. ...Z)")
    if closes_at <= db.utcnow():
        raise ValueError("kick-off must be in the future")
    cands = body.get("candidates") or []
    names = [str(c.get("name") or "").strip() for c in cands if isinstance(c, dict)]
    if not 2 <= len(names) <= 12 or len(names) != len(cands):
        raise ValueError("need 2 to 12 outcomes")
    if any(not n for n in names) or len({n.lower() for n in names}) != len(names):
        raise ValueError("outcome names must be non-empty and unique")
    priors = []
    for c in cands:
        p = c.get("prior")
        if not isinstance(p, int) or not 1 <= p <= 99:
            raise ValueError("priors must be whole numbers 1..99")
        priors.append(p)
    criteria = str(body.get("resolution_criteria") or "").strip() or \
        "Resolves on the full-time result as confirmed by the competition organisers / Oxford University Sport."
    fields = {"election": election, "position": position, "seats": seats, "closes_at": closes_at,
              "resolution_criteria": criteria, "status": "open",
              "description": str(body.get("description") or "").strip() or None,
              "source_name": body.get("source_name"), "source_url": body.get("source_url")}
    return fields, list(zip(names, priors))


@bp.post("/api/admin/races")
@admin_required
def create_match():
    try:
        fields, cands = _validate(request.get_json(silent=True) or {})
    except ValueError as e:
        return _err(str(e))
    if db.find_race(fields["election"], fields["position"]):
        return _err("That match already exists this term")
    race_id = db.create_race(fields)
    try:
        for name, prior in cands:
            trading.add_candidate(race_id, name, prior, category="cuppers",
                                  question=_question(name, fields["position"], fields["election"]))
    except trading.TradingError as e:
        log.exception("creating outcomes for race %s failed", race_id)
        db.update_race(race_id, status="rejected")
        return _err(str(e))
    log.info("match %s posted: %s (%s)", race_id, fields["position"], ", ".join(n for n, _ in cands))
    return jsonify(race=race_json(db.get_race(race_id))), 201


@bp.post("/api/admin/races/<int:race_id>/candidates")
@admin_required
def add_outcome(race_id):
    race = db.get_race(race_id)
    if race is None:
        return _err("not found", 404)
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()
    prior = body.get("prior")
    if prior is not None and (not isinstance(prior, int) or not 1 <= prior <= 99):
        return _err("prior must be a whole number 1..99")
    try:
        mid = trading.add_candidate(race_id, name, prior, category="cuppers",
                                    question=_question(name, race["position"], race["election"]))
    except trading.TradingError as e:
        return _err(str(e))
    return jsonify(market=market_json(db.get_market(mid))), 201
