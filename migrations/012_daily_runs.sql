CREATE TABLE IF NOT EXISTS daily_runs (
    run_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    market_session TEXT,
    market_status TEXT,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    qualified_count INTEGER NOT NULL DEFAULT 0,
    selected_symbols_json TEXT,
    execution_status TEXT NOT NULL,
    performance_run_id TEXT,
    paper_validation_run_id TEXT,
    report_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_daily_runs_timestamp ON daily_runs(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_daily_runs_execution_status ON daily_runs(execution_status);
