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

-- ---------------------------------------------------------------- market kinds
-- kind: 'match'  = Cuppers fixture (Home / Draw / Away outcomes, normalised to 100),
--       'multi'  = any multi-outcome event (exactly `seats` winners),
--       'binary' = one YES/NO question (a single market named 'Yes', never normalised).

ALTER TABLE oxmarket.races ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'match';
ALTER TABLE oxmarket.races ADD COLUMN IF NOT EXISTS category text NOT NULL DEFAULT 'cuppers';
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'races_kind_check'
                   AND connamespace = 'oxmarket'::regnamespace) THEN
        ALTER TABLE oxmarket.races ADD CONSTRAINT races_kind_check
            CHECK (kind IN ('match', 'multi', 'binary'));
    END IF;
END $$;

-- ---------------------------------------------------------------- reference data
-- Edited from the admin portal. The seed rows below are only inserted when missing.

CREATE TABLE IF NOT EXISTS oxmarket.categories (
    slug  text PRIMARY KEY,
    name  text NOT NULL,
    sort  int NOT NULL DEFAULT 100
);
INSERT INTO oxmarket.categories (slug, name, sort) VALUES
    ('cuppers', 'Cuppers', 10), ('news', 'Oxford news', 20), ('union', 'Oxford Union', 30),
    ('university', 'University', 40), ('other', 'Other', 90)
ON CONFLICT (slug) DO NOTHING;

CREATE TABLE IF NOT EXISTS oxmarket.sports (
    name     text PRIMARY KEY,
    icon     text NOT NULL,
    pattern  text NOT NULL,
    sort     int NOT NULL DEFAULT 100
);
INSERT INTO oxmarket.sports (name, icon, pattern, sort) VALUES
    ('Football', '⚽', 'football|soccer|futsal', 10), ('Rugby', '🏉', 'rugby', 20),
    ('Hockey', '🏑', 'hockey', 30), ('Netball', '🏐', 'netball', 40),
    ('Lacrosse', '🥍', 'lacrosse', 50), ('Rowing', '🚣', 'rowing|regatta|torpids|eights', 60),
    ('Cricket', '🏏', 'cricket', 70), ('Basketball', '🏀', 'basketball', 80),
    ('Tennis', '🎾', 'tennis', 90), ('Badminton', '🏸', 'badminton', 100),
    ('Volleyball', '🏐', 'volleyball', 110), ('Ultimate', '🥏', 'ultimate|frisbee', 120),
    ('Squash', '🎾', 'squash', 130), ('Darts', '🎯', 'darts', 140),
    ('Pool', '🎱', 'pool|snooker', 150), ('Chess', '♟️', 'chess', 160)
ON CONFLICT (name) DO NOTHING;

CREATE TABLE IF NOT EXISTS oxmarket.competitions (
    id        serial PRIMARY KEY,
    name      text UNIQUE NOT NULL,
    sport     text NULL REFERENCES oxmarket.sports(name) ON UPDATE CASCADE,
    category  text NOT NULL DEFAULT 'cuppers' REFERENCES oxmarket.categories(slug) ON UPDATE CASCADE,
    has_draw  boolean NOT NULL DEFAULT false,
    active    boolean NOT NULL DEFAULT true
);
INSERT INTO oxmarket.competitions (name, sport, has_draw) VALUES
    ('Men''s Football Cuppers', 'Football', true), ('Women''s Football Cuppers', 'Football', true),
    ('Men''s Rugby Cuppers', 'Rugby', false), ('Women''s Rugby Cuppers', 'Rugby', false),
    ('Men''s Hockey Cuppers', 'Hockey', true), ('Mixed Hockey Cuppers', 'Hockey', true),
    ('Mixed Netball Cuppers', 'Netball', false), ('Mixed Lacrosse Cuppers', 'Lacrosse', false),
    ('Basketball Cuppers', 'Basketball', false), ('Badminton Cuppers', 'Badminton', false),
    ('Squash Cuppers', 'Squash', false), ('Ultimate Cuppers', 'Ultimate', false),
    ('Chess Cuppers', 'Chess', true), ('Darts Cuppers', 'Darts', false)
ON CONFLICT (name) DO NOTHING;

