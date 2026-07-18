CREATE TABLE IF NOT EXISTS research_runs (
    research_run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    scanner_version TEXT,
    strategy_version TEXT,
    benchmark_symbol TEXT,
    market_regime TEXT,
    universe_size INTEGER NOT NULL DEFAULT 0,
    scanned_count INTEGER NOT NULL DEFAULT 0,
    eligible_count INTEGER NOT NULL DEFAULT 0,
    rejected_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    average_overall_score REAL,
    average_confidence REAL,
    scanner_duration_seconds REAL NOT NULL DEFAULT 0,
    data_source TEXT,
    data_mode TEXT,
    scanner_config_json TEXT,
    factor_weights_json TEXT,
    scanner_summary_json TEXT,
    status TEXT NOT NULL DEFAULT 'completed'
);

CREATE TABLE IF NOT EXISTS research_candidates (
    id INTEGER PRIMARY KEY,
    research_run_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    company_name TEXT,
    rank INTEGER,
    overall_score REAL,
    confidence REAL,
    signal TEXT,
    market_regime TEXT,
    sector TEXT,
    industry TEXT,
    latest_price REAL,
    average_dollar_volume REAL,
    liquidity_score REAL,
    trend_score REAL,
    momentum_score REAL,
    volume_score REAL,
    volatility_score REAL,
    market_regime_score REAL,
    risk_quality_score REAL,
    rejection_status TEXT,
    rejection_reasons_json TEXT,
    strategy_reasons_json TEXT,
    factor_breakdown_json TEXT,
    ranking_score REAL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (research_run_id) REFERENCES research_runs(research_run_id) ON DELETE CASCADE,
    UNIQUE (research_run_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_research_runs_started_at ON research_runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_runs_status ON research_runs(status);
CREATE INDEX IF NOT EXISTS idx_research_candidates_run_id ON research_candidates(research_run_id);
CREATE INDEX IF NOT EXISTS idx_research_candidates_symbol ON research_candidates(symbol);
CREATE INDEX IF NOT EXISTS idx_research_candidates_rank ON research_candidates(research_run_id, rank);
CREATE INDEX IF NOT EXISTS idx_research_candidates_sector ON research_candidates(sector);
CREATE INDEX IF NOT EXISTS idx_research_candidates_regime ON research_candidates(market_regime);
CREATE INDEX IF NOT EXISTS idx_research_candidates_score ON research_candidates(overall_score DESC);
CREATE INDEX IF NOT EXISTS idx_research_candidates_confidence ON research_candidates(confidence DESC);
