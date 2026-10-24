-- OxMarket schema (owner B). COLLAB section 6 is the contract.
-- Idempotent: safe to run on every startup. Everything lives in schema oxmarket;
-- nothing outside it is ever created, altered or dropped.
-- Requires the timescaledb extension to already exist (we never CREATE EXTENSION).

CREATE SCHEMA IF NOT EXISTS oxmarket;

-- ---------------------------------------------------------------- plain tables

CREATE TABLE IF NOT EXISTS oxmarket.users (
    id          serial PRIMARY KEY,
    username    text UNIQUE NOT NULL,
    pw_hash     text NOT NULL,
    cash        bigint NOT NULL DEFAULT 100000,
    is_admin    boolean DEFAULT false,
    is_bot      boolean DEFAULT false,
    created_at  timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS oxmarket.articles (
    id            serial PRIMARY KEY,
    source        text NOT NULL,
    url           text UNIQUE NOT NULL,
    title         text NOT NULL,
    summary       text,
    published_at  timestamptz,
    scraped_at    timestamptz DEFAULT now(),
    processed     boolean DEFAULT false
);
CREATE INDEX IF NOT EXISTS articles_published_idx ON oxmarket.articles (published_at DESC);

CREATE TABLE IF NOT EXISTS oxmarket.races (
    id                   serial PRIMARY KEY,
    election             text NOT NULL,
    position             text NOT NULL,
    seats                int NOT NULL DEFAULT 1,
    description          text,
    resolution_criteria  text NOT NULL,
    closes_at            timestamptz NOT NULL,
    status               text NOT NULL DEFAULT 'proposed',
    source_name          text,
    source_url           text,
    article_id           int NULL REFERENCES oxmarket.articles(id),
    ai_reason            text NULL,
    created_by_ai        boolean DEFAULT false,
    created_at           timestamptz DEFAULT now(),
    resolved_at          timestamptz NULL,
    UNIQUE (election, position)
);

CREATE TABLE IF NOT EXISTS oxmarket.markets (
    id                   serial PRIMARY KEY,
    race_id              int NOT NULL REFERENCES oxmarket.races(id),
    candidate            text NOT NULL,
    question             text NOT NULL,
    description          text,
    resolution_criteria  text NOT NULL,
    category             text DEFAULT 'union',
    source_name          text,
    source_url           text,
    article_id           int NULL REFERENCES oxmarket.articles(id),
    closes_at            timestamptz NOT NULL,
    status               text NOT NULL DEFAULT 'proposed',
    outcome              text NULL,
    ai_prior             int NULL,
    ai_reason            text NULL,
    created_by_ai        boolean DEFAULT false,
    created_at           timestamptz DEFAULT now(),
    resolved_at          timestamptz NULL,
    UNIQUE (race_id, candidate)
);
CREATE INDEX IF NOT EXISTS markets_race_idx ON oxmarket.markets (race_id);

CREATE SEQUENCE IF NOT EXISTS oxmarket.orders_id_seq;

CREATE TABLE IF NOT EXISTS oxmarket.orders (
    id             bigint PRIMARY KEY,
    market_id      int NOT NULL,
    user_id        int NOT NULL,
    side           text NOT NULL,
    price          int NOT NULL,
    qty            int NOT NULL,
    qty_remaining  int NOT NULL,
    status         text NOT NULL,
    created_at     timestamptz DEFAULT now(),
    updated_at     timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS orders_open_idx ON oxmarket.orders (market_id) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS orders_user_idx ON oxmarket.orders (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS oxmarket.positions (
    user_id    int,
    market_id  int,
    qty        int NOT NULL DEFAULT 0,
    net_cost   bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, market_id)
);

CREATE TABLE IF NOT EXISTS oxmarket.ai_briefs (
    id                    serial PRIMARY KEY,
    race_id               int NULL,
    market_id             int NULL,
    kind                  text NOT NULL,
    suggested_winner_ids  int[] NULL,
    text                  text NOT NULL,
    suggested_outcome     text NULL,
    evidence_url          text NULL,
    confidence            int NULL,
    created_at            timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ai_briefs_lookup_idx ON oxmarket.ai_briefs (kind, race_id, created_at DESC);

-- ---------------------------------------------------------------- hypertables
-- No serial PKs here: a hypertable's unique constraints must include the time column.

CREATE TABLE IF NOT EXISTS oxmarket.trades (
    time            timestamptz NOT NULL DEFAULT now(),
    market_id       int NOT NULL,
    price           int NOT NULL,
    qty             int NOT NULL,
    buyer_id        int NOT NULL,
    seller_id       int NOT NULL,
    taker_side      text NOT NULL,
    maker_order_id  bigint,
    taker_order_id  bigint NULL
);
SELECT create_hypertable('oxmarket.trades', 'time',
                         chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS trades_market_time_idx ON oxmarket.trades (market_id, time DESC);

CREATE TABLE IF NOT EXISTS oxmarket.price_ticks (
    time        timestamptz NOT NULL DEFAULT now(),
    market_id   int NOT NULL,
    best_bid    int NULL,
    best_ask    int NULL,
    last_price  int NULL
);
SELECT create_hypertable('oxmarket.price_ticks', 'time',
                         chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS price_ticks_market_time_idx ON oxmarket.price_ticks (market_id, time DESC);

CREATE TABLE IF NOT EXISTS oxmarket.portfolio_snapshots (
    time     timestamptz NOT NULL DEFAULT now(),
    user_id  int NOT NULL,
    cash     bigint NOT NULL,
    equity   bigint NOT NULL
);
SELECT create_hypertable('oxmarket.portfolio_snapshots', 'time',
                         chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS portfolio_snapshots_user_time_idx
    ON oxmarket.portfolio_snapshots (user_id, time DESC);

-- ---------------------------------------------------------------- continuous aggregates
-- OHLCV candles maintained incrementally by Timescale. materialized_only = false turns on
-- real-time aggregation: the newest, not-yet-materialised trades are merged in at query time.

CREATE MATERIALIZED VIEW IF NOT EXISTS oxmarket.candles_5m
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket(INTERVAL '5 minutes', time) AS bucket,
       market_id,
       first(price, time) AS open,
       max(price)         AS high,
       min(price)         AS low,
       last(price, time)  AS close,
       sum(qty)           AS volume
FROM oxmarket.trades
GROUP BY bucket, market_id
WITH NO DATA;

CREATE MATERIALIZED VIEW IF NOT EXISTS oxmarket.candles_1h
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket(INTERVAL '1 hour', time) AS bucket,
       market_id,
       first(price, time) AS open,
       max(price)         AS high,
       min(price)         AS low,
       last(price, time)  AS close,
       sum(qty)           AS volume
FROM oxmarket.trades
GROUP BY bucket, market_id
WITH NO DATA;

SELECT add_continuous_aggregate_policy('oxmarket.candles_5m',
         start_offset => INTERVAL '7 days', end_offset => INTERVAL '1 minute',
         schedule_interval => INTERVAL '1 minute', if_not_exists => TRUE);
SELECT add_continuous_aggregate_policy('oxmarket.candles_1h',
         start_offset => INTERVAL '30 days', end_offset => INTERVAL '1 hour',
         schedule_interval => INTERVAL '5 minutes', if_not_exists => TRUE);

-- ---------------------------------------------------------------- compression + retention
-- Ticks are the high-volume table: compress chunks older than a day (segmented per market,
-- so a single market's history decompresses on its own), and drop raw ticks after 90 days.
-- ALTER ... SET (compress) is only run once: re-running it with compressed chunks present fails.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM timescaledb_information.hypertables
                   WHERE hypertable_schema = 'oxmarket' AND hypertable_name = 'price_ticks'
                     AND compression_enabled) THEN
        ALTER TABLE oxmarket.price_ticks SET (timescaledb.compress,
                                              timescaledb.compress_segmentby = 'market_id',
                                              timescaledb.compress_orderby = 'time DESC');
    END IF;
END $$;

SELECT add_compression_policy('oxmarket.price_ticks', INTERVAL '1 day', if_not_exists => TRUE);
SELECT add_retention_policy('oxmarket.price_ticks', INTERVAL '90 days', if_not_exists => TRUE);
