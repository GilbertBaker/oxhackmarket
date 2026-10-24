"""OxMarket data layer (owner B). ALL SQL access goes through here (COLLAB 7.2).

Every function takes an optional conn. If given, it runs on that connection (and so
inside the caller's transaction). Otherwise it borrows one from the pool and commits.
Rows come back as dicts with datetime values. iso() turns them into API strings.
"""
import logging
import os
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

log = logging.getLogger("db")

SCHEMA = os.environ.get("DB_SCHEMA", "oxmarket")
SCHEMA_FILE = Path(__file__).resolve().parent / "schema.sql"

BUCKETS = {"1m": timedelta(minutes=1), "5m": timedelta(minutes=5),
           "1h": timedelta(hours=1), "1d": timedelta(days=1)}
MAX_BUCKETS = 3000
POSITION_ORDER = ["President", "Librarian", "Treasurer", "Secretary"]

_pool = None
_pool_lock = threading.Lock()


# ---------------------------------------------------------------- connections

def _conninfo():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set (copy .env.example to .env)")
    return url


def pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ConnectionPool(
                    _conninfo(), min_size=1, max_size=10, open=True,
                    check=ConnectionPool.check_connection, max_idle=300,
                    kwargs={"row_factory": dict_row,
                            "options": f"-c search_path={SCHEMA},public -c timezone=UTC"})
    return _pool


@contextmanager
def transaction():
    """Yield a pooled connection inside a transaction (commit on success, else rollback)."""
    with pool().connection() as conn:
        with conn.transaction():
            yield conn


@contextmanager
def _use(conn=None):
    if conn is not None:
        yield conn
    else:
        with pool().connection() as c:
            yield c


def _one(query, params=None, conn=None):
    with _use(conn) as c:
        return c.execute(query, params).fetchone()


def _all(query, params=None, conn=None):
    with _use(conn) as c:
        return c.execute(query, params).fetchall()


def _exec(query, params=None, conn=None):
    with _use(conn) as c:
        c.execute(query, params)


# ---------------------------------------------------------------- helpers

def iso(dt):
    """datetime -> '2026-10-24T14:05:00Z' (UTC). None stays None."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    """ISO 8601 string -> aware UTC datetime. Raises ValueError on bad input."""
    if isinstance(s, datetime):
        dt = s
    else:
        dt = datetime.fromisoformat(str(s).strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def utcnow():
    return datetime.now(timezone.utc)


def position_rank(position):
    """President, Librarian, Treasurer, Secretary, then everything else alphabetically."""
    try:
        return (POSITION_ORDER.index(position), "")
    except ValueError:
        return (len(POSITION_ORDER), (position or "").lower())


def _position_order_sql(col="position"):
    cases = " ".join(f"WHEN '{p}' THEN {i}" for i, p in enumerate(POSITION_ORDER))
    return f"CASE {col} {cases} ELSE {len(POSITION_ORDER)} END, lower({col})"


def _price_expr(m="m", asof=None):
    """SQL for a market's probability (COLLAB 5), optionally as of a %(asof)s parameter:
    latest tick's mid if it has both sides, else last trade, else ai_prior, else 50."""
    cond = f" AND x.time <= %({asof})s" if asof else ""
    return f"""COALESCE(
        (SELECT CASE WHEN x.best_bid IS NOT NULL AND x.best_ask IS NOT NULL
                     THEN round((x.best_bid + x.best_ask) / 2.0)::int END
           FROM {SCHEMA}.price_ticks x WHERE x.market_id = {m}.id{cond}
          ORDER BY x.time DESC LIMIT 1),
        (SELECT x.price FROM {SCHEMA}.trades x WHERE x.market_id = {m}.id{cond}
          ORDER BY x.time DESC LIMIT 1),
        {m}.ai_prior, 50)"""


def _bucket(bucket):
    if bucket not in BUCKETS:
        raise ValueError(f"bucket must be one of {', '.join(BUCKETS)}")
    return BUCKETS[bucket]


