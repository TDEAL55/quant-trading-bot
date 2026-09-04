from __future__ import annotations

from types import SimpleNamespace

import pytest

import controlled_micro_paper_runner as runner
from alpaca_micro_paper_broker import AlpacaMicroPaperBroker


class Client:
    def get_account(self):
        return SimpleNamespace(status="ACTIVE", buying_power="300", non_marginable_buying_power="300",
            cash="300", equity="300", last_equity="300", portfolio_value="300", multiplier="1",
            trading_blocked=False, account_blocked=False, account_number="paper-secret", currency="USD")
    def get_all_positions(self): return []
    def get_orders(self, filter=None): return []
    def get_clock(self): return SimpleNamespace(is_open=False, timestamp="", next_open="", next_close="")


def env(**updates):
    values = {"TRADING_MODE":"PAPER", "ALPACA_API_KEY":"paper-key", "ALPACA_API_SECRET":"paper-secret",
        "ALPACA_PAPER_BASE_URL":"https://paper-api.alpaca.markets", "PAPER_MICRO_TRIAL_ENABLED":"true",
        "ALPACA_ORDER_SUBMISSION_ENABLED":"true", "PAPER_MICRO_TRIAL_CONFIRMATION":"ENABLE_PAPER_MICRO_TRIAL",
        "PAPER_MICRO_KILL_SWITCH":"false", "PAPER_MICRO_ALLOWED_SYMBOLS":"F"}
    values.update(updates)
    return values


def test_paper_micro_broker_is_paper_only_and_fail_closed():
    with pytest.raises(RuntimeError, match="TRADING_MODE=PAPER"):
        AlpacaMicroPaperBroker(trading_client=Client(), environ=env(TRADING_MODE="LIVE"))
    with pytest.raises(RuntimeError, match="PAPER_MICRO_TRIAL_ENABLED"):
        AlpacaMicroPaperBroker(trading_client=Client(), environ=env(PAPER_MICRO_TRIAL_ENABLED="false"))


def test_read_only_paper_account_check_requires_no_arm_flags(monkeypatch):
    monkeypatch.setattr(runner, "AlpacaMicroPaperBroker",
        lambda **kwargs: AlpacaMicroPaperBroker(trading_client=Client(), **kwargs))
    result = runner.check_paper_account(env(PAPER_MICRO_TRIAL_ENABLED="false",
        ALPACA_ORDER_SUBMISSION_ENABLED="false", PAPER_MICRO_TRIAL_CONFIRMATION=""))
    assert result["starting_equity_matches_300"]
    assert not result["submission_enabled"]
    assert "account_number" not in result["account"]


def test_paper_policy_disables_entry_limits_without_weakening_other_filters():
    settings = runner.paper_micro_settings(env())
    assert settings.entry_limits_enabled is False
    assert settings.allowed_symbols == ("F",)


def test_kill_switch_blocks_before_broker_access(monkeypatch):
    monkeypatch.setattr(runner, "AlpacaMicroPaperBroker", lambda **kwargs: (_ for _ in ()).throw(AssertionError()))
    result = runner.run_paper_micro_cycle(env(PAPER_MICRO_KILL_SWITCH="true"))
    assert result["status"] == "blocked"
    assert result["reasons"] == ["paper_micro_kill_switch_active"]


def test_armed_paper_runner_delegates_to_identical_controlled_cycle(monkeypatch):
    broker = object()
    monkeypatch.setattr(runner, "AlpacaMicroPaperBroker", lambda **kwargs: broker)
    captured = {}
    def fake_cycle(**kwargs):
        captured.update(kwargs)
        return {"status":"no_trade", "submitted":False}
    monkeypatch.setattr(runner, "run_controlled_live_cycle", fake_cycle)
    result = runner.run_paper_micro_cycle(env())
    assert result["status"] == "no_trade"
    assert captured["broker"] is broker
    assert captured["environ"]["TRADING_MODE"] == "LIVE"
    assert captured["environ"]["LIVE_STATE_PATH"].endswith("paper-micro-state.json")
