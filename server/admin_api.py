"""Admin portal endpoints: create / edit / approve markets of every kind, reference data,
settings, scraped articles and the audit log. Everything lives in the DB (oxmarket).

Market kinds (races.kind):
  match   Cuppers fixture "<Competition>: <Home> vs <Away>", outcomes Home / (Draw) / Away
  multi   any question with 2-12 named outcomes, `seats` of them win
  binary  one YES/NO question (a single market named "Yes")
Close / resolve stay in trading_api (A); every successful admin write anywhere under
/api/admin/ is recorded in the admin_actions hypertable by audit_admin_request().
"""
import logging
import re
from datetime import date

from flask import Blueprint, g, jsonify, request

import db
import trading
from data_api import market_json, race_json
from trading_api import admin_required

log = logging.getLogger("admin_api")
bp = Blueprint("admin_api", __name__)

MAX_OUTCOMES = 12
URL_RE = re.compile(r"^https?://\S+$")
DEFAULT_CRITERIA = {
    "match": "Resolves on the full-time result as confirmed by the competition organisers or "
             "Oxford University Sport. The winning outcome resolves YES, every other outcome NO.",
    "generic": "Resolves by admin decision based on reporting in Cherwell or The Oxford Student, "
               "or on official University or Union announcements.",
}
# expected JSON type of each setting (anything else is refused)
SETTING_TYPES = {
    "mm.interval": (int, 1, 3600), "mm.size": (int, 1, 10000), "mm.half_spread": (int, 1, 20),
    "mm.alpha": (float, 0.0, 1.0), "mm.prior_pull": (float, 0.0, 1.0),
    "jobs.tick_seconds": (int, 5, 3600), "jobs.autoclose_seconds": (int, 5, 3600),
    "jobs.portfolio_seconds": (int, 30, 86400), "content.brief_cooldown_seconds": (int, 0, 86400),
    "content.scrape_minutes": (int, 5, 1440), "users.starting_cash": (int, 0, 100_000_000),
    "defaults.criteria.match": (str, None, None), "defaults.criteria.generic": (str, None, None),
    "defaults.priors.draw": (list, 3, 3), "defaults.priors.no_draw": (list, 2, 2),
}


def _err(msg, code=400):
    return jsonify(error=msg), code


def _clean_str(v, limit=500):
    s = str(v or "").strip()
    return s[:limit] if s else None


# ---------------------------------------------------------------- validation

def load_ref():
    """Reference data used by validate_race (one dict so tests can pass their own)."""
    return {"terms": {t["name"] for t in db.list_ref("terms")},
            "competitions": {c["name"]: c for c in db.list_ref("competitions")},
            "categories": {c["slug"] for c in db.list_ref("categories")},
            "criteria": {"match": db.get_setting("defaults.criteria.match", DEFAULT_CRITERIA["match"]),
                         "generic": db.get_setting("defaults.criteria.generic", DEFAULT_CRITERIA["generic"])}}


