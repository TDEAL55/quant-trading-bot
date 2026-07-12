from pathlib import Path

import pytest

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


def test_account_snapshot_insert_without_explicit_id_generates_id(tmp_path):
    db = MonitoringDatabase(database_url=f"sqlite:///{tmp_path / 'monitoring.db'}")
    db.ensure_schema()

    db.insert_account_snapshot(
        {
            "run_id": "run-acc-id",
            "snapshot_timestamp": "2026-07-12T10:00:00+00:00",
            "account_status": "ACTIVE",
            "portfolio_value": 1000.0,
            "cash": 1000.0,
            "buying_power": 1000.0,
            "open_positions": 0,
            "unrealized_paper_pl": 0.0,
            "pending_orders": 0,
        }
    )

    row = db.query_one("SELECT id, run_id FROM paper_account_snapshots WHERE run_id = ?", ("run-acc-id",))
    assert row is not None
    assert isinstance(row["id"], int)
    assert row["id"] > 0


def test_failed_insert_is_rolled_back_and_later_insert_succeeds(tmp_path):
    db = MonitoringDatabase(database_url=f"sqlite:///{tmp_path / 'monitoring.db'}")
    db.ensure_schema()

    with pytest.raises(Exception):
        db.execute(
            "INSERT INTO paper_account_snapshots (run_id, snapshot_timestamp) VALUES (?, ?)",
            (None, None),
        )

    db.insert_account_snapshot(
        {
            "run_id": "run-after-failure",
            "snapshot_timestamp": "2026-07-12T10:05:00+00:00",
            "account_status": "ACTIVE",
            "portfolio_value": 1001.0,
            "cash": 1001.0,
            "buying_power": 1001.0,
            "open_positions": 0,
            "unrealized_paper_pl": 1.0,
            "pending_orders": 0,
        }
    )

    row = db.query_one("SELECT COUNT(*) AS n FROM paper_account_snapshots WHERE run_id = ?", ("run-after-failure",))
    assert row["n"] == 1


def test_migrations_are_idempotent_for_sqlite(tmp_path):
    db = MonitoringDatabase(database_url=f"sqlite:///{tmp_path / 'monitoring.db'}")
    db.ensure_schema()
    db.ensure_schema()

    db.insert_bot_run(
        {
            "run_id": "run-idempotent",
            "run_timestamp": "2026-07-12T11:00:00+00:00",
            "market_date": "2026-07-12",
            "trading_mode": "PAPER",
            "market_status": "open",
            "bot_status": "healthy",
            "review_required": False,
            "stop_reason": "completed",
            "safe_error_type": "",
            "safe_error_message": "",
            "submitted": False,
            "symbol": "SPY",
            "notional": 0.0,
            "safe_order_status": "skipped",
        }
    )

    row = db.query_one("SELECT COUNT(*) AS n FROM bot_runs WHERE run_id = ?", ("run-idempotent",))
    assert row["n"] == 1


def test_postgres_identity_migration_covers_all_tables():
    migration_text = Path("migrations/002_postgres_identity_fix.sql").read_text(encoding="utf-8")

    assert "CREATE SEQUENCE IF NOT EXISTS bot_runs_id_seq" in migration_text
    assert "CREATE SEQUENCE IF NOT EXISTS signal_snapshots_id_seq" in migration_text
    assert "CREATE SEQUENCE IF NOT EXISTS paper_account_snapshots_id_seq" in migration_text
    assert "CREATE SEQUENCE IF NOT EXISTS sanitized_order_events_id_seq" in migration_text
    assert "ALTER TABLE IF EXISTS bot_runs ALTER COLUMN id SET DEFAULT nextval('bot_runs_id_seq')" in migration_text
    assert "ALTER TABLE IF EXISTS signal_snapshots ALTER COLUMN id SET DEFAULT nextval('signal_snapshots_id_seq')" in migration_text
    assert "ALTER TABLE IF EXISTS paper_account_snapshots ALTER COLUMN id SET DEFAULT nextval('paper_account_snapshots_id_seq')" in migration_text
    assert "ALTER TABLE IF EXISTS sanitized_order_events ALTER COLUMN id SET DEFAULT nextval('sanitized_order_events_id_seq')" in migration_text
