"""Content endpoints (owner C, COLLAB 9 "Content & AI"): articles, Gemini briefs, digest.

Creating markets (Cuppers matches and generic ones) moved to admin_api.py.
"""
import logging
from datetime import timedelta

from flask import Blueprint, jsonify, request

import content
import db
from data_api import _public_race, login_required

log = logging.getLogger("content_api")
bp = Blueprint("content_api", __name__)

BRIEF_COOLDOWN = timedelta(minutes=10)   # default; live value is content.brief_cooldown_seconds


def _brief_cooldown():
    v = db.get_setting("content.brief_cooldown_seconds") if hasattr(db, "get_setting") else None
    return timedelta(seconds=v) if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else BRIEF_COOLDOWN


def _err(msg, code=400):
    return jsonify(error=msg), code


def _brief_json(b):
    return {"text": b["text"], "created_at": db.iso(b["created_at"])} if b else None


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
    cooldown = _brief_cooldown()
    if last and last["created_at"] > db.utcnow() - cooldown:
        return _err(f"This analysis was refreshed in the last {max(1, round(cooldown.total_seconds() / 60))} "
                    "minutes. Try again later.")
    return jsonify(brief=_brief_json(content.refresh_brief(race_id)))


@bp.get("/api/digest")
def digest():
    return jsonify(digest=_brief_json(db.latest_brief(None, "digest")))
