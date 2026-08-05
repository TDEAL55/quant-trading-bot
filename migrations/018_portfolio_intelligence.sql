CREATE TABLE IF NOT EXISTS portfolio_allocation_runs (
    allocation_run_id TEXT PRIMARY KEY,
    source_scan_run_id TEXT,
    generated_at TEXT NOT NULL,
    account_equity REAL NOT NULL,
    available_cash REAL NOT NULL,
    investable_capital REAL NOT NULL,
    proposed_exposure REAL NOT NULL,
    cash_reserve REAL NOT NULL,
    selected_count INTEGER NOT NULL,
    rejected_count INTEGER NOT NULL,
    portfolio_risk_score REAL NOT NULL,
    diversification_score REAL NOT NULL,
    policy_version TEXT NOT NULL,
    review_required INTEGER NOT NULL DEFAULT 1,
    configuration_json TEXT NOT NULL,
    summary_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS portfolio_allocation_recommendations (
    recommendation_id TEXT PRIMARY KEY,
    allocation_run_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    rank INTEGER,
    strategy_id TEXT,
    strategy_version TEXT,
    quantum_score REAL,
    strategy_score REAL,
    target_allocation_pct REAL,
    target_notional REAL,
    proposed_quantity REAL,
    sector TEXT,
    confidence_tier TEXT,
    average_correlation REAL,
    maximum_correlation REAL,
    risk_reward_ratio REAL,
    selected INTEGER NOT NULL DEFAULT 0,
    rejection_reasons_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS portfolio_exposure_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    allocation_run_id TEXT NOT NULL,
    exposure_type TEXT NOT NULL,
    exposure_key TEXT NOT NULL,
    current_exposure_pct REAL NOT NULL,
    proposed_exposure_pct REAL NOT NULL,
    maximum_allowed_pct REAL NOT NULL,
    policy_passed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS portfolio_intelligence_reports (
    report_id TEXT PRIMARY KEY,
    allocation_run_id TEXT NOT NULL,
    report_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    review_required INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_portfolio_allocation_runs_generated_at
    ON portfolio_allocation_runs(generated_at DESC);
CREATE INDEX IF NOT EXISTS idx_portfolio_allocation_recos_run
    ON portfolio_allocation_recommendations(allocation_run_id, selected, rank);
CREATE INDEX IF NOT EXISTS idx_portfolio_exposure_snapshots_run
    ON portfolio_exposure_snapshots(allocation_run_id, exposure_type);
CREATE INDEX IF NOT EXISTS idx_portfolio_intelligence_reports_run
    ON portfolio_intelligence_reports(allocation_run_id, report_type, created_at DESC);