def _bucket_range(width, since, until):
    """Epoch-aligned bucket starts in [floor(since), until), the same grid time_bucket uses."""
    since, until = parse_iso(since), parse_iso(until)
    if (until - since) / width > MAX_BUCKETS:
        since = until - width * MAX_BUCKETS
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    start = epoch + width * ((since - epoch) // width)
    out, t = [], start
    while t < until:
        out.append(t)
        t += width
    return start, out


def _insert(table, fields, allowed, returning="id", conn=None):
    cols = [k for k in fields if k in allowed]
    q = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(
        sql.Identifier(SCHEMA), sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, cols)),
        sql.SQL(", ").join(sql.Placeholder(c) for c in cols))
    if returning:
        q = q + sql.SQL(" RETURNING {}").format(sql.Identifier(returning))
        row = _one(q, {c: fields[c] for c in cols}, conn)
        return row[returning]
    _exec(q, {c: fields[c] for c in cols}, conn)


def _update(table, id_, fields, allowed, conn=None):
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"cannot update {table} columns: {sorted(bad)}")
    if not fields:
        return
    q = sql.SQL("UPDATE {}.{} SET {} WHERE id = %(_id)s").format(
        sql.Identifier(SCHEMA), sql.Identifier(table),
        sql.SQL(", ").join(sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder(k))
                           for k in fields))
    _exec(q, {**fields, "_id": id_}, conn)


# ---------------------------------------------------------------- schema

def _split_sql(text):
    """Split schema.sql into statements, keeping $$ ... $$ blocks intact."""
    text = re.sub(r"--[^\n]*", "", text)
    out, buf, in_dollar = [], [], False
    for line in text.splitlines():
        buf.append(line)
        in_dollar ^= line.count("$$") % 2 == 1
        if not in_dollar and line.rstrip().endswith(";"):
            stmt = "\n".join(buf).strip()
            if stmt.strip(";").strip():
                out.append(stmt)
            buf = []
    if "\n".join(buf).strip():
        out.append("\n".join(buf).strip())
    return out


def ensure_schema(conn=None):
    """Apply schema.sql. Idempotent. Runs in autocommit because continuous aggregates
    can't be created inside a transaction block. Refuses to run without timescaledb."""
    with _use(conn) as c:
        ext = c.execute("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'").fetchone()
        if not ext:
            raise RuntimeError("timescaledb extension is missing; ask the owner (never CREATE EXTENSION)")
        prev = c.autocommit
        if c.info.transaction_status != 0:  # IDLE
            c.commit()
        c.autocommit = True
        try:
            for stmt in _split_sql(SCHEMA_FILE.read_text(encoding="utf-8")):
                c.execute(stmt)
        finally:
            c.autocommit = prev
    log.info("schema %s applied (timescaledb %s)", SCHEMA, ext["extversion"])


def ensure_bot_user(conn=None):
    """Create the house market-maker user if missing. Returns its id."""
    with _use(conn) as c:
        c.execute(f"""INSERT INTO {SCHEMA}.users (username, pw_hash, cash, is_bot)
                      VALUES ('house', '!unusable', 100000000, true)
                      ON CONFLICT (username) DO NOTHING""")
        return c.execute(f"SELECT id FROM {SCHEMA}.users WHERE username = 'house'").fetchone()["id"]


# ---------------------------------------------------------------- users

def create_user(username, pw_hash, is_admin=False, conn=None):
    return _one(f"""INSERT INTO {SCHEMA}.users (username, pw_hash, is_admin)
                    VALUES (%s, %s, %s) RETURNING *""", (username, pw_hash, is_admin), conn)


def get_user(user_id, conn=None):
    return _one(f"SELECT * FROM {SCHEMA}.users WHERE id = %s", (user_id,), conn)


def get_user_by_username(name, conn=None):
    return _one(f"SELECT * FROM {SCHEMA}.users WHERE username = %s", (name,), conn)


def adjust_cash(user_id, delta, conn=None):
    _exec(f"UPDATE {SCHEMA}.users SET cash = cash + %s WHERE id = %s", (delta, user_id), conn)


def list_users(include_bots=False, conn=None):
    where = "" if include_bots else "WHERE NOT is_bot"
    return _all(f"SELECT * FROM {SCHEMA}.users {where} ORDER BY id", None, conn)


# ---------------------------------------------------------------- races

