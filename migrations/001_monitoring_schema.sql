CREATE TABLE IF NOT EXISTS bot_runs (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    run_timestamp TEXT NOT NULL,
    market_date TEXT,
    trading_mode TEXT NOT NULL,
    market_status TEXT,
    bot_status TEXT,
    review_required INTEGER NOT NULL DEFAULT 0,
    stop_reason TEXT,
    safe_error_type TEXT,
    safe_error_message TEXT,
    submitted INTEGER,
    symbol TEXT,
    notional REAL,
    safe_order_status TEXT
);

CREATE TABLE IF NOT EXISTS signal_snapshots (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    snapshot_timestamp TEXT NOT NULL,
    market_date TEXT,
    market_open INTEGER,
    latest_market_data_timestamp TEXT,
    symbol TEXT,
    latest_price REAL,
    short_moving_average REAL,
    long_moving_average REAL,
    generated_signal TEXT,
    trade_or_skip_reason TEXT,
    daily_submitted_order_count INTEGER,
    max_daily_orders INTEGER,
    daily_submitted_notional REAL,
    max_daily_submitted_notional REAL,
    cooldown_status TEXT,
    duplicate_signal_status TEXT,
    pending_order_status TEXT,
    daily_loss_stop_status TEXT
);

CREATE TABLE IF NOT EXISTS paper_account_snapshots (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    snapshot_timestamp TEXT NOT NULL,
    account_status TEXT,
    portfolio_value REAL,
    cash REAL,
    buying_power REAL,
    open_positions INTEGER,
    unrealized_paper_pl REAL,
    pending_orders INTEGER
);

CREATE TABLE IF NOT EXISTS sanitized_order_events (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    event_timestamp TEXT NOT NULL,
    market_date TEXT,
    signal TEXT,
    submitted INTEGER,
    symbol TEXT,
    notional REAL,
    safe_order_status TEXT,
    stop_reason TEXT,
    review_required INTEGER,
    safe_error_type TEXT,
    safe_error_message TEXT,
    order_id_masked TEXT
);

CREATE INDEX IF NOT EXISTS idx_bot_runs_run_timestamp ON bot_runs(run_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_signal_snapshots_run_id ON signal_snapshots(run_id);
CREATE INDEX IF NOT EXISTS idx_signal_snapshots_market_date ON signal_snapshots(market_date DESC);
CREATE INDEX IF NOT EXISTS idx_paper_account_snapshots_run_id ON paper_account_snapshots(run_id);
CREATE INDEX IF NOT EXISTS idx_paper_account_snapshots_timestamp ON paper_account_snapshots(snapshot_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_sanitized_order_events_run_id ON sanitized_order_events(run_id);
CREATE INDEX IF NOT EXISTS idx_sanitized_order_events_timestamp ON sanitized_order_events(event_timestamp DESC);
