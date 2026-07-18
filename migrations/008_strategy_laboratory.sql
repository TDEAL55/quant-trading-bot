CREATE TABLE IF NOT EXISTS strategy_definitions (
    id INTEGER PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    version TEXT NOT NULL,
    description TEXT,
    configuration_json TEXT,
    configuration_fingerprint TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (strategy_id, version)
);

CREATE TABLE IF NOT EXISTS strategy_comparison_runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    horizon INTEGER NOT NULL,
    benchmark TEXT NOT NULL,
    comparison_mode TEXT NOT NULL,
    start_date TEXT,
    end_date TEXT,
    strategy_ids_json TEXT,
    portfolio_configuration_json TEXT,
    transaction_cost_configuration_json TEXT,
    status TEXT NOT NULL DEFAULT 'completed',
    duration_seconds REAL NOT NULL DEFAULT 0,
    error_message TEXT,
    summary_json TEXT,
    performance_json TEXT
);

CREATE TABLE IF NOT EXISTS strategy_comparison_results (
    result_id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    eligible_candidate_count INTEGER NOT NULL DEFAULT 0,
    snapshot_count INTEGER NOT NULL DEFAULT 0,
    completed_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    analytics_json TEXT,
    scorecard_json TEXT,
    walk_forward_json TEXT,
    regime_json TEXT,
    factor_exposure_json TEXT,
    warnings_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (run_id, strategy_id),
    FOREIGN KEY (run_id) REFERENCES strategy_comparison_runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS strategy_pairwise_results (
    pair_id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    strategy_a_id TEXT NOT NULL,
    strategy_b_id TEXT NOT NULL,
    common_snapshot_count INTEGER NOT NULL DEFAULT 0,
    comparison_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (run_id, strategy_a_id, strategy_b_id),
    FOREIGN KEY (run_id) REFERENCES strategy_comparison_runs(run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_strategy_definitions_strategy_id ON strategy_definitions(strategy_id);
CREATE INDEX IF NOT EXISTS idx_strategy_runs_created_at ON strategy_comparison_runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_strategy_results_run_id ON strategy_comparison_results(run_id);
CREATE INDEX IF NOT EXISTS idx_strategy_pairwise_run_id ON strategy_pairwise_results(run_id);
