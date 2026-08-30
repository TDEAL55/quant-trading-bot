CREATE TABLE IF NOT EXISTS paper_entry_reservations (
    reservation_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    notional REAL NOT NULL,
    reference_price REAL NOT NULL,
    portfolio_equity REAL NOT NULL,
    allowed_position_percent REAL NOT NULL,
    status TEXT NOT NULL,
    outcome TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    released_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_entry_one_active_symbol
    ON paper_entry_reservations(symbol)
    WHERE status = 'ACTIVE';
CREATE INDEX IF NOT EXISTS idx_paper_entry_reservation_status_expiry
    ON paper_entry_reservations(status, expires_at);
CREATE INDEX IF NOT EXISTS idx_paper_entry_reservation_run
    ON paper_entry_reservations(run_id);