def validate_race(body, ref=None, now=None):
    """Request body -> kwargs for trading.create_race. Raises ValueError with a message
    meant for the admin. ref = load_ref() (injectable for tests)."""
    ref = ref if ref is not None else load_ref()
    now = now or db.utcnow()
    kind = str(body.get("kind") or "match").strip()
    if kind not in db.RACE_KINDS:
        raise ValueError("kind must be match, multi or binary")
    election = str(body.get("election") or "").strip()
    if election not in ref["terms"]:
        raise ValueError(f'election must be a term from the Reference data tab (e.g. "{min(ref["terms"], default="Michaelmas 2026")}")')
    position = " ".join(str(body.get("position") or "").split())
    if not position:
        raise ValueError("position (the match or question) is required")
    if len(position) > 200:
        raise ValueError("position / question is too long (200 characters max)")
    status = body.get("status", "open")
    if status not in ("open", "proposed"):
        raise ValueError("status must be open (publish) or proposed (draft)")
    try:
        closes_at = trading._parse_time(body.get("closes_at"))
    except (TypeError, ValueError):
        raise ValueError("closes_at must be an ISO timestamp")
    if closes_at is None or closes_at.tzinfo is None:
        raise ValueError("closes_at must be an ISO timestamp with a timezone (e.g. ...Z)")
    if closes_at <= now:
        raise ValueError("the close time / kick-off must be in the future")

    seats = body.get("seats", 1)
    if not isinstance(seats, int) or isinstance(seats, bool) or not 1 <= seats <= 10:
        raise ValueError("seats must be 1..10")
    raw = body.get("candidates") or []
    if not isinstance(raw, list) or not all(isinstance(c, dict) for c in raw):
        raise ValueError("candidates must be a list of {name, prior}")

    if kind == "binary":
        prior = raw[0].get("prior", 50) if raw else body.get("prior", 50)
        if not isinstance(prior, int) or isinstance(prior, bool) or not 1 <= prior <= 99:
            raise ValueError("the prior must be a whole number 1..99")
        cands, seats = [{"name": trading.BINARY_NAME, "prior": prior}], 1
    else:
        names = [" ".join(str(c.get("name") or "").split()) for c in raw]
        if not 2 <= len(names) <= MAX_OUTCOMES:
            raise ValueError(f"need 2 to {MAX_OUTCOMES} outcomes")
        if any(not n for n in names) or len({n.lower() for n in names}) != len(names):
            raise ValueError("outcome names must be non-empty and unique")
        if seats >= len(names):
            raise ValueError("seats must be fewer than the number of outcomes")
        priors = []
        for c in raw:
            p = c.get("prior")
            if not isinstance(p, int) or isinstance(p, bool) or not 1 <= p <= 99:
                raise ValueError("priors must be whole numbers 1..99")
            priors.append(p)
        cands = [{"name": n, "prior": p} for n, p in zip(names, priors)]

    category = str(body.get("category") or ("cuppers" if kind == "match" else "news")).strip()
    if category not in ref["categories"]:
        raise ValueError(f"unknown category {category!r}")
    if kind == "match":
        match = trading.parse_match(position)
        if not match:
            raise ValueError('a match position must look like "<Competition>: <Home> vs <Away>"')
        comp = ref["competitions"].get(match[0])
        if comp is None:
            raise ValueError(f"unknown competition {match[0]!r}: add it in the Reference data tab first")
        if not comp["active"]:
            raise ValueError(f"{match[0]} is not active")
        teams = {match[1].lower(), match[2].lower()}
        if len(teams) != 2:
            raise ValueError("home and away must be different teams")
        if not teams <= {c["name"].lower() for c in cands}:
            raise ValueError("the outcomes must include the home and away teams")

    criteria = _clean_str(body.get("resolution_criteria"), 2000) or \
        ref["criteria"]["match" if kind == "match" else "generic"]
    source_url = _clean_str(body.get("source_url"))
    if source_url and not URL_RE.match(source_url):
        raise ValueError("source_url must be an http(s) link")
    article_id = body.get("article_id")
    if article_id is not None and (not isinstance(article_id, int) or isinstance(article_id, bool)):
        raise ValueError("article_id must be an integer")
    return {"election": election, "position": position, "closes_at": closes_at,
            "resolution_criteria": criteria, "candidates": cands, "seats": seats,
            "status": status, "kind": kind, "category": category,
            "description": _clean_str(body.get("description")),
            "source_name": _clean_str(body.get("source_name"), 100), "source_url": source_url,
            "article_id": article_id}


def _admin_race_json(race):
    out = race_json(race)
    out.update(created_by_ai=bool(race.get("created_by_ai")), ai_reason=race.get("ai_reason"),
               article_id=race.get("article_id"), description=race.get("description"))
    return out


# ---------------------------------------------------------------- races

@bp.get("/api/admin/races")
@admin_required
def list_races(user):
    status = request.args.get("status", "any")
    if status not in ("any", "all", "proposed", "open", "closed", "resolved", "rejected"):
        return _err("bad status")
    return jsonify(races=[_admin_race_json(r) for r in db.list_races(status)])


@bp.post("/api/admin/races")
@admin_required
def create_race(user):
    body = request.get_json(silent=True) or {}
    try:
        f = validate_race(body)
    except ValueError as e:
        return _err(str(e))
    if f["article_id"] is not None:
        art = db.get_article(f["article_id"])
        if art is None:
            return _err("article not found")
        f["source_name"] = f["source_name"] or art["source"]
        f["source_url"] = f["source_url"] or art["url"]
    extra = {k: f[k] for k in ("description", "source_name", "source_url", "article_id") if f[k] is not None}
    try:
        race_id = trading.create_race(f["election"], f["position"], f["closes_at"], f["resolution_criteria"],
                                      f["candidates"], seats=f["seats"], status=f["status"],
                                      kind=f["kind"], category=f["category"], **extra)
    except trading.TradingError as e:
        msg = str(e)
        return _err("That market already exists this term" if msg.startswith("Race already exists") else msg)
    if f["article_id"] is not None:
        db.mark_article_processed(f["article_id"])
    g.audit = {"race_id": race_id}
    log.info("race %s created by %s: [%s] %s", race_id, user["username"], f["kind"], f["position"])
    return jsonify(race=_admin_race_json(db.get_race(race_id))), 201


