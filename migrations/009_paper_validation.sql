CREATE TABLE IF NOT EXISTS paper_strategy_approvals (
    approval_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    strategy_fingerprint TEXT NOT NULL,
    portfolio_configuration_json TEXT NOT NULL,
    risk_configuration_json TEXT NOT NULL,
    benchmark TEXT NOT NULL,
    horizon INTEGER NOT NULL,
    approved_by TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    notes TEXT,
    configuration_fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_approval_config_fingerprint ON paper_strategy_approvals(configuration_fingerprint);
CREATE INDEX IF NOT EXISTS idx_paper_approval_strategy ON paper_strategy_approvals(strategy_id, strategy_version);
CREATE INDEX IF NOT EXISTS idx_paper_approval_enabled_expires ON paper_strategy_approvals(enabled, expires_at);

CREATE TABLE IF NOT EXISTS paper_validation_runs (
    run_id TEXT PRIMARY KEY,
    run_fingerprint TEXT NOT NULL,
    execution_fingerprint TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    strategy_fingerprint TEXT NOT NULL,
    research_run_id TEXT,
    scanner_timestamp TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    dry_run INTEGER NOT NULL DEFAULT 1,
    proposed_order_count INTEGER NOT NULL DEFAULT 0,
    approved_order_count INTEGER NOT NULL DEFAULT 0,
    rejected_order_count INTEGER NOT NULL DEFAULT 0,
    submitted_order_count INTEGER NOT NULL DEFAULT 0,
    filled_order_count INTEGER NOT NULL DEFAULT 0,
    failed_order_count INTEGER NOT NULL DEFAULT 0,
    configuration_json TEXT,
    risk_snapshot_json TEXT,
    performance_json TEXT,
    warnings_json TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (approval_id) REFERENCES paper_strategy_approvals(approval_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_paper_validation_run_fingerprint ON paper_validation_runs(run_fingerprint);
CREATE INDEX IF NOT EXISTS idx_paper_validation_execution_fingerprint ON paper_validation_runs(execution_fingerprint);
CREATE INDEX IF NOT EXISTS idx_paper_validation_runs_approval ON paper_validation_runs(approval_id);
CREATE INDEX IF NOT EXISTS idx_paper_validation_runs_status ON paper_validation_runs(status);
CREATE INDEX IF NOT EXISTS idx_paper_validation_runs_started ON paper_validation_runs(started_at DESC);

CREATE TABLE IF NOT EXISTS paper_orders (
    paper_order_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    notional REAL NOT NULL,
    target_weight REAL,
    current_weight REAL,
    weight_delta REAL,
    reference_price REAL,
    proposed_at TEXT NOT NULL,
    risk_status TEXT NOT NULL,
    risk_reason TEXT,
    submission_status TEXT NOT NULL,
    broker_order_id TEXT,
    submitted_at TEXT,
    filled_quantity REAL,
    average_fill_price REAL,
    filled_at TEXT,
    canceled_at TEXT,
    failed_at TEXT,
    error_message TEXT,
    order_payload_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES paper_validation_runs(run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_paper_orders_run_id ON paper_orders(run_id);
CREATE INDEX IF NOT EXISTS idx_paper_orders_symbol ON paper_orders(symbol);
CREATE INDEX IF NOT EXISTS idx_paper_orders_submission_status ON paper_orders(submission_status);

CREATE TABLE IF NOT EXISTS paper_position_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    positions_json TEXT,
    cash REAL,
    buying_power REAL,
    portfolio_value REAL,
    gross_exposure REAL,
    net_exposure REAL,
    concentration_json TEXT,
    reconciliation_status TEXT,
    warnings_json TEXT,
    FOREIGN KEY (run_id) REFERENCES paper_validation_runs(run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_paper_position_snapshots_run_id ON paper_position_snapshots(run_id);
CREATE INDEX IF NOT EXISTS idx_paper_position_snapshots_captured_at ON paper_position_snapshots(captured_at DESC);
