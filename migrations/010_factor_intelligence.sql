CREATE TABLE IF NOT EXISTS factor_definitions (
    factor_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    category TEXT NOT NULL,
    version TEXT NOT NULL,
    direction TEXT NOT NULL,
    calculation_source TEXT,
    lookback_period INTEGER,
    minimum_history_required INTEGER,
    expected_value_type TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (factor_id, version)
);

CREATE INDEX IF NOT EXISTS idx_factor_definitions_category_active ON factor_definitions(category, active);

CREATE TABLE IF NOT EXISTS factor_intelligence_runs (
    run_id TEXT PRIMARY KEY,
    run_fingerprint TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    analysis_start_date TEXT,
    analysis_end_date TEXT,
    forward_horizon INTEGER NOT NULL,
    universe_filter TEXT,
    regime_filter TEXT,
    factor_version_set TEXT,
    sample_count INTEGER NOT NULL DEFAULT 0,
    configuration_json TEXT,
    timings_json TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_factor_intelligence_unique_completed
    ON factor_intelligence_runs(run_fingerprint)
    WHERE status = 'completed';

CREATE INDEX IF NOT EXISTS idx_factor_intelligence_runs_started ON factor_intelligence_runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_factor_intelligence_runs_status ON factor_intelligence_runs(status);

CREATE TABLE IF NOT EXISTS factor_observations (
    observation_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL,
    candidate_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    factor_id TEXT NOT NULL,
    factor_version TEXT NOT NULL,
    observation_timestamp TEXT NOT NULL,
    factor_value REAL,
    normalized_value REAL,
    percentile_rank REAL,
    universe_size INTEGER,
    regime_label TEXT,
    data_freshness_timestamp TEXT,
    value_status TEXT NOT NULL,
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (snapshot_id, candidate_id, factor_id, factor_version),
    FOREIGN KEY (candidate_id) REFERENCES research_candidates(id) ON DELETE CASCADE,
    FOREIGN KEY (factor_id, factor_version) REFERENCES factor_definitions(factor_id, version) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_factor_observations_lookup
    ON factor_observations(factor_id, factor_version, observation_timestamp, symbol);
CREATE INDEX IF NOT EXISTS idx_factor_observations_snapshot ON factor_observations(snapshot_id);

CREATE TABLE IF NOT EXISTS factor_predictive_statistics (
    stat_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    factor_id TEXT NOT NULL,
    factor_version TEXT NOT NULL,
    forward_horizon INTEGER NOT NULL,
    sample_count INTEGER NOT NULL,
    valid_sample_count INTEGER NOT NULL,
    missing_count INTEGER NOT NULL,
    pearson_correlation REAL,
    spearman_correlation REAL,
    mean_forward_return REAL,
    median_forward_return REAL,
    top_bucket_return REAL,
    bottom_bucket_return REAL,
    top_minus_bottom_spread REAL,
    positive_return_rate REAL,
    mean_excess_return REAL,
    median_excess_return REAL,
    confidence_classification TEXT NOT NULL,
    status TEXT NOT NULL,
    analysis_start_date TEXT,
    analysis_end_date TEXT,
    warnings_json TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES factor_intelligence_runs(run_id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_factor_predictive_unique
    ON factor_predictive_statistics(run_id, factor_id, factor_version, forward_horizon);

CREATE TABLE IF NOT EXISTS factor_bucket_statistics (
    bucket_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    factor_id TEXT NOT NULL,
    factor_version TEXT NOT NULL,
    forward_horizon INTEGER NOT NULL,
    bucket_count INTEGER NOT NULL,
    bucket_number INTEGER NOT NULL,
    lower_bound REAL,
    upper_bound REAL,
    observation_count INTEGER NOT NULL,
    average_forward_return REAL,
    median_forward_return REAL,
    positive_return_rate REAL,
    average_excess_return REAL,
    return_volatility REAL,
    min_return REAL,
    max_return REAL,
    top_minus_bottom_spread REAL,
    monotonicity_score REAL,
    direction_consistency REAL,
    bucket_coverage REAL,
    status TEXT NOT NULL,
    warnings_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES factor_intelligence_runs(run_id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_factor_bucket_unique
    ON factor_bucket_statistics(run_id, factor_id, factor_version, forward_horizon, bucket_count, bucket_number);

CREATE TABLE IF NOT EXISTS factor_stability_results (
    stability_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    factor_id TEXT NOT NULL,
    factor_version TEXT NOT NULL,
    window_id TEXT,
    per_window INTEGER NOT NULL,
    training_start_date TEXT,
    training_end_date TEXT,
    validation_start_date TEXT,
    validation_end_date TEXT,
    window_sample_count INTEGER NOT NULL DEFAULT 0,
    window_correlation REAL,
    window_spread REAL,
    expected_direction_correct INTEGER,
    mean_window_score REAL,
    stddev_window_score REAL,
    min_window_score REAL,
    max_window_score REAL,
    degradation_score REAL,
    stability_score REAL,
    stability_classification TEXT NOT NULL,
    status TEXT NOT NULL,
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES factor_intelligence_runs(run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_factor_stability_lookup
    ON factor_stability_results(run_id, factor_id, factor_version, per_window, window_id);

CREATE TABLE IF NOT EXISTS factor_regime_statistics (
    regime_stat_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    factor_id TEXT NOT NULL,
    factor_version TEXT NOT NULL,
    regime_label TEXT NOT NULL,
    sample_count INTEGER NOT NULL,
    spearman_correlation REAL,
    top_minus_bottom_spread REAL,
    positive_return_rate REAL,
    average_return REAL,
    average_excess_return REAL,
    stability_score REAL,
    expected_direction_success_rate REAL,
    status TEXT NOT NULL,
    warnings_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES factor_intelligence_runs(run_id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_factor_regime_unique
    ON factor_regime_statistics(run_id, factor_id, factor_version, regime_label);

CREATE TABLE IF NOT EXISTS factor_redundancy_statistics (
    redundancy_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    factor_a_id TEXT NOT NULL,
    factor_a_version TEXT NOT NULL,
    factor_b_id TEXT NOT NULL,
    factor_b_version TEXT NOT NULL,
    aligned_sample_count INTEGER NOT NULL,
    pearson_correlation REAL,
    spearman_correlation REAL,
    absolute_correlation REAL,
    redundancy_classification TEXT NOT NULL,
    status TEXT NOT NULL,
    warnings_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES factor_intelligence_runs(run_id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_factor_redundancy_unique
    ON factor_redundancy_statistics(run_id, factor_a_id, factor_a_version, factor_b_id, factor_b_version);

CREATE TABLE IF NOT EXISTS factor_intelligence_scorecards (
    scorecard_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    factor_id TEXT NOT NULL,
    factor_version TEXT NOT NULL,
    predictive_score REAL,
    stability_score REAL,
    regime_score REAL,
    sample_quality_score REAL,
    redundancy_penalty REAL,
    overall_research_score REAL,
    confidence_classification TEXT NOT NULL,
    strongest_evidence_json TEXT,
    weakest_evidence_json TEXT,
    warnings_json TEXT,
    sample_count INTEGER NOT NULL,
    analysis_start_date TEXT,
    analysis_end_date TEXT,
    formula_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES factor_intelligence_runs(run_id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_factor_scorecard_unique
    ON factor_intelligence_scorecards(run_id, factor_id, factor_version);