EDITABLE = {"description", "resolution_criteria", "closes_at", "source_name", "source_url", "category"}


@bp.patch("/api/admin/races/<int:race_id>")
@admin_required
def edit_race(user, race_id):
    race = db.get_race(race_id)
    if race is None:
        return _err("not found", 404)
    if race["status"] in ("resolved", "rejected"):
        return _err(f"race is {race['status']} and can no longer be edited")
    body = request.get_json(silent=True) or {}
    bad = set(body) - EDITABLE
    if bad:
        return _err(f"cannot edit {sorted(bad)}")
    fields = {}
    for k in ("description", "resolution_criteria", "source_name", "source_url"):
        if k in body:
            fields[k] = _clean_str(body[k], 2000)
    if "resolution_criteria" in fields and not fields["resolution_criteria"]:
        return _err("resolution rules can't be empty")
    if fields.get("source_url") and not URL_RE.match(fields["source_url"]):
        return _err("source_url must be an http(s) link")
    if "category" in body:
        if not db.get_ref("categories", body["category"]):
            return _err("unknown category")
        fields["category"] = body["category"]
    if "closes_at" in body:
        try:
            closes_at = trading._parse_time(body["closes_at"])
        except (TypeError, ValueError):
            return _err("closes_at must be an ISO timestamp")
        if closes_at is None or closes_at.tzinfo is None:
            return _err("closes_at needs a timezone")
        if race["status"] in ("open", "proposed") and closes_at <= db.utcnow():
            return _err("the new close time must be in the future (use Stop trading to close now)")
        fields["closes_at"] = closes_at
    if not fields:
        return _err("nothing to change")
    with db.transaction() as conn:
        db.update_race(race_id, conn=conn, **fields)
        # markets carry their own copies of these columns
        shared = {k: v for k, v in fields.items()
                  if k in ("description", "resolution_criteria", "source_name", "source_url", "category")}
        if shared:
            for m in db.race_markets(race_id, conn=conn):
                db.update_market(m["id"], conn=conn, **shared)
    return jsonify(race=_admin_race_json(db.get_race(race_id)))


@bp.post("/api/admin/races/<int:race_id>/approve")
@admin_required
def approve_race(user, race_id):
    race = db.get_race(race_id)
    if race is None:
        return _err("not found", 404)
    if race["status"] == "proposed" and race["closes_at"] <= db.utcnow():
        return _err("its close time has passed: edit the close time first")
    try:
        trading.approve_race(race_id)
    except trading.TradingError as e:
        return _err(str(e))
    return jsonify(race=_admin_race_json(db.get_race(race_id)))


@bp.post("/api/admin/races/<int:race_id>/reject")
@admin_required
def reject_race(user, race_id):
    try:
        trading.reject_race(race_id)
    except trading.TradingError as e:
        return _err(str(e), 404 if "not found" in str(e) else 400)
    return jsonify(race=_admin_race_json(db.get_race(race_id)))


@bp.post("/api/admin/races/<int:race_id>/candidates")
@admin_required
def add_outcome(user, race_id):
    race = db.get_race(race_id)
    if race is None:
        return _err("not found", 404)
    if race.get("kind") == "binary":
        return _err("a yes/no market has exactly one outcome")
    body = request.get_json(silent=True) or {}
    name = " ".join(str(body.get("name") or "").split())
    prior = body.get("prior")
    if prior is not None and (not isinstance(prior, int) or not 1 <= prior <= 99):
        return _err("prior must be a whole number 1..99")
    try:
        mid = trading.add_candidate(race_id, name, prior)
    except trading.TradingError as e:
        return _err(str(e))
    g.audit = {"race_id": race_id, "market_id": mid}
    return jsonify(market=market_json(db.get_market(mid))), 201


# ---------------------------------------------------------------- articles (scraped news)

def _article_json(a):
    return {"id": a["id"], "source": a["source"], "url": a["url"], "title": a["title"],
            "summary": a.get("summary"), "published_at": db.iso(a.get("published_at")),
            "processed": bool(a.get("processed")), "race_ids": a.get("race_ids") or []}


@bp.get("/api/admin/articles")
@admin_required
def list_articles(user):
    pending = request.args.get("pending") == "1"
    limit = max(1, min(200, request.args.get("limit", 60, type=int)))
    return jsonify(articles=[_article_json(a) for a in db.admin_articles(limit=limit, pending_only=pending)])


