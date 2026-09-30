from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest

from live_risk_policy import (
    LiveRiskSettings,
    evaluate_live_performance_controls,
    evaluate_live_readiness,
    live_entry_notional,
    settings_from_environment,
    stop_risk_position_size,
)


def _account(**overrides):
    account = {
        "status": "ACTIVE", "equity": 300, "last_equity": 300, "day_pl": 0,
        "cash": 300, "multiplier": 1, "trading_blocked": False, "account_blocked": False,
    }
    account.update(overrides)
    return account


def _armed():
    return LiveRiskSettings(enabled=True, order_submission_enabled=True, kill_switch=False,
        confirmation="ENABLE_LIVE_MICRO_TRADING", private_dashboard_confirmed=True,
        allowed_symbols=("F",))


def test_defaults_fail_closed():
    result = evaluate_live_readiness(_account(), {}, [], settings=LiveRiskSettings(), market_is_open=True, orders_submitted_today=0)
    assert not result["approved"]
    assert "live_kill_switch_active" in result["reasons"]
    assert "live_symbol_allowlist_empty" in result["reasons"]


def test_live_environment_uses_same_uncapped_entry_policy_as_paper():
    settings = settings_from_environment({})
    assert settings.entry_limits_enabled is False
    assert settings.entry_cash_allocation_percent == 25.0


def test_healthy_micro_account_is_approved_and_caps_entry_at_30():
    settings = _armed()
    result = evaluate_live_readiness(_account(), {}, [], settings=settings, market_is_open=True, orders_submitted_today=0)
    assert result["approved"]
    assert live_entry_notional(_account(), {}, settings) == 30.0


def test_disabled_entry_limits_reserve_cash_for_more_entries_and_ignore_entry_counts():
    settings = LiveRiskSettings(
        enabled=True,
        order_submission_enabled=True,
        kill_switch=False,
        confirmation="ENABLE_LIVE_MICRO_TRADING",
        private_dashboard_confirmed=True,
        entry_limits_enabled=False,
        allowed_symbols=("F",),
    )
    positions = {
        "F": {"market_value": 100},
        "SOFI": {"market_value": 100},
        "NU": {"market_value": 100},
    }
    result = evaluate_live_readiness(
        _account(),
        positions,
        [],
        settings=settings,
        market_is_open=True,
        orders_submitted_today=50,
    )
    assert result["approved"]
    assert live_entry_notional(_account(), positions, settings) == 75.0


def test_unlimited_entry_allocation_is_configurable_but_never_exceeds_cash():
    settings = settings_from_environment({"LIVE_ENTRY_CASH_ALLOCATION_PERCENT": "40"})
    assert live_entry_notional(_account(), {}, settings) == 120.0
    assert live_entry_notional(_account(cash=50), {}, settings) == 50.0


@pytest.mark.parametrize("account,reason", [
    (_account(day_pl=-3), "daily_loss_stop_active"),
    (_account(equity=501, cash=501), "account_equity_above_micro_launch_limit"),
    (_account(multiplier=4), "margin_or_negative_cash_not_allowed"),
])
def test_account_safety_blocks(account, reason):
    result = evaluate_live_readiness(account, {}, [], settings=_armed(), market_is_open=True, orders_submitted_today=0)
    assert reason in result["reasons"]


@pytest.mark.parametrize("key,value", [
    ("LIVE_MAX_POSITION_PERCENT", "11"), ("LIVE_MAX_GROSS_EXPOSURE_PERCENT", "31"),
    ("LIVE_MAX_OPEN_POSITIONS", "4"), ("LIVE_MAX_NEW_ORDERS_PER_DAY", "2"),
    ("LIVE_ENTRY_CASH_ALLOCATION_PERCENT", "101"),
    ("LIVE_DAILY_LOSS_STOP_DOLLARS", "4"),
])
def test_environment_cannot_raise_micro_caps(key, value):
    with pytest.raises(ValueError):
        settings_from_environment({key: value})


def test_stop_distance_sizing_uses_smaller_of_risk_and_notional_caps():
    result = stop_risk_position_size(
        equity=1000,
        entry_price=100,
        stop_price=95,
        maximum_notional=400,
        settings=_armed(),
    )
    assert result["allowed_risk_dollars"] == 5
    assert result["quantity"] == 1
    assert result["planned_risk_dollars"] == 5


def test_weekly_drawdown_and_consecutive_losses_block_new_entries():
    now = datetime(2026, 9, 30, 15, tzinfo=timezone.utc)
    trades = [
        {"exit_timestamp": (now - timedelta(minutes=20 * offset)).isoformat(), "realized_pnl": -4}
        for offset in (3, 2, 1)
    ]
    result = evaluate_live_performance_controls(
        _account(equity=300),
        trades,
        settings=_armed(),
        now=now,
    )
    assert not result["approved"]
    assert "weekly_drawdown_stop_active" in result["reasons"]
    assert "consecutive_loss_cooldown_active" in result["reasons"]


def test_missing_order_history_fails_closed():
    result = evaluate_live_performance_controls(
        _account(),
        [],
        settings=_armed(),
        now=datetime(2026, 9, 30, tzinfo=timezone.utc),
        history_available=False,
        history_complete=False,
    )
    assert result["reasons"] == ["live_order_history_unavailable", "live_order_history_incomplete"]


def test_weekly_peak_to_current_equity_drawdown_is_enforced():
    now = datetime(2026, 9, 30, 15, tzinfo=timezone.utc)
    result = evaluate_live_performance_controls(
        _account(equity=290),
        [],
        settings=_armed(),
        now=now,
        equity_history=[
            {"timestamp": (now - timedelta(days=1)).isoformat(), "equity": 300},
            {"timestamp": now.isoformat(), "equity": 290},
        ],
    )
    assert "weekly_drawdown_stop_active" in result["reasons"]
    assert result["weekly_peak_equity"] == 300
    assert result["weekly_drawdown_dollars"] == 10