RACE_COLS = {"election", "position", "seats", "description", "resolution_criteria", "closes_at",
             "status", "source_name", "source_url", "article_id", "ai_reason", "created_by_ai",
             "created_at", "resolved_at"}


def create_race(fields, conn=None):
    return _insert("races", fields, RACE_COLS, conn=conn)


def get_race(id, conn=None):
    return _one(f"SELECT * FROM {SCHEMA}.races WHERE id = %s", (id,), conn)


def find_race(election, position, conn=None):
    return _one(f"SELECT * FROM {SCHEMA}.races WHERE election = %s AND position = %s",
                (election, position), conn)


def list_races(status=None, conn=None):
    """status None or 'all' = every race. Ordered by closes_at, then position order."""
    if status in (None, "all"):
        where, params = "", None
    elif isinstance(status, (list, tuple, set)):
        where, params = "WHERE status = ANY(%s)", (list(status),)
    else:
        where, params = "WHERE status = %s", (status,)
    return _all(f"SELECT * FROM {SCHEMA}.races {where} ORDER BY closes_at, {_position_order_sql()}, id",
                params, conn)


def update_race(id, conn=None, **fields):
    """Update a race. status and closes_at are also applied to all of its markets,
    in the same transaction."""
    with (_use(conn) if conn is not None else transaction()) as c:
        _update("races", id, fields, RACE_COLS, c)
        shared = {k: v for k, v in fields.items() if k in ("status", "closes_at", "resolved_at")}
        if shared:
            q = sql.SQL("UPDATE {}.markets SET {} WHERE race_id = %(_id)s").format(
                sql.Identifier(SCHEMA),
                sql.SQL(", ").join(sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder(k))
                                   for k in shared))
            c.execute(q, {**shared, "_id": id})


def race_markets(race_id, conn=None):
    """Candidate markets of a race, each with its current "price"."""
    return _all(f"""SELECT m.*, {_price_expr('m')} AS price FROM {SCHEMA}.markets m
                    WHERE m.race_id = %s ORDER BY m.id""", (race_id,), conn)


# ---------------------------------------------------------------- markets

MARKET_COLS = {"race_id", "candidate", "question", "description", "resolution_criteria", "category",
               "source_name", "source_url", "article_id", "closes_at", "status", "outcome",
               "ai_prior", "ai_reason", "created_by_ai", "created_at", "resolved_at"}


def create_market(fields, conn=None):
    return _insert("markets", fields, MARKET_COLS, conn=conn)


def get_market(id, conn=None):
    return _one(f"SELECT m.*, {_price_expr('m')} AS price FROM {SCHEMA}.markets m WHERE m.id = %s",
                (id,), conn)


def list_markets(status=None, category=None, race_id=None, conn=None):
    conds, params = [], {}
    if status not in (None, "all"):
        conds.append("m.status = %(status)s")
        params["status"] = status
    if category:
        conds.append("m.category = %(category)s")
        params["category"] = category
    if race_id is not None:
        conds.append("m.race_id = %(race_id)s")
        params["race_id"] = race_id
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    return _all(f"""SELECT m.*, {_price_expr('m')} AS price FROM {SCHEMA}.markets m
                    {where} ORDER BY m.closes_at, m.race_id, m.id""", params, conn)


def update_market(id, conn=None, **fields):
    _update("markets", id, fields, MARKET_COLS, conn)


def current_price(market_id, conn=None):
    row = _one(f"""SELECT {_price_expr('m')} AS price FROM {SCHEMA}.markets m WHERE m.id = %s""",
               (market_id,), conn)
    return row["price"] if row else 50


def market_stats(market_ids, conn=None):
    """{market_id: {"price_24h_ago": int|None, "volume_24h": int}} for list endpoints."""
    if not market_ids:
        return {}
    rows = _all(f"""SELECT m.id,
                           CASE WHEN m.created_at <= %(t)s THEN {_price_expr('m', 't')} END AS price_24h_ago,
                           (SELECT COALESCE(sum(qty), 0) FROM {SCHEMA}.trades x
                             WHERE x.market_id = m.id AND x.time > %(t)s) AS volume_24h
                    FROM {SCHEMA}.markets m WHERE m.id = ANY(%(ids)s)""",
                {"t": utcnow() - timedelta(hours=24), "ids": list(market_ids)}, conn)
    return {r["id"]: r for r in rows}


