CREATE TABLE IF NOT EXISTS notification_history (
    notification_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    deduplication_key TEXT NOT NULL,
    delivery_status TEXT NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    provider TEXT NOT NULL,
    safe_error_message TEXT,
    related_run_id TEXT,
    symbol TEXT,
    strategy_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_notification_history_event_time
    ON notification_history(event_type, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_notification_history_dedup
    ON notification_history(deduplication_key, provider, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_notification_history_status
    ON notification_history(delivery_status, timestamp DESC);
