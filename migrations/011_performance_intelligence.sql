CREATE TABLE IF NOT EXISTS performance_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    source_run_count INTEGER NOT NULL DEFAULT 0,
    source_trade_count INTEGER NOT NULL DEFAULT 0,
    analysis_start_date TEXT,
    analysis_end_date TEXT,
    benchmark_symbol TEXT NOT NULL DEFAULT 'SPY',
    configuration_json TEXT,
    warnings_json TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_performance_runs_status ON performance_runs(status);
CREATE INDEX IF NOT EXISTS idx_performance_runs_completed ON performance_runs(completed_at DESC);

CREATE TABLE IF NOT EXISTS daily_equity (
    daily_equity_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    equity_date TEXT NOT NULL,
    portfolio_value REAL NOT NULL,
    cash REAL NOT NULL,
    buying_power REAL NOT NULL,
    daily_return REAL,
    total_return REAL,
    cumulative_return REAL,
    max_drawdown REAL,
    current_drawdown REAL,
    volatility REAL,
    turnover REAL,
    exposure_pct REAL,
    position_concentration REAL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES performance_runs(run_id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_equity_run_date ON daily_equity(run_id, equity_date);
CREATE INDEX IF NOT EXISTS idx_daily_equity_date ON daily_equity(equity_date DESC);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    source_validation_run_id TEXT,
    captured_at TEXT NOT NULL,
    portfolio_value REAL NOT NULL,
    cash REAL NOT NULL,
    buying_power REAL NOT NULL,
    exposure_pct REAL,
    position_concentration REAL,
    sector_allocation_json TEXT,
    positions_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES performance_runs(run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_run ON portfolio_snapshots(run_id, captured_at DESC);

CREATE TABLE IF NOT EXISTS trade_statistics (
    trade_stat_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    source_validation_run_id TEXT,
    trade_date TEXT,
    trade_count INTEGER NOT NULL DEFAULT 0,
    win_rate REAL,
    loss_rate REAL,
    average_winner REAL,
    average_loser REAL,
    profit_factor REAL,
    largest_winner REAL,
    largest_loser REAL,
    average_hold_time_days REAL,
    turnover REAL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES performance_runs(run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_trade_statistics_run ON trade_statistics(run_id);
CREATE INDEX IF NOT EXISTS idx_trade_statistics_date ON trade_statistics(trade_date DESC);

CREATE TABLE IF NOT EXISTS performance_metrics (
    metric_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    metric_group TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_value REAL,
    as_of_date TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES performance_runs(run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_performance_metrics_run ON performance_metrics(run_id);
CREATE INDEX IF NOT EXISTS idx_performance_metrics_group ON performance_metrics(metric_group, metric_name);