# ---------------------------------------------------------------- articles

ARTICLE_COLS = {"source", "url", "title", "summary", "published_at", "scraped_at", "processed"}


def insert_article(fields, conn=None):
    cols = [k for k in fields if k in ARTICLE_COLS]
    q = sql.SQL("INSERT INTO {}.articles ({}) VALUES ({}) ON CONFLICT (url) DO NOTHING RETURNING id").format(
        sql.Identifier(SCHEMA), sql.SQL(", ").join(map(sql.Identifier, cols)),
        sql.SQL(", ").join(sql.Placeholder(c) for c in cols))
    row = _one(q, {c: fields[c] for c in cols}, conn)
    return row["id"] if row else None


def get_article(id, conn=None):
    return _one(f"SELECT * FROM {SCHEMA}.articles WHERE id = %s", (id,), conn)


def unprocessed_articles(limit=20, conn=None):
    return _all(f"""SELECT * FROM {SCHEMA}.articles WHERE NOT processed
                    ORDER BY published_at DESC NULLS LAST, id DESC LIMIT %s""", (limit,), conn)


def mark_article_processed(id, conn=None):
    _exec(f"UPDATE {SCHEMA}.articles SET processed = true WHERE id = %s", (id,), conn)


def recent_articles(limit=20, before=None, conn=None):
    """Newest first by published_at (scraped_at if unknown); before = only those <= before."""
    cond, params = "", {"limit": limit}
    if before is not None:
        cond = "WHERE COALESCE(published_at, scraped_at) <= %(before)s"
        params["before"] = before
    return _all(f"""SELECT * FROM {SCHEMA}.articles {cond}
                    ORDER BY COALESCE(published_at, scraped_at) DESC, id DESC LIMIT %(limit)s""",
                params, conn)


# ---------------------------------------------------------------- orders / positions

ORDER_COLS = {"id", "market_id", "user_id", "side", "price", "qty", "qty_remaining", "status",
              "created_at", "updated_at"}


def next_order_id(conn=None):
    return _one(f"SELECT nextval('{SCHEMA}.orders_id_seq') AS id", None, conn)["id"]


def insert_order(fields, conn=None):
    _insert("orders", fields, ORDER_COLS, returning=None, conn=conn)


def update_order(id, qty_remaining, status, conn=None):
    _exec(f"""UPDATE {SCHEMA}.orders SET qty_remaining = %s, status = %s, updated_at = now()
              WHERE id = %s""", (qty_remaining, status, id), conn)


def get_order(id, conn=None):
    return _one(f"SELECT * FROM {SCHEMA}.orders WHERE id = %s", (id,), conn)


def open_orders(market_id=None, user_id=None, conn=None):
    conds, params = ["status = 'open'"], {}
    if market_id is not None:
        conds.append("market_id = %(market_id)s")
        params["market_id"] = market_id
    if user_id is not None:
        conds.append("user_id = %(user_id)s")
        params["user_id"] = user_id
    return _all(f"SELECT * FROM {SCHEMA}.orders WHERE {' AND '.join(conds)} ORDER BY created_at, id",
                params, conn)


def user_orders(user_id, status=None, limit=200, conn=None):
    """A user's orders newest first; status None/'all' = every status."""
    cond = "" if status in (None, "all") else "AND status = %(status)s"
    return _all(f"""SELECT * FROM {SCHEMA}.orders WHERE user_id = %(uid)s {cond}
                    ORDER BY created_at DESC, id DESC LIMIT %(limit)s""",
                {"uid": user_id, "status": status, "limit": limit}, conn)


def get_position(user_id, market_id, conn=None):
    row = _one(f"SELECT * FROM {SCHEMA}.positions WHERE user_id = %s AND market_id = %s",
               (user_id, market_id), conn)
    return row or {"user_id": user_id, "market_id": market_id, "qty": 0, "net_cost": 0}