@bp.post("/api/admin/articles/<int:article_id>/dismiss")
@admin_required
def dismiss_article(user, article_id):
    if db.get_article(article_id) is None:
        return _err("not found", 404)
    db.mark_article_processed(article_id)
    return jsonify(ok=True)


@bp.post("/api/admin/scrape")
@admin_required
def scrape(user):
    import scraper
    new = scraper.scrape_all()
    g.audit = {"details": {"new_articles": new}}
    return jsonify(new_articles=new)


# ---------------------------------------------------------------- settings

def _check_setting(key, value):
    if key not in SETTING_TYPES:
        raise ValueError(f"unknown setting {key}")
    typ, lo, hi = SETTING_TYPES[key]
    if typ is int:
        if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
            raise ValueError(f"{key} must be a whole number {lo}..{hi}")
    elif typ is float:
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not lo <= value <= hi:
            raise ValueError(f"{key} must be a number {lo}..{hi}")
    elif typ is str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be non-empty text")
        value = value.strip()
    elif typ is list:
        if (not isinstance(value, list) or len(value) != lo
                or not all(isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 99 for v in value)):
            raise ValueError(f"{key} must be {lo} whole numbers 1..99")
    return value


def _setting_json(r):
    return {"key": r["key"], "value": r["value"], "description": r.get("description"),
            "updated_at": db.iso(r.get("updated_at")), "updated_by": r.get("updated_by")}


@bp.get("/api/admin/settings")
@admin_required
def get_settings(user):
    return jsonify(settings=[_setting_json(r) for r in db.all_settings()])


