from __future__ import annotations

import pytest

from live_risk_policy import LiveRiskSettings, evaluate_live_readiness, live_entry_notional, settings_from_environment


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


def test_healthy_micro_account_is_approved_and_caps_entry_at_30():
    settings = _armed()
    result = evaluate_live_readiness(_account(), {}, [], settings=settings, market_is_open=True, orders_submitted_today=0)
    assert result["approved"]
    assert live_entry_notional(_account(), {}, settings) == 30.0


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
    ("LIVE_DAILY_LOSS_STOP_DOLLARS", "4"),
])
def test_environment_cannot_raise_micro_caps(key, value):
    with pytest.raises(ValueError):
        settings_from_environment({key: value})
