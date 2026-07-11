from monitoring_recorder import MonitoringRecorder, mask_identifier


def test_mask_identifier_never_returns_full_identifier():
    masked = mask_identifier("paper-order-123456789")
    assert masked != "paper-order-123456789"
    assert "123456789" not in masked


def test_recorder_sanitizes_secret_fields(tmp_path):
    recorder = MonitoringRecorder(database_url=f"sqlite:///{tmp_path / 'monitoring.db'}")
    recorder.run_id = "run-abc"
    recorder.ensure_schema()

    recorder.record_order_event(
        {
            "market_date": "2026-07-11",
            "signal": "BUY",
            "submitted": False,
            "symbol": "SPY",
            "notional": 10.0,
            "safe_order_status": "skipped",
            "stop_reason": "account_number=123456789 authorization=Bearer secret-token",
            "safe_error_type": "RuntimeError",
            "safe_error_message": "ALPACA_API_KEY=abc123 token=xyz987",
            "order_id": "paper-order-123456789",
        }
    )

    row = recorder.db.query_one("SELECT * FROM sanitized_order_events WHERE run_id = ?", ("run-abc",))
    assert row is not None
    assert "123456789" not in str(row["stop_reason"])
    assert "abc123" not in str(row["safe_error_message"])
    assert "xyz987" not in str(row["safe_error_message"])
    assert row["order_id_masked"] != "paper-order-123456789"


def test_recorder_duplicate_run_finalize_is_safe(tmp_path):
    recorder = MonitoringRecorder(database_url=f"sqlite:///{tmp_path / 'monitoring.db'}")
    recorder.run_id = "run-dupe"
    recorder.ensure_schema()

    payload = {
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

    recorder.finalize_run(payload)
    recorder.finalize_run(payload)

    row = recorder.db.query_one("SELECT COUNT(*) AS n FROM bot_runs WHERE run_id = ?", ("run-dupe",))
    assert row["n"] == 1