def user_positions(user_id, conn=None):
    """Non-zero positions, joined with market fields and the current YES "price"."""
    return _all(f"""SELECT p.*, m.question, m.candidate, m.race_id, m.status, m.outcome,
                           {_price_expr('m')} AS price
                    FROM {SCHEMA}.positions p JOIN {SCHEMA}.markets m ON m.id = p.market_id
                    WHERE p.user_id = %s AND p.qty <> 0 ORDER BY p.market_id""", (user_id,), conn)


def market_positions(market_id, conn=None):
    return _all(f"""SELECT * FROM {SCHEMA}.positions WHERE market_id = %s AND qty <> 0
                    ORDER BY user_id""", (market_id,), conn)


def add_position(user_id, market_id, delta_qty, delta_cost, conn=None):
    _exec(f"""INSERT INTO {SCHEMA}.positions (user_id, market_id, qty, net_cost)
              VALUES (%(u)s, %(m)s, %(q)s, %(c)s)
              ON CONFLICT (user_id, market_id) DO UPDATE
              SET qty = positions.qty + EXCLUDED.qty, net_cost = positions.net_cost + EXCLUDED.net_cost""",
          {"u": user_id, "m": market_id, "q": delta_qty, "c": delta_cost}, conn)


# ---------------------------------------------------------------- time series: writes

TRADE_COLS = {"time", "market_id", "price", "qty", "buyer_id", "seller_id", "taker_side",
              "maker_order_id", "taker_order_id"}


def insert_trade(fields, conn=None):
    _insert("trades", fields, TRADE_COLS, returning=None, conn=conn)


def record_tick(market_id, best_bid, best_ask, last_price, conn=None, time=None):
    _exec(f"""INSERT INTO {SCHEMA}.price_ticks (time, market_id, best_bid, best_ask, last_price)
              VALUES (COALESCE(%s, now()), %s, %s, %s, %s)""",
          (time, market_id, best_bid, best_ask, last_price), conn)


def last_tick(market_id, conn=None):
    return _one(f"""SELECT * FROM {SCHEMA}.price_ticks WHERE market_id = %s
                    ORDER BY time DESC LIMIT 1""", (market_id,), conn)


def insert_portfolio_snapshot(user_id, cash, equity, conn=None, time=None):
    _exec(f"""INSERT INTO {SCHEMA}.portfolio_snapshots (time, user_id, cash, equity)
              VALUES (COALESCE(%s, now()), %s, %s, %s)""", (time, user_id, cash, equity), conn)


# ---------------------------------------------------------------- time series: reads

def recent_trades(market_id, limit=50, conn=None):
    return _all(f"""SELECT * FROM {SCHEMA}.trades WHERE market_id = %s
                    ORDER BY time DESC LIMIT %s""", (market_id, limit), conn)


def last_trade_price(market_id, conn=None):
    row = _one(f"""SELECT price FROM {SCHEMA}.trades WHERE market_id = %s
                   ORDER BY time DESC LIMIT 1""", (market_id,), conn)
    return row["price"] if row else None


def prices_at(market_ids, t, conn=None):
    """As-of prices: {market_id: price, or None if the market didn't exist yet at t}.
    Same rule as current_price, restricted to data at or before t."""
    if not market_ids:
        return {}
    rows = _all(f"""SELECT m.id, CASE WHEN m.created_at <= %(t)s THEN {_price_expr('m', 't')} END AS price
                    FROM {SCHEMA}.markets m WHERE m.id = ANY(%(ids)s)""",
                {"t": parse_iso(t), "ids": list(market_ids)}, conn)
    return {r["id"]: r["price"] for r in rows}


def price_at(market_id, t, conn=None):
    return prices_at([market_id], t, conn).get(market_id)


