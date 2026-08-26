from datetime import datetime, timedelta, timezone

from live_readiness import run_live_readiness_check


def _env(tmp_path):
    return {
        "TRADING_MODE": "PAPER",
        "LIVE_READINESS_MODE": "true",
        "LIVE_READINESS_STATUS_PATH": str(tmp_path / "live-readiness.json"),
        "LIVE_READINESS_OBSERVATION_DAYS": "14",
        "ALPACA_PAPER_BASE_URL": "https://paper-api.alpaca.markets",
        "ALPACA_LIVE_BASE_URL": "https://api.alpaca.markets",
        "ALPACA_LIVE_API_KEY": "live-key",
        "ALPACA_LIVE_API_SECRET": "live-secret",
        "LIVE_TRADING_ENABLED": "false",
        "LIVE_ORDER_SUBMISSION_ENABLED": "false",
        "LIVE_ACTIVATION_TOKEN": "",
        "MAX_POSITION_EQUITY_PERCENT": "10",
        "LIVE_INITIAL_MAX_POSITION_EQUITY_PERCENT": "1",
        "LIVE_DAILY_LOSS_STOP_PERCENT": "2",
        "LIVE_MAX_DAILY_ORDERS": "5",
        "LIVE_MAX_OPEN_POSITIONS": "10",
        "LIVE_KILL_SWITCH_ENABLED": "true",
        "LIVE_DUPLICATE_ORDER_PROTECTION": "true",
        "LIVE_MAX_MARKET_DATA_AGE_SECONDS": "120",
        "NOTIFICATIONS_ENABLED": "true",
    }


def _matched():
    return {"status": "matched", "account_status": "ACTIVE", "warnings": [], "paper_only": True}


def test_readiness_starts_observing_and_never_enables_live(tmp_path):
    report = run_live_readiness_check(
        reconciliation_result=_matched(),
        environ=_env(tmp_path),
        now=datetime(2026, 8, 25, tzinfo=timezone.utc),
    )

    assert report["status"] == "OBSERVING"
    assert report["matched_observation_days"] == 1
    assert report["safe_to_enable_live"] is False
    assert report["live_orders_blocked"] is True
    assert "live-key" not in str(report)
    assert "live-secret" not in str(report)


def test_fourteen_clean_days_reaches_controlled_launch_readiness(tmp_path):
    env = _env(tmp_path)
    start = datetime(2026, 8, 25, tzinfo=timezone.utc)
    report = {}
    for offset in range(14):
        report = run_live_readiness_check(
            reconciliation_result=_matched(),
            environ=env,
            now=start + timedelta(days=offset),
        )

    assert report["status"] == "READY_FOR_CONTROLLED_LAUNCH"
    assert report["matched_observation_days"] == 14
    assert report["blockers"] == []
    assert report["safe_to_enable_live"] is False


def test_mismatch_and_unsafe_live_flags_create_blockers(tmp_path):
    env = _env(tmp_path)
    env["LIVE_TRADING_ENABLED"] = "true"
    env["LIVE_ORDER_SUBMISSION_ENABLED"] = "true"
    env["LIVE_INITIAL_MAX_POSITION_EQUITY_PERCENT"] = "4"

    report = run_live_readiness_check(
        reconciliation_result={"status": "mismatch", "account_status": "ACTIVE", "warnings": ["positions"]},
        environ=env,
        now=datetime(2026, 8, 25, tzinfo=timezone.utc),
    )

    failed_ids = {gate["id"] for gate in report["gates"] if not gate["pass"]}
    assert {"live_trading_blocked", "live_orders_blocked", "reconciliation", "initial_position_cap"} <= failed_ids
    assert report["live_orders_blocked"] is True


def test_same_day_updates_do_not_inflate_observation_count(tmp_path):
    env = _env(tmp_path)
    moment = datetime(2026, 8, 25, tzinfo=timezone.utc)
    run_live_readiness_check(reconciliation_result=_matched(), environ=env, now=moment)
    report = run_live_readiness_check(reconciliation_result=_matched(), environ=env, now=moment + timedelta(hours=1))

    assert report["observation_days_recorded"] == 1
    assert len(report["observations"]) == 1
