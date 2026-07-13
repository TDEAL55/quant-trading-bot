from monitoring_db import MonitoringDatabase
from monitoring_recorder import MonitoringRecorder
import dashboard_app


def test_dashboard_queries_work_with_sample_sanitized_records(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'monitoring.db'}"
    recorder = MonitoringRecorder(database_url=db_url)
    recorder.run_id = "run-sample"
    recorder.ensure_schema()

    recorder.record_signal_snapshot(
        {
            "market_date": "2026-07-11",
            "market_open": True,
            "latest_market_data_timestamp": "2026-07-11T13:30:00+00:00",
            "symbol": "SPY",
            "latest_price": 600.12,
            "short_moving_average": 599.50,
            "long_moving_average": 597.10,
            "generated_signal": "BUY",
            "trade_or_skip_reason": "submitted",
            "daily_submitted_order_count": 1,
            "max_daily_orders": 3,
            "daily_submitted_notional": 10.0,
            "max_daily_submitted_notional": 30.0,
            "cooldown_status": "inactive",
            "duplicate_signal_status": "clear",
            "pending_order_status": "clear",
            "daily_loss_stop_status": "clear",
        }
    )
    recorder.record_account_snapshot(
        {
            "account_status": "ACTIVE",
            "portfolio_value": 1005.0,
            "cash": 995.0,
            "buying_power": 1000.0,
            "open_positions": 1,
            "unrealized_paper_pl": 5.0,
            "pending_orders": 0,
        }
    )
    recorder.record_order_event(
        {
            "market_date": "2026-07-11",
            "signal": "BUY",
            "submitted": True,
            "symbol": "SPY",
            "notional": 10.0,
            "safe_order_status": "accepted",
            "stop_reason": "submitted",
            "review_required": False,
            "safe_error_type": "",
            "safe_error_message": "",
            "order_id": "paper-order-xyz987654",
        }
    )
    recorder.finalize_run(
        {
            "run_timestamp": "2026-07-11T13:31:00+00:00",
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
    )

    db = MonitoringDatabase(database_url=db_url)
    latest_run = db.fetch_latest_bot_run()
    latest_signal = db.fetch_latest_signal_snapshot()
    latest_account = db.fetch_latest_account_snapshot()
    orders = db.fetch_recent_order_events(limit=5)

    assert latest_run is not None
    assert latest_run["trading_mode"] == "PAPER"
    assert latest_signal is not None
    assert latest_signal["generated_signal"] == "BUY"
    assert latest_account is not None
    assert latest_account["account_status"] == "ACTIVE"
    assert len(orders) == 1
    assert "xyz987654" not in str(orders[0]["order_id_masked"])

    latest_success = db.fetch_latest_successful_run()
    payload = {
        "db_connected": True,
        "latest_run": latest_run,
        "latest_success": latest_success,
        "latest_signal": latest_signal,
        "latest_account": latest_account,
        "recent_runs": db.fetch_recent_runs(limit=20),
        "recent_orders": orders,
        "portfolio_history": list(reversed(db.fetch_portfolio_history(limit=50))),
        "signal_history": list(reversed(db.fetch_signal_history(limit=50))),
        "order_count_by_day": list(reversed(db.fetch_order_count_by_day(limit=50))),
    }
    view = dashboard_app.build_dashboard_view_model(payload)
    assert view["bot_health"]["label"] == "Healthy"
    assert view["market_status"]["label"] == "Open"
    assert view["generated_signal"] == "BUY"
    assert dashboard_app.format_currency(view["portfolio_value"]) == "$1,005.00"
    assert dashboard_app.format_currency(view["cash"]) == "$995.00"
    assert dashboard_app.format_currency(view["buying_power"]) == "$1,000.00"
    assert dashboard_app.format_currency(view["unrealized_paper_pl"]) == "$5.00"
    assert dashboard_app.format_currency(view["today_pl"]).startswith("$")
    assert dashboard_app.format_currency(view["total_pl"]).startswith("$")


def test_dashboard_view_model_handles_warning_error_and_market_closed_states():
    warning_payload = {
        "db_connected": True,
        "latest_run": {"bot_status": "warning", "review_required": 0, "trading_mode": "PAPER"},
        "latest_success": {},
        "latest_signal": {"market_open": 0, "generated_signal": "HOLD"},
        "latest_account": {},
        "recent_runs": [],
        "recent_orders": [],
        "portfolio_history": [],
        "signal_history": [],
        "order_count_by_day": [],
    }
    warning_view = dashboard_app.build_dashboard_view_model(warning_payload)
    assert warning_view["bot_health"]["label"] == "Warning"
    assert warning_view["market_status"]["label"] == "Closed"
    assert warning_view["generated_signal"] == "HOLD"
    assert warning_view["latest_spy_price"] == "Waiting for the next market-hours update"

    error_payload = {
        "db_connected": True,
        "latest_run": {"bot_status": "error", "review_required": 1, "trading_mode": "PAPER"},
        "latest_success": {},
        "latest_signal": {"market_open": 1, "generated_signal": "SELL"},
        "latest_account": {},
        "recent_runs": [],
        "recent_orders": [],
        "portfolio_history": [],
        "signal_history": [],
        "order_count_by_day": [],
    }
    error_view = dashboard_app.build_dashboard_view_model(error_payload)
    assert error_view["bot_health"]["label"] == "Error"
    assert error_view["generated_signal"] == "SELL"


def test_positive_and_negative_pl_formatting():
    history_payload = {
        "db_connected": True,
        "latest_run": {"bot_status": "healthy", "review_required": 0, "trading_mode": "PAPER"},
        "latest_success": {},
        "latest_signal": {"market_open": 1, "generated_signal": "BUY"},
        "latest_account": {"portfolio_value": 950.0, "cash": 950.0, "buying_power": 950.0, "unrealized_paper_pl": -50.0},
        "recent_runs": [],
        "recent_orders": [],
        "portfolio_history": [{"portfolio_value": 1000.0}, {"portfolio_value": 950.0}],
        "signal_history": [],
        "order_count_by_day": [],
    }
    view = dashboard_app.build_dashboard_view_model(history_payload)
    assert view["today_pl"] == -50.0
    assert view["total_pl"] == -50.0
    assert dashboard_app.format_currency(view["today_pl"]) == "$-50.00"
