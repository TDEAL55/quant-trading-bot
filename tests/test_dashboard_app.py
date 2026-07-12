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


def test_bot_health_state_labels():
    assert dashboard_app.classify_bot_health({"bot_status": "healthy", "review_required": 0}) == ("Healthy", "healthy")
    assert dashboard_app.classify_bot_health({"bot_status": "warning", "review_required": 0}) == ("Warning", "warning")
    assert dashboard_app.classify_bot_health({"bot_status": "error", "review_required": 0}) == ("Error", "error")
    assert dashboard_app.classify_bot_health({"bot_status": "healthy", "review_required": 1}) == ("Error", "error")


def test_market_closed_and_signal_state_labels():
    assert dashboard_app.classify_market_status({"market_open": 0}) == ("Closed", "neutral")
    assert dashboard_app.classify_market_status({"market_open": 1}) == ("Open", "healthy")
    assert dashboard_app.classify_signal("BUY") == ("BUY", "buy")
    assert dashboard_app.classify_signal("HOLD") == ("HOLD", "hold")
    assert dashboard_app.classify_signal("SELL") == ("SELL", "sell")


def test_currency_formatting():
    assert dashboard_app.format_currency(1234.5) == "$1,234.50"
    assert dashboard_app.format_currency("bad", default="$0.00") == "$0.00"


def test_market_waiting_message_for_missing_values():
    assert dashboard_app.market_display_value(None, {"market_open": 0}) == "Waiting for market data"
    assert dashboard_app.market_display_value(None, {"market_open": 1}) == "Waiting for market data"


def test_empty_state_messages():
    messages = dashboard_app.empty_state_messages(recent_runs=[], signal_history=[], recent_orders=[], open_positions=0)
    assert "No monitoring records available yet" in messages
    assert "No paper orders yet" in messages
    assert "No signal history yet" in messages
    assert "No open positions" in messages
