CREATE TABLE IF NOT EXISTS paper_order_status_transitions (
    transition_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    paper_order_id TEXT,
    broker_order_id TEXT,
    client_order_id TEXT,
    symbol TEXT NOT NULL,
    previous_status TEXT,
    status TEXT NOT NULL,
    requested_quantity REAL,
    filled_quantity REAL,
    average_fill_price REAL,
    rejection_reason TEXT,
    event_time TEXT NOT NULL,
    execution_latency_seconds REAL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES paper_validation_runs(run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_order_transitions_run_id ON paper_order_status_transitions(run_id);
CREATE INDEX IF NOT EXISTS idx_order_transitions_order_id ON paper_order_status_transitions(broker_order_id);
CREATE INDEX IF NOT EXISTS idx_order_transitions_client_order_id ON paper_order_status_transitions(client_order_id);
CREATE INDEX IF NOT EXISTS idx_order_transitions_status ON paper_order_status_transitions(status);

CREATE TABLE IF NOT EXISTS strategy_closed_trades (
    trade_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    symbol TEXT NOT NULL,
    entry_timestamp TEXT NOT NULL,
    exit_timestamp TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    quantity REAL NOT NULL,
    realized_gross_pnl REAL NOT NULL,
    estimated_fees REAL NOT NULL,
    estimated_slippage REAL NOT NULL,
    net_pnl REAL NOT NULL,
    percentage_return REAL NOT NULL,
    holding_duration_hours REAL NOT NULL,
    max_adverse_excursion REAL NOT NULL,
    max_favorable_excursion REAL NOT NULL,
    exit_reason TEXT NOT NULL,
    market_regime TEXT NOT NULL,
    close_type TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_closed_trades_strategy ON strategy_closed_trades(strategy_id, strategy_version);
CREATE INDEX IF NOT EXISTS idx_closed_trades_symbol ON strategy_closed_trades(symbol);
CREATE INDEX IF NOT EXISTS idx_closed_trades_exit_ts ON strategy_closed_trades(exit_timestamp DESC);

CREATE TABLE IF NOT EXISTS strategy_leaderboard_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    captured_at TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    completed_trade_count INTEGER NOT NULL,
    net_profit REAL NOT NULL,
    profit_factor REAL NOT NULL,
    win_rate REAL NOT NULL,
    average_winner REAL NOT NULL,
    average_loser REAL NOT NULL,
    expectancy REAL NOT NULL,
    sharpe_ratio REAL NOT NULL,
    maximum_drawdown REAL NOT NULL,
    average_holding_time_hours REAL NOT NULL,
    sample_status TEXT NOT NULL,
    performance_by_regime_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_strategy_leaderboard_captured ON strategy_leaderboard_snapshots(captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_strategy_leaderboard_strategy ON strategy_leaderboard_snapshots(strategy_id, strategy_version);