def _close_series(market_ids, width, start, until, conn=None):
    """Gap-filled closing probability per market: {market_id: {bucket: close|None}}.
    Observations are trades plus two-sided tick mids; time_bucket_gapfill + locf carries
    the last value forward through empty buckets."""
    rows = _all(f"""
        SELECT time_bucket_gapfill(%(w)s::interval, o.time, %(start)s::timestamptz, %(until)s::timestamptz) AS t,
               o.market_id,
               locf(last(o.p, o.time)) AS close
        FROM (
            SELECT time, market_id, price AS p FROM {SCHEMA}.trades
             WHERE market_id = ANY(%(ids)s) AND time >= %(start)s AND time < %(until)s
            UNION ALL
            SELECT time, market_id, round((best_bid + best_ask) / 2.0)::int FROM {SCHEMA}.price_ticks
             WHERE market_id = ANY(%(ids)s) AND best_bid IS NOT NULL AND best_ask IS NOT NULL
               AND time >= %(start)s AND time < %(until)s
        ) o
        GROUP BY t, o.market_id
        ORDER BY o.market_id, t""",
        {"w": width, "start": start, "until": until, "ids": list(market_ids)}, conn)
    out = {mid: {} for mid in market_ids}
    for r in rows:
        out[r["market_id"]][r["t"]] = r["close"]
    return out


def _fill(buckets, width, observed, initial, created_at, prior):
    """Carry values forward across buckets: observed value, else previous, else the
    market's prior once it exists. Buckets before the market existed stay None."""
    out, prev = [], initial
    for t in buckets:
        v = observed.get(t)
        if v is not None:
            prev = v
        elif prev is None and created_at is not None and created_at < t + width:
            prev = prior if prior is not None else 50
        out.append(prev)
    return out


def close_series(market_ids, bucket, since, until=None, conn=None):
    """{market_id: [(bucket_start, close|None), ...]} with no holes once a market exists."""
    width = _bucket(bucket)
    until = parse_iso(until) if until else utcnow()
    start, buckets = _bucket_range(width, since, until)
    with _use(conn) as c:
        observed = _close_series(market_ids, width, start, until, c)
        initial = prices_at(market_ids, start, c)
        meta = {r["id"]: r for r in c.execute(
            f"SELECT id, created_at, ai_prior FROM {SCHEMA}.markets WHERE id = ANY(%s)",
            (list(market_ids),)).fetchall()}
    out = {}
    for mid in market_ids:
        m = meta.get(mid, {})
        vals = _fill(buckets, width, observed.get(mid, {}), initial.get(mid),
                     m.get("created_at"), m.get("ai_prior"))
        out[mid] = list(zip(buckets, vals))
    return out


def price_history(market_id, bucket, since, conn=None):
    """Gap-filled OHLCV candles for one market:
    [{"t": ISO, "open", "high", "low", "close", "volume"}].
    5m / 1h read the candles_5m / candles_1h continuous aggregates; 1m reads raw trades.
    Buckets without trades are flat candles at the carried-forward close (volume 0).
    Before the first trade, close falls back to tick mids, then ai_prior."""
    width = _bucket(bucket)
    until = utcnow()
    start, buckets = _bucket_range(width, since, until)
    params = {"w": width, "start": start, "until": until, "id": market_id}
    if bucket in ("5m", "1h"):
        view = "candles_5m" if bucket == "5m" else "candles_1h"
        q = f"""SELECT time_bucket_gapfill(%(w)s::interval, bucket, %(start)s::timestamptz, %(until)s::timestamptz) AS t,
                       first(open, bucket) AS open, max(high) AS high, min(low) AS low,
                       locf(last(close, bucket)) AS close, sum(volume) AS volume
                FROM {SCHEMA}.{view}
                WHERE market_id = %(id)s AND bucket >= %(start)s AND bucket < %(until)s
                GROUP BY t ORDER BY t"""
    else:
        q = f"""SELECT time_bucket_gapfill(%(w)s::interval, time, %(start)s::timestamptz, %(until)s::timestamptz) AS t,
                       first(price, time) AS open, max(price) AS high, min(price) AS low,
                       locf(last(price, time)) AS close, sum(qty) AS volume
                FROM {SCHEMA}.trades
                WHERE market_id = %(id)s AND time >= %(start)s AND time < %(until)s
                GROUP BY t ORDER BY t"""
    with _use(conn) as c:
        candles = {r["t"]: r for r in c.execute(q, params).fetchall()}
        fallback = dict(close_series([market_id], bucket, start, until, c)[market_id])
    points, prev_close = [], None
    for t in buckets:
        r = candles.get(t) or {}
        close = r.get("close")
        if close is None:
            close = prev_close if prev_close is not None else fallback.get(t)
        prev_close = close if close is not None else prev_close
        has_trades = r.get("open") is not None
        points.append({
            "t": iso(t),
            "open": int(r["open"]) if has_trades else close,
            "high": int(r["high"]) if has_trades else close,
            "low": int(r["low"]) if has_trades else close,
            "close": int(close) if close is not None else None,
            "volume": int(r.get("volume") or 0),
        })
    return points


