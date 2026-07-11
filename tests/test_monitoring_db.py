from pathlib import Path

from monitoring_db import MonitoringDatabase


def test_empty_database_queries_return_safe_defaults(tmp_path):
    db = MonitoringDatabase(database_url=f"sqlite:///{tmp_path / 'monitoring.db'}")
    db.ensure_schema()

    assert db.fetch_latest_bot_run() is None
    assert db.fetch_latest_signal_snapshot() is None
    assert db.fetch_latest_account_snapshot() is None
    assert db.fetch_recent_runs(limit=10) == []
    assert db.fetch_recent_order_events(limit=10) == []


def test_duplicate_run_id_is_ignored(tmp_path):
    db = MonitoringDatabase(database_url=f"sqlite:///{tmp_path / 'monitoring.db'}")
    db.ensure_schema()

    payload = {
        "run_id": "run-1",
        "run_timestamp": "2026-07-11T12:00:00+00:00",
        "market_date": "2026-07-11",
        "trading_mode": "PAPER",
        "market_status": "open",
        "bot_status": "healthy",
        "review_required": False,
        "stop_reason": "completed",
        "safe_error_type": "",
        "safe_error_message": "",
        "submitted": True,
        "symbol": "SPY",
        "notional": 10.0,
        "safe_order_status": "accepted",
    }
    db.insert_bot_run(payload)
    db.insert_bot_run(payload)

    row = db.query_one("SELECT COUNT(*) AS n FROM bot_runs WHERE run_id = ?", ("run-1",))
    assert row["n"] == 1


def test_retention_helper_sql_is_available(tmp_path):
    db = MonitoringDatabase(database_url=f"sqlite:///{tmp_path / 'monitoring.db'}")
    sql = db.retention_sql()
    assert "bot_runs" in sql
    assert "sanitized_order_events" in sql
