CREATE TABLE IF NOT EXISTS strategy_evaluations (
    id INTEGER PRIMARY KEY,
    research_candidate_id INTEGER NOT NULL UNIQUE,
    research_run_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    observation_date TEXT NOT NULL,
    observation_price REAL,
    benchmark_symbol TEXT NOT NULL,
    benchmark_observation_price REAL,
    forward_1d_target_date TEXT,
    forward_1d_actual_date TEXT,
    forward_1d_future_price REAL,
    forward_1d_benchmark_future_price REAL,
    forward_1d_return REAL,
    forward_1d_benchmark_return REAL,
    forward_1d_excess_return REAL,
    forward_1d_status TEXT NOT NULL DEFAULT 'pending',
    forward_5d_target_date TEXT,
    forward_5d_actual_date TEXT,
    forward_5d_future_price REAL,
    forward_5d_benchmark_future_price REAL,
    forward_5d_return REAL,
    forward_5d_benchmark_return REAL,
    forward_5d_excess_return REAL,
    forward_5d_status TEXT NOT NULL DEFAULT 'pending',
    forward_10d_target_date TEXT,
    forward_10d_actual_date TEXT,
    forward_10d_future_price REAL,
    forward_10d_benchmark_future_price REAL,
    forward_10d_return REAL,
    forward_10d_benchmark_return REAL,
    forward_10d_excess_return REAL,
    forward_10d_status TEXT NOT NULL DEFAULT 'pending',
    forward_20d_target_date TEXT,
    forward_20d_actual_date TEXT,
    forward_20d_future_price REAL,
    forward_20d_benchmark_future_price REAL,
    forward_20d_return REAL,
    forward_20d_benchmark_return REAL,
    forward_20d_excess_return REAL,
    forward_20d_status TEXT NOT NULL DEFAULT 'pending',
    label_status TEXT NOT NULL DEFAULT 'pending',
    data_source TEXT,
    last_attempted_at TEXT,
    completed_at TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (research_candidate_id) REFERENCES research_candidates(id) ON DELETE CASCADE,
    FOREIGN KEY (research_run_id) REFERENCES research_runs(research_run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_strategy_evaluations_research_run_id ON strategy_evaluations(research_run_id);
CREATE INDEX IF NOT EXISTS idx_strategy_evaluations_symbol ON strategy_evaluations(symbol);
CREATE INDEX IF NOT EXISTS idx_strategy_evaluations_observation_date ON strategy_evaluations(observation_date);
CREATE INDEX IF NOT EXISTS idx_strategy_evaluations_label_status ON strategy_evaluations(label_status);
CREATE INDEX IF NOT EXISTS idx_strategy_evaluations_completed_at ON strategy_evaluations(completed_at);
CREATE INDEX IF NOT EXISTS idx_strategy_evaluations_forward_1d_excess_return ON strategy_evaluations(forward_1d_excess_return);
CREATE INDEX IF NOT EXISTS idx_strategy_evaluations_forward_5d_excess_return ON strategy_evaluations(forward_5d_excess_return);
CREATE INDEX IF NOT EXISTS idx_strategy_evaluations_forward_10d_excess_return ON strategy_evaluations(forward_10d_excess_return);
CREATE INDEX IF NOT EXISTS idx_strategy_evaluations_forward_20d_excess_return ON strategy_evaluations(forward_20d_excess_return);