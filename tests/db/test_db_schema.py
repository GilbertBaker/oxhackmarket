"""The schema matches COLLAB 6 and the Timescale features are configured as intended."""
import re
from datetime import timedelta

import pytest

import db

S = db.SCHEMA

# COLLAB 6 column contract
COLUMNS = {
    "users": {"id", "username", "pw_hash", "cash", "is_admin", "is_bot", "created_at"},
    "articles": {"id", "source", "url", "title", "summary", "published_at", "scraped_at", "processed"},
    "races": {"id", "election", "position", "seats", "description", "resolution_criteria", "closes_at",
              "status", "source_name", "source_url", "article_id", "ai_reason", "created_by_ai",
              "created_at", "resolved_at", "kind", "category"},
    "markets": {"id", "race_id", "candidate", "question", "description", "resolution_criteria",
                "category", "source_name", "source_url", "article_id", "closes_at", "status", "outcome",
                "ai_prior", "ai_reason", "created_by_ai", "created_at", "resolved_at"},
    "orders": {"id", "market_id", "user_id", "side", "price", "qty", "qty_remaining", "status",
               "created_at", "updated_at"},
    "positions": {"user_id", "market_id", "qty", "net_cost"},
    "trades": {"time", "market_id", "price", "qty", "buyer_id", "seller_id", "taker_side",
               "maker_order_id", "taker_order_id"},
    "price_ticks": {"time", "market_id", "best_bid", "best_ask", "last_price"},
    "portfolio_snapshots": {"time", "user_id", "cash", "equity"},
    "ai_briefs": {"id", "race_id", "market_id", "kind", "suggested_winner_ids", "text",
                  "suggested_outcome", "evidence_url", "confidence", "created_at"},
    # admin portal: reference data, settings, audit log
    "categories": {"slug", "name", "sort"},
    "sports": {"name", "icon", "pattern", "sort"},
    "competitions": {"id", "name", "sport", "category", "has_draw", "active"},
    "teams": {"id", "name", "kind", "active"},
    "terms": {"id", "name", "starts_on", "ends_on"},
    "settings": {"key", "value", "description", "updated_at", "updated_by"},
    "admin_actions": {"time", "admin_id", "action", "race_id", "market_id", "details"},
}
HYPERTABLES = {"trades", "price_ticks", "portfolio_snapshots", "admin_actions"}
CHUNK = {"trades": timedelta(days=1), "price_ticks": timedelta(days=1),
         "portfolio_snapshots": timedelta(days=1), "admin_actions": timedelta(days=7)}
CAGGS = {"candles_5m", "candles_1h"}


def q(query, params=None):
    with db.pool().connection() as c:
        return c.execute(query, params).fetchall()


@pytest.fixture(scope="module", autouse=True)
def _schema(tag):
    pass