def race_history(race_id, bucket, since, conn=None):
    """One gap-filled close series per candidate:
    [{"market_id", "candidate", "points": [{"t": ISO, "close": int}]}]."""
    with _use(conn) as c:
        markets = c.execute(f"SELECT id, candidate FROM {SCHEMA}.markets WHERE race_id = %s ORDER BY id",
                            (race_id,)).fetchall()
        if not markets:
            return []
        series = close_series([m["id"] for m in markets], bucket, since, conn=c)
    return [{"market_id": m["id"], "candidate": m["candidate"],
             "points": [{"t": iso(t), "close": v} for t, v in series[m["id"]] if v is not None]}
            for m in markets]


def race_status_at(race, t):
    if race.get("resolved_at") and race["resolved_at"] <= t:
        return "resolved"
    if race["closes_at"] <= t:
        return "closed"
    return "open"


def timemachine(t, conn=None):
    """Every public race that existed at t, with its status and candidate prices as of t.
    One query: races x markets with an as-of LATERAL price lookup.
    [{"id","election","position","seats","status","candidates":[{"market_id","candidate","price"}]}]"""
    t = parse_iso(t)
    rows = _all(f"""
        SELECT r.id, r.election, r.position, r.seats, r.closes_at, r.resolved_at,
               m.id AS market_id, m.candidate, p.price
        FROM {SCHEMA}.races r
        LEFT JOIN {SCHEMA}.markets m ON m.race_id = r.id AND m.created_at <= %(t)s
        LEFT JOIN LATERAL (SELECT {_price_expr('m', 't')} AS price) p ON m.id IS NOT NULL
        WHERE r.created_at <= %(t)s AND r.status NOT IN ('proposed', 'rejected')""",
        {"t": t}, conn)
    races = {}
    for r in rows:
        race = races.get(r["id"])
        if race is None:
            race = races[r["id"]] = {
                "id": r["id"], "election": r["election"], "position": r["position"],
                "seats": r["seats"], "status": race_status_at(r, t),
                "closes_at": r["closes_at"], "candidates": []}
        if r["market_id"] is not None:
            race["candidates"].append({"market_id": r["market_id"], "candidate": r["candidate"],
                                       "price": r["price"]})
    out = sorted(races.values(), key=lambda x: (x["closes_at"], position_rank(x["position"]), x["id"]))
    for race in out:
        race["candidates"].sort(key=lambda c: (-c["price"], c["candidate"]))
        del race["closes_at"]
    return out


def timemachine_range(conn=None):
    row = _one(f"""SELECT min(created_at) AS start FROM {SCHEMA}.races
                   WHERE status NOT IN ('proposed', 'rejected')""", None, conn)
    now = utcnow()
    return {"start": row["start"] or now, "end": now}


def equity(user_id, conn=None):
    """cash + SUM(position qty * current price). Shorts contribute negatively."""
    row = _one(f"""SELECT u.cash + COALESCE((
                        SELECT sum(p.qty * {_price_expr('m')})
                        FROM {SCHEMA}.positions p JOIN {SCHEMA}.markets m ON m.id = p.market_id
                        WHERE p.user_id = u.id AND p.qty <> 0), 0)::bigint AS equity
                   FROM {SCHEMA}.users u WHERE u.id = %s""", (user_id,), conn)
    return int(row["equity"]) if row else 0


