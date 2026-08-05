CREATE TABLE IF NOT EXISTS quantum_score_runs (
    id INTEGER PRIMARY KEY,
    scanner_run_id INTEGER,
    score_version TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    symbol_count INTEGER NOT NULL DEFAULT 0,
    eligible_count INTEGER NOT NULL DEFAULT 0,
    selected_symbol TEXT,
    selected_strategy_id TEXT,
    selected_final_score REAL,
    configuration_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quantum_security_scores (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    rank INTEGER,
    is_selected INTEGER NOT NULL DEFAULT 0,
    strategy_eligibility INTEGER NOT NULL DEFAULT 0,
    final_score REAL NOT NULL,
    data_quality_status TEXT,
    score_timestamp TEXT,
    score_version TEXT,
    market_regime TEXT,
    risk_reward_ratio REAL,
    warnings_json TEXT,
    rejection_reasons_json TEXT,
    factor_values_json TEXT,
    weights_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quantum_component_contributions (
    id INTEGER PRIMARY KEY,
    security_score_id INTEGER NOT NULL,
    component_name TEXT NOT NULL,
    normalized_score REAL,
    weight REAL,
    weighted_contribution REAL,
    penalty_points REAL,
    warning TEXT
);

CREATE TABLE IF NOT EXISTS quantum_strategy_scores (
    id INTEGER PRIMARY KEY,
    security_score_id INTEGER NOT NULL,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT,
    strategy_score REAL,
    confidence REAL,
    eligible INTEGER NOT NULL DEFAULT 0,
    required_factors_json TEXT,
    rejection_reasons_json TEXT,
    warnings_json TEXT
);

CREATE TABLE IF NOT EXISTS quantum_score_rejections (
    id INTEGER PRIMARY KEY,
    security_score_id INTEGER NOT NULL,
    rejection_reason TEXT NOT NULL,
    source TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_quantum_score_runs_started_at ON quantum_score_runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_quantum_security_scores_run_id ON quantum_security_scores(run_id);
CREATE INDEX IF NOT EXISTS idx_quantum_security_scores_symbol ON quantum_security_scores(symbol);
CREATE INDEX IF NOT EXISTS idx_quantum_component_contrib_score_id ON quantum_component_contributions(security_score_id);
CREATE INDEX IF NOT EXISTS idx_quantum_strategy_scores_score_id ON quantum_strategy_scores(security_score_id);
CREATE INDEX IF NOT EXISTS idx_quantum_score_rejections_score_id ON quantum_score_rejections(security_score_id);
