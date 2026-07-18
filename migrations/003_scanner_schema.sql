CREATE TABLE IF NOT EXISTS scanner_runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    universe_name TEXT,
    symbol_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    rejection_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    eligible_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    duration_seconds REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS scanner_results (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    company_name TEXT,
    sector TEXT,
    latest_price REAL,
    average_dollar_volume REAL,
    overall_score REAL,
    confidence REAL,
    signal TEXT,
    regime TEXT,
    ranking_score REAL,
    rank INTEGER,
    eligible INTEGER NOT NULL DEFAULT 0,
    rejection_reasons_json TEXT,
    component_scores_json TEXT,
    reasons_json TEXT,
    warnings_json TEXT,
    data_quality_json TEXT
);

CREATE TABLE IF NOT EXISTS portfolio_candidates (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    rank INTEGER,
    sector TEXT,
    score REAL,
    confidence REAL,
    suggested_allocation_percent REAL,
    suggested_paper_notional REAL,
    selection_reasons_json TEXT,
    warnings_json TEXT
);

CREATE TABLE IF NOT EXISTS position_reviews (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    current_quantity REAL,
    current_entry_price REAL,
    current_market_price REAL,
    score REAL,
    confidence REAL,
    recommendation TEXT,
    reasons_json TEXT,
    warnings_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_scanner_runs_started_at ON scanner_runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_scanner_results_run_id ON scanner_results(run_id);
CREATE INDEX IF NOT EXISTS idx_scanner_results_rank ON scanner_results(run_id, rank);
CREATE INDEX IF NOT EXISTS idx_portfolio_candidates_run_id ON portfolio_candidates(run_id);
CREATE INDEX IF NOT EXISTS idx_position_reviews_run_id ON position_reviews(run_id);
