from datetime import datetime, timezone

import pytest

from pnl_risk_policy import (
    PnLRiskSettings,
    evaluate_account_pnl_policy,
    risk_adjusted_position_percent,
    settings_from_environment,
)


NOW = datetime(2026, 8, 27, 18, 0, tzinfo=timezone.utc)


def test_daily_loss_stop_uses_actual_account_day_pl_and_still_allows_exits():
    result = evaluate_account_pnl_policy(
        {"equity": 97_500, "last_equity": 100_000, "day_pl": -2_500},
        now=NOW,
    )

    assert result["status"] == "BLOCKED"
    assert result["reason"] == "daily_loss_stop"
    assert result["day_return_percent"] == -2.5
    assert result["block_new_entries"] is True
    assert result["exits_allowed"] is True


def test_three_recent_losses_trigger_one_hour_cooldown():
    trades = [
        {"exit_timestamp": "2026-08-27T17:55:00Z", "net_pnl": -10},
        {"exit_timestamp": "2026-08-27T17:45:00Z", "net_pnl": -5},
        {"exit_timestamp": "2026-08-27T17:30:00Z", "net_pnl": -2},
    ]
    result = evaluate_account_pnl_policy(
        {"equity": 100_000, "last_equity": 100_000, "day_pl": 0},
        closed_trades=trades,
        now=NOW,
    )

    assert result["reason"] == "consecutive_loss_cooldown"
    assert result["consecutive_losses"] == 3
    assert result["block_new_entries"] is True


def test_win_breaks_loss_streak_and_clear_account_remains_armed():
    trades = [
        {"exit_timestamp": "2026-08-27T17:55:00Z", "net_pnl": -10},
        {"exit_timestamp": "2026-08-27T17:45:00Z", "net_pnl": 1},
        {"exit_timestamp": "2026-08-27T17:30:00Z", "net_pnl": -2},
    ]
    result = evaluate_account_pnl_policy(
        {"equity": 100_500, "last_equity": 100_000},
        closed_trades=trades,
        now=NOW,
    )

    assert result["status"] == "ARMED"
    assert result["consecutive_losses"] == 1
    assert result["block_new_entries"] is False


def test_risk_adjusted_position_size_honors_one_percent_risk_and_ten_percent_cap():
    settings = PnLRiskSettings(maximum_position_percent=10, maximum_risk_per_trade_percent=1)
    assert risk_adjusted_position_percent(stop_loss_percent=4, settings=settings) == 10
    assert risk_adjusted_position_percent(stop_loss_percent=5, settings=settings) == 10
    assert risk_adjusted_position_percent(stop_loss_percent=25, settings=settings) == 4


def test_settings_reject_position_limits_above_ten_percent():
    with pytest.raises(ValueError):
        PnLRiskSettings(maximum_position_percent=10.1).validate()


def test_environment_settings_keep_daily_stop_active_in_aggressive_paper_mode(monkeypatch):
    monkeypatch.setenv("PAPER_AGGRESSIVE_TEST_MODE", "true")
    monkeypatch.setenv("PAPER_DAILY_LOSS_STOP_PERCENT", "2")
    assert settings_from_environment().daily_loss_stop_percent == 2