def test_timescaledb_installed():
    ext = q("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
    assert ext, "timescaledb extension missing"
    major, minor = (int(x) for x in ext[0]["extversion"].split(".")[:2])
    assert (major, minor) >= (2, 13)


@pytest.mark.parametrize("table", sorted(COLUMNS))
def test_table_columns_match_contract(table):
    cols = {r["column_name"] for r in q(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s",
        (S, table))}
    assert cols == COLUMNS[table]


def test_money_columns_are_bigint():
    rows = {(r["table_name"], r["column_name"]): r["data_type"] for r in q(
        "SELECT table_name, column_name, data_type FROM information_schema.columns WHERE table_schema = %s",
        (S,))}
    for key in [("users", "cash"), ("positions", "net_cost"), ("portfolio_snapshots", "cash"),
                ("portfolio_snapshots", "equity"), ("orders", "id")]:
        assert rows[key] == "bigint", key
    for t in HYPERTABLES:
        assert rows[(t, "time")] == "timestamp with time zone"


def test_constraints():
    cons = q("""SELECT conrelid::regclass::text AS tbl, contype, pg_get_constraintdef(oid) AS def
                FROM pg_constraint WHERE connamespace = %s::regnamespace""", (S,))
    defs = {(c["tbl"].split(".")[-1], c["contype"], c["def"]) for c in cons}
    assert ("users", "u", "UNIQUE (username)") in defs
    assert ("articles", "u", "UNIQUE (url)") in defs
    assert ("races", "u", "UNIQUE (election, \"position\")") in defs or \
           ("races", "u", "UNIQUE (election, position)") in defs
    assert ("markets", "u", "UNIQUE (race_id, candidate)") in defs
    assert any(t == "races" and k == "c" and "kind" in d and "binary" in d for t, k, d in defs)
    assert ("positions", "p", "PRIMARY KEY (user_id, market_id)") in defs
    # pg_get_constraintdef drops the schema when it is on the search_path
    assert any(t == "markets" and k == "f" and re.search(r"REFERENCES (oxmarket\.)?races\(id\)", d)
               for t, k, d in defs)
    # hypertables must not carry a PK/unique without the time column
    for t, k, d in defs:
        if t in HYPERTABLES and k in ("p", "u"):
            assert "time" in d, (t, d)


def test_orders_sequence():
    a, b = db.next_order_id(), db.next_order_id()
    assert b > a


def test_hypertables_and_chunk_interval():
    rows = q("""SELECT hypertable_name, time_interval FROM timescaledb_information.dimensions
                WHERE hypertable_schema = %s AND column_name = 'time'""", (S,))
    assert {r["hypertable_name"] for r in rows} == HYPERTABLES
    assert all(r["time_interval"] == CHUNK[r["hypertable_name"]] for r in rows)


def test_continuous_aggregates_are_realtime():
    rows = q("""SELECT view_name, materialized_only, materialization_hypertable_name
                FROM timescaledb_information.continuous_aggregates WHERE view_schema = %s""", (S,))
    assert {r["view_name"] for r in rows} == CAGGS
    assert all(r["materialized_only"] is False for r in rows), "real-time aggregation should be on"
    cols = {r["column_name"] for r in q(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = 'candles_5m'",
        (S,))}
    assert cols == {"bucket", "market_id", "open", "high", "low", "close", "volume"}


def _jobs():
    return q("""SELECT j.job_id, j.proc_name, j.hypertable_name, j.schedule_interval, j.config,
                       s.last_run_status, s.total_failures
                FROM timescaledb_information.jobs j
                LEFT JOIN timescaledb_information.job_stats s USING (job_id)
                WHERE j.hypertable_schema = %s""", (S,))


def test_policies():
    jobs = _jobs()
    by = {(j["proc_name"], j["hypertable_name"]): j for j in jobs}
    r5 = by[("policy_refresh_continuous_aggregate", "candles_5m")]
    r1 = by[("policy_refresh_continuous_aggregate", "candles_1h")]
    assert r5["schedule_interval"] == timedelta(minutes=1)
    assert r1["schedule_interval"] == timedelta(minutes=5)
    comp = by[("policy_compression", "price_ticks")]
    assert comp["config"]["compress_after"] in ("1 day", "1 day 00:00:00", "24:00:00")
    ret = by[("policy_retention", "price_ticks")]
    assert ret["config"]["drop_after"] in ("90 days", "90 days 00:00:00")
    assert len(jobs) == 4, "no duplicate policies"


def test_refresh_jobs_have_not_failed():
    for j in _jobs():
        if j["proc_name"] == "policy_refresh_continuous_aggregate" and j["last_run_status"]:
            assert j["last_run_status"] == "Success", j


def test_compression_settings():
    try:
        rows = q("""SELECT segmentby, orderby FROM timescaledb_information.hypertable_compression_settings
                    WHERE hypertable = %s::regclass""", (f"{S}.price_ticks",))
        assert rows and rows[0]["segmentby"] == "market_id" and \
            "time DESC" in rows[0]["orderby"].replace('"', "")
    except Exception as e:  # older Timescale: per-column view
        if "does not exist" not in str(e):
            raise
        rows = q("""SELECT attname, segmentby_column_index, orderby_column_index, orderby_asc
                    FROM timescaledb_information.compression_settings
                    WHERE hypertable_schema = %s AND hypertable_name = 'price_ticks'""", (S,))
        seg = [r["attname"] for r in rows if r["segmentby_column_index"]]
        assert seg == ["market_id"]


def test_ensure_schema_is_idempotent():
    before = (len(_jobs()), len(q("SELECT 1 FROM pg_class WHERE relnamespace = %s::regnamespace", (S,))))
    db.ensure_schema()
    db.ensure_schema()
    after = (len(_jobs()), len(q("SELECT 1 FROM pg_class WHERE relnamespace = %s::regnamespace", (S,))))
    assert before == after


def test_nothing_of_ours_outside_oxmarket():
    names = list(COLUMNS) + list(CAGGS)
    rows = q("""SELECT table_schema, table_name FROM information_schema.tables
                WHERE table_name = ANY(%s) AND table_schema <> %s
                  AND table_schema NOT LIKE '\\_timescaledb%%'""", (names, S))
    assert rows == [], f"our table names found outside {S}: {rows}"
    hts = q("SELECT hypertable_schema, hypertable_name FROM timescaledb_information.hypertables "
            "WHERE hypertable_name = ANY(%s) AND hypertable_schema <> %s", (names, S))
    assert hts == []


def test_connection_settings():
    with db.pool().connection() as c:
        assert c.execute("SHOW search_path").fetchone()["search_path"].startswith(S)
        assert c.execute("SHOW timezone").fetchone()["TimeZone"] == "UTC"


def test_reference_data_and_settings_seeded():
    assert db.current_term() is not None
    assert {"cuppers", "news"} <= {r["slug"] for r in db.list_ref("categories")}
    assert len(db.list_ref("teams")) >= 30
    comps = {r["name"]: r for r in db.list_ref("competitions")}
    assert comps["Men's Football Cuppers"]["has_draw"] is True
    keys = {r["key"] for r in db.all_settings()}
    assert {"mm.size", "mm.half_spread", "users.starting_cash", "defaults.priors.draw"} <= keys
    assert isinstance(db.get_setting("mm.size"), int)
