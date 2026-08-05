CREATE TABLE IF NOT EXISTS trade_memory_records (
    trade_memory_id TEXT PRIMARY KEY,
    trade_id TEXT NOT NULL,
    run_id TEXT,
    broker_order_ids_json TEXT NOT NULL,
    client_order_ids_json TEXT NOT NULL,
    symbol TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    quantum_score_version TEXT NOT NULL,
    quantum_score_at_entry REAL NOT NULL,
    strategy_specific_score REAL NOT NULL,
    factor_values_json TEXT NOT NULL,
    component_scores_json TEXT NOT NULL,
    factor_weights_json TEXT NOT NULL,
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
    market_regime_entry TEXT NOT NULL,
    market_regime_exit TEXT NOT NULL,
    benchmark_return_during_trade REAL NOT NULL,
    sector TEXT NOT NULL,
    industry TEXT NOT NULL,
    entry_reason TEXT NOT NULL,
    exit_reason TEXT NOT NULL,
    stop_level REAL,
    target_level REAL,
    confidence REAL NOT NULL,
    data_quality_status TEXT NOT NULL,
    execution_mode TEXT NOT NULL,
    completed_only INTEGER NOT NULL DEFAULT 1,
    source_order_status TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(trade_id, execution_mode)
);

CREATE TABLE IF NOT EXISTS strategy_leaderboard_versions (
    version_id TEXT PRIMARY KEY,
    captured_at TEXT NOT NULL,
    leaderboard_version TEXT NOT NULL,
    sample_minimum INTEGER NOT NULL,
    source_trade_count INTEGER NOT NULL,
    configuration_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_leaderboard_records (
    id INTEGER PRIMARY KEY,
    version_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    completed_trade_count INTEGER NOT NULL,
    win_rate REAL NOT NULL,
    loss_rate REAL NOT NULL,
    average_return REAL NOT NULL,
    median_return REAL NOT NULL,
    gross_profit REAL NOT NULL,
    gross_loss REAL NOT NULL,
    net_profit REAL NOT NULL,
    profit_factor REAL NOT NULL,
    expectancy REAL NOT NULL,
    sharpe_ratio REAL NOT NULL,
    sortino_ratio REAL NOT NULL,
    maximum_drawdown REAL NOT NULL,
    average_winner REAL NOT NULL,
    average_loser REAL NOT NULL,
    payoff_ratio REAL NOT NULL,
    average_holding_hours REAL NOT NULL,
    best_trade REAL NOT NULL,
    worst_trade REAL NOT NULL,
    consecutive_wins INTEGER NOT NULL,
    consecutive_losses INTEGER NOT NULL,
    recent20_json TEXT NOT NULL,
    recent60_json TEXT NOT NULL,
    full_history_json TEXT NOT NULL,
    by_regime_json TEXT NOT NULL,
    by_sector_json TEXT NOT NULL,
    by_score_bucket_json TEXT NOT NULL,
    sample_status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_regime_calculations (
    regime_calc_id TEXT PRIMARY KEY,
    regime_id TEXT NOT NULL,
    regime_version TEXT NOT NULL,
    inputs_json TEXT NOT NULL,
    regime_score REAL NOT NULL,
    regime_confidence REAL NOT NULL,
    warnings_json TEXT NOT NULL,
    calculated_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_regime_metrics (
    id INTEGER PRIMARY KEY,
    regime_calc_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    regime_id TEXT NOT NULL,
    sample_count INTEGER NOT NULL,
    win_rate REAL NOT NULL,
    expectancy REAL NOT NULL,
    drawdown REAL NOT NULL,
    recent_degradation REAL NOT NULL,
    compatibility_score REAL NOT NULL,
    pause_recommended INTEGER NOT NULL DEFAULT 0,
    reasons_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS factor_effectiveness_metrics (
    id INTEGER PRIMARY KEY,
    analysis_version TEXT NOT NULL,
    factor_name TEXT NOT NULL,
    factor_bucket TEXT NOT NULL,
    strategy_id TEXT,
    regime_id TEXT,
    sample_count INTEGER NOT NULL,
    win_rate REAL NOT NULL,
    average_return REAL NOT NULL,
    median_return REAL NOT NULL,
    expectancy REAL NOT NULL,
    profit_factor REAL NOT NULL,
    drawdown_contribution REAL NOT NULL,
    forward_return_correlation REAL NOT NULL,
    stability_score REAL NOT NULL,
    predictive_status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS allocation_recommendations (
    recommendation_id TEXT PRIMARY KEY,
    recommendation_version TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    symbol TEXT,
    quantum_score REAL NOT NULL,
    strategy_score REAL NOT NULL,
    historical_expectancy REAL NOT NULL,
    profit_factor REAL NOT NULL,
    drawdown REAL NOT NULL,
    sample_size INTEGER NOT NULL,
    market_regime TEXT NOT NULL,
    portfolio_concentration REAL NOT NULL,
    stability_score REAL NOT NULL,
    recommended_allocation_pct REAL NOT NULL,
    recommended_risk_pct REAL NOT NULL,
    confidence_tier TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL,
    max_allowed_allocation_pct REAL NOT NULL,
    policy_passed INTEGER NOT NULL,
    review_required INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_state_recommendations (
    recommendation_id TEXT PRIMARY KEY,
    recommendation_version TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    current_state TEXT NOT NULL,
    proposed_state TEXT NOT NULL,
    sample_size INTEGER NOT NULL,
    net_expectancy REAL NOT NULL,
    profit_factor REAL NOT NULL,
    sharpe_ratio REAL NOT NULL,
    drawdown REAL NOT NULL,
    recent_degradation REAL NOT NULL,
    regime_specific_result REAL NOT NULL,
    stability_score REAL NOT NULL,
    automation_allowed INTEGER NOT NULL DEFAULT 0,
    review_required INTEGER NOT NULL DEFAULT 1,
    reasons_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS weight_change_recommendations (
    recommendation_id TEXT PRIMARY KEY,
    recommendation_version TEXT NOT NULL,
    factor_name TEXT NOT NULL,
    current_weight REAL NOT NULL,
    proposed_weight REAL NOT NULL,
    evidence_json TEXT NOT NULL,
    sample_size INTEGER NOT NULL,
    expected_benefit REAL NOT NULL,
    risk_score REAL NOT NULL,
    confidence REAL NOT NULL,
    rollback_plan TEXT NOT NULL,
    walk_forward_passed INTEGER NOT NULL,
    out_of_sample_passed INTEGER NOT NULL,
    review_required INTEGER NOT NULL DEFAULT 1,
    rejected_reason TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS intelligence_report_snapshots (
    report_id TEXT PRIMARY KEY,
    report_type TEXT NOT NULL,
    report_version TEXT NOT NULL,
    report_period_start TEXT,
    report_period_end TEXT,
    payload_json TEXT NOT NULL,
    unresolved_data_quality_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS intelligence_model_versions (
    model_version_id TEXT PRIMARY KEY,
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    configuration_json TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    review_only INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trade_memory_strategy ON trade_memory_records(strategy_id, strategy_version);
CREATE INDEX IF NOT EXISTS idx_trade_memory_exit_ts ON trade_memory_records(exit_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_trade_memory_symbol ON trade_memory_records(symbol);
CREATE INDEX IF NOT EXISTS idx_leaderboard_records_version ON strategy_leaderboard_records(version_id);
CREATE INDEX IF NOT EXISTS idx_regime_calc_ts ON market_regime_calculations(calculated_at DESC);
CREATE INDEX IF NOT EXISTS idx_strategy_regime_metrics_calc ON strategy_regime_metrics(regime_calc_id);
CREATE INDEX IF NOT EXISTS idx_factor_effectiveness_factor ON factor_effectiveness_metrics(factor_name, factor_bucket);
CREATE INDEX IF NOT EXISTS idx_allocation_reco_strategy ON allocation_recommendations(strategy_id, strategy_version, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_state_reco_strategy ON strategy_state_recommendations(strategy_id, strategy_version, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_weight_reco_factor ON weight_change_recommendations(factor_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_report_snapshots_type ON intelligence_report_snapshots(report_type, created_at DESC);