CREATE TABLE IF NOT EXISTS oxmarket.teams (
    id      serial PRIMARY KEY,
    name    text UNIQUE NOT NULL,
    kind    text NOT NULL DEFAULT 'college',
    active  boolean NOT NULL DEFAULT true
);
INSERT INTO oxmarket.teams (name) VALUES
    ('Balliol'), ('Blackfriars'), ('Brasenose'), ('Campion Hall'), ('Christ Church'),
    ('Corpus Christi'), ('Exeter'), ('Green Templeton'), ('Harris Manchester'), ('Hertford'),
    ('Jesus'), ('Keble'), ('Kellogg'), ('Lady Margaret Hall'), ('Linacre'), ('Lincoln'),
    ('Magdalen'), ('Mansfield'), ('Merton'), ('New College'), ('Nuffield'), ('Oriel'),
    ('Pembroke'), ('Queen''s'), ('Regent''s Park'), ('Reuben'), ('Somerville'), ('St Anne''s'),
    ('St Antony''s'), ('St Catherine''s'), ('St Cross'), ('St Edmund Hall'), ('St Hilda''s'),
    ('St Hugh''s'), ('St John''s'), ('St Peter''s'), ('St Stephen''s House'), ('Trinity'),
    ('University'), ('Wadham'), ('Wolfson'), ('Worcester'), ('Wycliffe Hall')
ON CONFLICT (name) DO NOTHING;

-- name is also the races.election value, e.g. 'Michaelmas 2026'
CREATE TABLE IF NOT EXISTS oxmarket.terms (
    id         serial PRIMARY KEY,
    name       text UNIQUE NOT NULL,
    starts_on  date NOT NULL,
    ends_on    date NOT NULL
);
INSERT INTO oxmarket.terms (name, starts_on, ends_on) VALUES
    ('Trinity 2026', '2026-04-01', '2026-09-30'),
    ('Michaelmas 2026', '2026-10-01', '2026-12-31'),
    ('Hilary 2027', '2027-01-01', '2027-03-31'),
    ('Trinity 2027', '2027-04-01', '2027-09-30')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------- settings
-- Runtime knobs read by the server (cached for ~30 s), editable in the admin portal.

CREATE TABLE IF NOT EXISTS oxmarket.settings (
    key          text PRIMARY KEY,
    value        jsonb NOT NULL,
    description  text,
    updated_at   timestamptz DEFAULT now(),
    updated_by   int NULL REFERENCES oxmarket.users(id)
);
INSERT INTO oxmarket.settings (key, value, description) VALUES
    ('mm.interval', '10', 'Market maker: seconds between requote passes'),
    ('mm.size', '50', 'Market maker: shares quoted on each side'),
    ('mm.half_spread', '2', 'Market maker: pence either side of fair value'),
    ('mm.alpha', '0.3', 'Market maker: EMA weight of each new trade price'),
    ('mm.prior_pull', '0.1', 'Market maker: pull of fair value back towards the prior'),
    ('jobs.tick_seconds', '15', 'Seconds between price tick snapshots'),
    ('jobs.autoclose_seconds', '60', 'Seconds between checks for markets past their close time'),
    ('jobs.portfolio_seconds', '300', 'Seconds between portfolio equity snapshots'),
    ('content.brief_cooldown_seconds', '600', 'Minimum seconds between on-demand Gemini briefs per race'),
    ('content.scrape_minutes', '30', 'Minutes between Cherwell and Oxford Student feed scrapes'),
    ('users.starting_cash', '100000', 'Play money in pence given to each new account'),
    ('defaults.criteria.match', '"Resolves on the full-time result as confirmed by the competition organisers or Oxford University Sport. The winning outcome resolves YES, every other outcome NO."', 'Default resolution rules for Cuppers matches'),
    ('defaults.criteria.generic', '"Resolves by admin decision based on reporting in Cherwell or The Oxford Student, or on official University or Union announcements."', 'Default resolution rules for other markets'),
    ('defaults.priors.draw', '[40, 25, 35]', 'Default Home / Draw / Away opening odds'),
    ('defaults.priors.no_draw', '[50, 50]', 'Default Home / Away opening odds')
ON CONFLICT (key) DO NOTHING;

-- ---------------------------------------------------------------- admin audit log
-- Every admin action (create, edit, approve, close, resolve, settings) as a time series.

CREATE TABLE IF NOT EXISTS oxmarket.admin_actions (
    time       timestamptz NOT NULL DEFAULT now(),
    admin_id   int NULL,
    action     text NOT NULL,
    race_id    int NULL,
    market_id  int NULL,
    details    jsonb NULL
);
SELECT create_hypertable('oxmarket.admin_actions', 'time',
                         chunk_time_interval => INTERVAL '7 days', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS admin_actions_race_time_idx ON oxmarket.admin_actions (race_id, time DESC);
