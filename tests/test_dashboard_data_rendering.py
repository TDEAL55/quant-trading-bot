from monitoring_db import MonitoringDatabase
from monitoring_recorder import MonitoringRecorder


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
