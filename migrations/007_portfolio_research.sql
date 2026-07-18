CREATE TABLE IF NOT EXISTS portfolio_research_runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    horizon INTEGER NOT NULL,
    weighting_method TEXT NOT NULL,
    top_n INTEGER,
    maximum_position_weight REAL,
    sector_cap REAL,
    target_volatility REAL,
    benchmark TEXT NOT NULL,
    start_date TEXT,
    end_date TEXT,
    configuration_json TEXT,
    portfolio_count INTEGER NOT NULL DEFAULT 0,
    completed_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'completed',
    duration_seconds REAL NOT NULL DEFAULT 0,
    error_message TEXT,
    performance_json TEXT,
    analytics_json TEXT,
    method_comparison_json TEXT,
    walk_forward_json TEXT,
    warnings_json TEXT
);

CREATE TABLE IF NOT EXISTS portfolio_research_snapshots (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    research_run_id TEXT NOT NULL,
    formation_date TEXT NOT NULL,
    horizon INTEGER NOT NULL,
    weighting_method TEXT NOT NULL,
    holding_count INTEGER NOT NULL DEFAULT 0,
    invested_weight REAL NOT NULL DEFAULT 0,
    cash_weight REAL NOT NULL DEFAULT 0,
    portfolio_return REAL,
    benchmark_return REAL,
    excess_return REAL,
    turnover REAL,
    concentration_metrics_json TEXT,
    sector_exposure_json TEXT,
    holdings_json TEXT,
    symbol_contribution_json TEXT,
    sector_contribution_json TEXT,
    signal_contribution_json TEXT,
    regime_contribution_json TEXT,
    warnings_json TEXT,
    status TEXT NOT NULL DEFAULT 'completed',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (run_id, snapshot_id),
    FOREIGN KEY (run_id) REFERENCES portfolio_research_runs(run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_portfolio_research_runs_created_at ON portfolio_research_runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_portfolio_research_runs_method ON portfolio_research_runs(weighting_method);
CREATE INDEX IF NOT EXISTS idx_portfolio_research_runs_horizon ON portfolio_research_runs(horizon);
CREATE INDEX IF NOT EXISTS idx_portfolio_research_snapshots_run_id ON portfolio_research_snapshots(run_id);
CREATE INDEX IF NOT EXISTS idx_portfolio_research_snapshots_formation_date ON portfolio_research_snapshots(formation_date);
CREATE INDEX IF NOT EXISTS idx_portfolio_research_snapshots_status ON portfolio_research_snapshots(status);