def leaderboard(limit=20, conn=None):
    """[{"rank", "user_id", "username", "equity"}] for non-bot users, richest first."""
    rows = _all(f"""SELECT u.id AS user_id, u.username, u.cash + COALESCE((
                        SELECT sum(p.qty * {_price_expr('m')})
                        FROM {SCHEMA}.positions p JOIN {SCHEMA}.markets m ON m.id = p.market_id
                        WHERE p.user_id = u.id AND p.qty <> 0), 0)::bigint AS equity
                    FROM {SCHEMA}.users u WHERE NOT u.is_bot
                    ORDER BY equity DESC, u.username LIMIT %s""", (limit,), conn)
    return [{"rank": i + 1, "user_id": r["user_id"], "username": r["username"],
             "equity": int(r["equity"])} for i, r in enumerate(rows)]


def equity_history(user_ids, bucket, since=None, conn=None):
    """{user_id: [{"t": ISO, "equity": int}]} from portfolio_snapshots (last value per bucket)."""
    if not user_ids:
        return {}
    width = _bucket(bucket)
    params = {"w": width, "ids": list(user_ids), "since": since}
    cond = "AND time >= %(since)s" if since is not None else ""
    rows = _all(f"""SELECT time_bucket(%(w)s::interval, time) AS t, user_id,
                           last(equity, time) AS equity
                    FROM {SCHEMA}.portfolio_snapshots
                    WHERE user_id = ANY(%(ids)s) {cond}
                    GROUP BY t, user_id ORDER BY user_id, t""", params, conn)
    out = {uid: [] for uid in user_ids}
    for r in rows:
        out[r["user_id"]].append({"t": iso(r["t"]), "equity": int(r["equity"])})
    return out


# ---------------------------------------------------------------- AI

BRIEF_COLS = {"race_id", "market_id", "kind", "suggested_winner_ids", "text", "suggested_outcome",
              "evidence_url", "confidence", "created_at"}


def save_brief(fields, conn=None):
    return _insert("ai_briefs", fields, BRIEF_COLS, conn=conn)


def latest_brief(race_id=None, kind="brief", conn=None):
    """Newest brief of this kind for a race. race_id None = the daily digest."""
    if race_id is None:
        if kind == "brief":
            kind = "digest"
        return _one(f"""SELECT * FROM {SCHEMA}.ai_briefs WHERE race_id IS NULL AND kind = %s
                        ORDER BY created_at DESC, id DESC LIMIT 1""", (kind,), conn)
    return _one(f"""SELECT * FROM {SCHEMA}.ai_briefs WHERE race_id = %s AND kind = %s
                    ORDER BY created_at DESC, id DESC LIMIT 1""", (race_id, kind), conn)


# ---------------------------------------------------------------- Timescale stats

def timescale_stats(conn=None):
    """Hypertable chunk counts, compression and cagg info for an "under the hood" panel."""
    with _use(conn) as c:
        hts = c.execute("""SELECT hypertable_name, num_chunks, compression_enabled
                           FROM timescaledb_information.hypertables
                           WHERE hypertable_schema = %s ORDER BY hypertable_name""", (SCHEMA,)).fetchall()
        comp = c.execute(f"""SELECT * FROM hypertable_compression_stats('{SCHEMA}.price_ticks')""").fetchone()
        caggs = c.execute("""SELECT view_name, materialized_only FROM timescaledb_information.continuous_aggregates
                             WHERE view_schema = %s ORDER BY view_name""", (SCHEMA,)).fetchall()
        jobs = c.execute("""SELECT j.job_id, j.proc_name, j.hypertable_name, j.schedule_interval,
                                   s.last_run_status, s.last_successful_finish, s.next_start
                            FROM timescaledb_information.jobs j
                            LEFT JOIN timescaledb_information.job_stats s USING (job_id)
                            WHERE j.hypertable_schema = %s ORDER BY j.job_id""", (SCHEMA,)).fetchall()
        rows = {r["name"]: r["n"] for r in c.execute(f"""
            SELECT 'trades' AS name, count(*) AS n FROM {SCHEMA}.trades
            UNION ALL SELECT 'price_ticks', count(*) FROM {SCHEMA}.price_ticks
            UNION ALL SELECT 'portfolio_snapshots', count(*) FROM {SCHEMA}.portfolio_snapshots""").fetchall()}
    return {"hypertables": hts, "compression": comp, "continuous_aggregates": caggs,
            "jobs": jobs, "rows": rows}
