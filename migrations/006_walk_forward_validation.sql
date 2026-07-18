CREATE TABLE IF NOT EXISTS walk_forward_runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    window_type TEXT NOT NULL,
    training_periods INTEGER NOT NULL,
    validation_periods INTEGER NOT NULL,
    step_periods INTEGER NOT NULL,
    horizon INTEGER NOT NULL,
    benchmark_symbol TEXT NOT NULL,
    configuration_snapshot_json TEXT,
    total_windows INTEGER NOT NULL DEFAULT 0,
    completed_windows INTEGER NOT NULL DEFAULT 0,
    skipped_windows INTEGER NOT NULL DEFAULT 0,
    scorecard_json TEXT,
    factor_stability_summary_json TEXT,
    performance_decay_json TEXT,
    regime_robustness_json TEXT,
    performance_json TEXT,
    status TEXT NOT NULL DEFAULT 'completed',
    duration_seconds REAL NOT NULL DEFAULT 0,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS walk_forward_windows (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    window_id TEXT NOT NULL,
    training_start_date TEXT NOT NULL,
    training_end_date TEXT NOT NULL,
    validation_start_date TEXT NOT NULL,
    validation_end_date TEXT NOT NULL,
    training_observation_count INTEGER NOT NULL DEFAULT 0,
    validation_observation_count INTEGER NOT NULL DEFAULT 0,
    horizon INTEGER NOT NULL,
    benchmark_symbol TEXT NOT NULL,
    window_type TEXT NOT NULL,
    training_metrics_json TEXT,
    validation_metrics_json TEXT,
    degradation_metrics_json TEXT,
    factor_stability_json TEXT,
    regime_metrics_json TEXT,
    warnings_json TEXT,
    status TEXT NOT NULL DEFAULT 'completed',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (run_id, window_id),
    FOREIGN KEY (run_id) REFERENCES walk_forward_runs(run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_walk_forward_runs_created_at ON walk_forward_runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_walk_forward_runs_status ON walk_forward_runs(status);
CREATE INDEX IF NOT EXISTS idx_walk_forward_windows_run_id ON walk_forward_windows(run_id);
CREATE INDEX IF NOT EXISTS idx_walk_forward_windows_validation_start ON walk_forward_windows(validation_start_date);
CREATE INDEX IF NOT EXISTS idx_walk_forward_windows_status ON walk_forward_windows(status);