@bp.put("/api/admin/settings")
@admin_required
def put_settings(user):
    """Body {key: value, ...}: all checked first, then all saved."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not body:
        return _err("send {key: value}")
    try:
        clean = {k: _check_setting(k, v) for k, v in body.items()}
    except ValueError as e:
        return _err(str(e))
    with db.transaction() as conn:
        for k, v in clean.items():
            db.set_setting(k, v, user["id"], conn=conn)
    return jsonify(settings=[_setting_json(r) for r in db.all_settings()])


# ---------------------------------------------------------------- reference data

def _ref_json(row):
    return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in row.items()}


def _check_ref(table, body, partial):
    """Validate a reference-data row. Returns the cleaned fields."""
    spec = db.REF_TABLES[table]
    out = {}
    for k, v in body.items():
        if k not in spec["cols"]:
            raise ValueError(f"unknown field {k}")
        if k in ("active", "has_draw"):
            if not isinstance(v, bool):
                raise ValueError(f"{k} must be true or false")
        elif k == "sort":
            if not isinstance(v, int) or isinstance(v, bool):
                raise ValueError("sort must be a whole number")
        elif k in ("starts_on", "ends_on"):
            try:
                v = date.fromisoformat(str(v))
            except ValueError:
                raise ValueError(f"{k} must be a date YYYY-MM-DD")
        elif k == "pattern":
            try:
                re.compile(str(v))
            except re.error:
                raise ValueError("pattern must be a valid regular expression")
            v = str(v).strip()
        elif k == "kind" and table == "teams":
            if v not in ("college", "other"):
                raise ValueError("kind must be college or other")
        elif k == "sport" and v in ("", None):
            v = None
        else:
            v = " ".join(str(v or "").split())
            if not v:
                raise ValueError(f"{k} can't be empty")
        out[k] = v
    if not partial:
        required = {"categories": {"slug", "name"}, "sports": {"name", "icon", "pattern"},
                    "competitions": {"name"}, "teams": {"name"}, "terms": {"name", "starts_on", "ends_on"}}[table]
        missing = required - set(out)
        if missing:
            raise ValueError(f"missing {sorted(missing)}")
    if table == "categories" and "slug" in out and not re.match(r"^[a-z][a-z0-9_-]{1,30}$", out["slug"]):
        raise ValueError("slug must be lowercase letters, digits, - or _")
    if table == "terms" and "starts_on" in out and "ends_on" in out and out["ends_on"] < out["starts_on"]:
        raise ValueError("ends_on is before starts_on")
    return out


@bp.get("/api/admin/ref/<table>")
@admin_required
def list_ref(user, table):
    if table not in db.REF_TABLES:
        return _err("not found", 404)
    return jsonify(rows=[_ref_json(r) for r in db.list_ref(table)])


@bp.post("/api/admin/ref/<table>")
@admin_required
def add_ref(user, table):
    if table not in db.REF_TABLES:
        return _err("not found", 404)
    try:
        fields = _check_ref(table, request.get_json(silent=True) or {}, partial=False)
        row = db.insert_ref(table, fields)
    except ValueError as e:
        return _err(str(e))
    except Exception as e:   # unique / FK violations
        log.info("ref insert into %s refused: %s", table, e)
        return _err("that would duplicate an existing entry or reference a missing one")
    g.audit = {"details": {"table": table, "row": _ref_json(row)}}
    return jsonify(row=_ref_json(row)), 201


@bp.patch("/api/admin/ref/<table>/<key>")
@admin_required
def edit_ref(user, table, key):
    if table not in db.REF_TABLES:
        return _err("not found", 404)
    k = int(key) if db.REF_TABLES[table]["key"] == "id" and key.isdigit() else key
    if db.get_ref(table, k) is None:
        return _err("not found", 404)
    try:
        fields = _check_ref(table, request.get_json(silent=True) or {}, partial=True)
        row = db.update_ref(table, k, fields)
    except ValueError as e:
        return _err(str(e))
    except Exception as e:
        log.info("ref update of %s/%s refused: %s", table, key, e)
        return _err("that would duplicate an existing entry or reference a missing one")
    g.audit = {"details": {"table": table, "key": key, "changes": _ref_json(fields)}}
    return jsonify(row=_ref_json(row))


# ---------------------------------------------------------------- audit log

def _action_json(a):
    return {"time": db.iso(a["time"]), "admin": a.get("admin"), "action": a["action"],
            "race_id": a.get("race_id"), "market_id": a.get("market_id"),
            "race_position": a.get("race_position"), "details": a.get("details")}


@bp.get("/api/admin/actions")
@admin_required
def actions(user):
    limit = max(1, min(500, request.args.get("limit", 100, type=int)))
    race_id = request.args.get("race_id", type=int)
    return jsonify(actions=[_action_json(a) for a in db.recent_admin_actions(limit, race_id)])


_ID_IN_PATH = re.compile(r"/api/admin/(races|markets)/(\d+)")


def audit_admin_request(response):
    """after_request hook (app-wide): record every successful admin write in admin_actions.
    action = "<METHOD> <route rule>", e.g. "POST /api/admin/races/<int:race_id>/resolve"."""
    try:
        if (request.method in ("GET", "HEAD", "OPTIONS") or not request.path.startswith("/api/admin/")
                or response.status_code >= 400):
            return response
        from data_api import current_user
        user = current_user()
        info = dict(getattr(g, "audit", None) or {})
        m = _ID_IN_PATH.match(request.path)
        if m:
            info.setdefault("race_id" if m.group(1) == "races" else "market_id", int(m.group(2)))
        details = info.pop("details", None)
        if details is None:
            body = request.get_json(silent=True)
            details = body if isinstance(body, (dict, list)) else None
        rule = request.url_rule.rule if request.url_rule else request.path
        db.log_admin_action(user["id"] if user else None, f"{request.method} {rule}",
                            race_id=info.get("race_id"), market_id=info.get("market_id"), details=details)
    except Exception:
        log.exception("audit log write failed")
    return response


# ---------------------------------------------------------------- public reference data

@bp.get("/api/meta")
def meta():
    """Reference data for every page: categories, sports (icon + regex), active competitions,
    teams, the current term and every term. Cached by the browser for a minute."""
    term = db.current_term()
    resp = jsonify(
        categories=[_ref_json(r) for r in db.list_ref("categories")],
        sports=[_ref_json(r) for r in db.list_ref("sports")],
        competitions=[_ref_json(r) for r in db.list_ref("competitions", active_only=True)],
        teams=[r["name"] for r in db.list_ref("teams", active_only=True)],
        terms=[r["name"] for r in db.list_ref("terms")],
        current_term=term["name"] if term else None,
        defaults={"priors_draw": db.get_setting("defaults.priors.draw", [40, 25, 35]),
                  "priors_no_draw": db.get_setting("defaults.priors.no_draw", [50, 50]),
                  "criteria_match": db.get_setting("defaults.criteria.match", DEFAULT_CRITERIA["match"]),
                  "criteria_generic": db.get_setting("defaults.criteria.generic", DEFAULT_CRITERIA["generic"])})
    resp.headers["Cache-Control"] = "max-age=60"
    return resp


def init_app(app):
    app.register_blueprint(bp)
    app.after_request(audit_admin_request)
