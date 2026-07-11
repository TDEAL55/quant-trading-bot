from pathlib import Path

import pytest

import dashboard_app


def test_password_protection_helper():
    assert dashboard_app.check_dashboard_password("abc", "abc") is True
    assert dashboard_app.check_dashboard_password("abc", "def") is False
    assert dashboard_app.check_dashboard_password("", "def") is False


def test_paper_only_enforcement_blocks_live():
    with pytest.raises(RuntimeError, match="blocked in LIVE mode"):
        dashboard_app.enforce_paper_mode("LIVE")


def test_dashboard_code_has_no_write_capability():
    module_text = Path("dashboard_app.py").read_text(encoding="utf-8")
    assert dashboard_app.has_write_capability(module_text) is False


def test_dashboard_allows_empty_database_reads(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'monitoring.db'}"
    db = dashboard_app.MonitoringDatabase(database_url=db_url)
    db.ensure_schema()

    assert db.fetch_latest_bot_run() is None
    assert db.fetch_recent_runs(limit=5) == []
