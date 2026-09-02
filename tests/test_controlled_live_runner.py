from __future__ import annotations

from datetime import datetime, timezone

import controlled_live_runner as module
from controlled_live_runner import LiveStateStore, run_controlled_live_cycle
from live_risk_policy import LiveRiskSettings


class Broker:
    def __init__(self, positions=None, orders=None):
        self.positions = positions or {}
        self.orders = orders or []
        self.submitted = []
    def get_account(self):
        return {"status":"ACTIVE", "equity":300, "last_equity":300, "day_pl":0, "cash":300,
            "multiplier":1, "trading_blocked":False, "account_blocked":False}
    def get_positions(self): return self.positions
    def get_open_orders(self): return self.orders
    def get_market_clock(self): return {"is_open": True}
    def submit_bracket_entry(self, **kwargs):
        self.submitted.append(kwargs)
        return {"order_id":"oid", "client_order_id":kwargs["client_order_id"], "symbol":kwargs["symbol"],
            "requested_quantity":kwargs["quantity"], "reference_price":kwargs["reference_price"],
            "stop_price":kwargs["stop_price"], "target_price":kwargs["target_price"], "status":"accepted"}


def armed():
    return LiveRiskSettings(enabled=True, order_submission_enabled=True, kill_switch=False,
        confirmation="ENABLE_LIVE_MICRO_TRADING", private_dashboard_confirmed=True,
        minimum_strategy_score=75, minimum_confidence=70, allowed_symbols=("F",))


def scanner(_records):
    return {"ranked_candidates": [{"symbol":"F", "latest_price":12}]}


def signal(_candidate):
    return [{"strategy_id":"stock_trend_pullback_v3", "strategy_version":"3", "signal":"BUY",
        "strategy_score":80, "confidence":80, "data_quality_status":"ok", "market_regime":"bull"}]


def test_disabled_cycle_never_touches_broker():
    result = run_controlled_live_cycle(environ={"TRADING_MODE":"PAPER"})
    assert result["status"] == "blocked"
    assert not result["submitted"]


def test_healthy_cycle_submits_once_then_daily_limit_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "evaluate_all_strategies", signal)
    broker = Broker()
    store = LiveStateStore(tmp_path / "live.json")
    now = lambda: datetime(2026, 9, 1, 15, tzinfo=timezone.utc)
    env = {"TRADING_MODE":"LIVE"}
    first = run_controlled_live_cycle(environ=env, settings=armed(), broker=broker, scanner=scanner,
        state_store=store, now_provider=now)
    second = run_controlled_live_cycle(environ=env, settings=armed(), broker=broker, scanner=scanner,
        state_store=store, now_provider=now)
    assert first["status"] == "submitted"
    assert broker.submitted[0]["quantity"] == 2
    assert second["status"] == "blocked"
    assert "daily_new_order_limit_reached" in second["reasons"]


def test_unprotected_position_blocks_new_entry(tmp_path):
    broker = Broker(positions={"JPM":{"market_value":25}})
    result = run_controlled_live_cycle(environ={"TRADING_MODE":"LIVE"}, settings=armed(), broker=broker,
        scanner=scanner, state_store=LiveStateStore(tmp_path / "live.json"))
    assert result["status"] == "blocked"
    assert "unprotected_live_positions_require_manual_review" in result["reasons"]


def test_high_price_symbol_cannot_create_fractional_order(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "evaluate_all_strategies", signal)
    result = run_controlled_live_cycle(environ={"TRADING_MODE":"LIVE"}, settings=armed(), broker=Broker(),
        scanner=lambda _: {"ranked_candidates":[{"symbol":"F", "latest_price":31}]},
        state_store=LiveStateStore(tmp_path / "live.json"))
    assert result["status"] == "no_trade"
