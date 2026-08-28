from datetime import datetime, timezone

import pandas as pd

from options_paper_trader import run_options_paper_cycle


class _Broker:
    def __init__(self, **_kwargs):
        self.orders = []

    def get_account(self):
        return {"equity": 100000, "options_buying_power": 50000, "options_trading_level": 2}

    def get_positions(self):
        return {}

    def get_open_orders(self):
        return []

    def get_option_contracts(self, *_args, **_kwargs):
        return [
            {
                "symbol": "SPY260918C00139000",
                "expiration_date": "2026-09-18",
                "strike_price": 139,
                "size": 100,
                "open_interest": 1000,
                "type": "call",
            }
        ]

    def submit_option_order(self, **kwargs):
        self.orders.append(kwargs)
        return {"status": "accepted", "order_id": "option-order-1"}


class _MarketData:
    def fetch_underlying_bars(self, symbols, **_kwargs):
        index = pd.date_range(end="2026-08-24T14:00:00Z", periods=100, freq="15min")
        frame = pd.DataFrame({"close": [100 + (i * 0.4) for i in range(100)], "volume": [1000] * 100}, index=index)
        return {symbol: frame for symbol in symbols}

    def fetch_option_snapshots(self, symbols):
        return {
            symbol: {
                "symbol": symbol,
                "bid": 4.9,
                "ask": 5.0,
                "mid": 4.95,
                "spread_percent": 2.02,
                "delta": 0.55,
            }
            for symbol in symbols
        }


def test_options_cycle_builds_defined_risk_call_order(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("OPTIONS_TRADING_ENABLED", "true")
    monkeypatch.setenv("OPTIONS_UNDERLYINGS", "SPY")
    broker = _Broker()

    result = run_options_paper_cycle(
        broker_factory=lambda **_kwargs: broker,
        market_data_factory=lambda: _MarketData(),
        now=datetime(2026, 8, 24, 14, 1, tzinfo=timezone.utc),
        status_path=tmp_path / "options.json",
        trades_path=tmp_path / "options.jsonl",
        dry_run=True,
    )

    assert result["cycle_status"] == "dry_run"
    assert result["last_order"]["side"] == "BUY"
    assert result["last_order"]["position_intent"] == "buy_to_open"
    assert result["last_order"]["estimated_premium"] <= 10000
    assert result["maximum_position_percent"] == 4
    assert result["stop_loss_percent"] == 25
    assert result["take_profit_percent"] == 50
    assert broker.orders == []


def test_options_cycle_waits_outside_regular_market_hours(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("OPTIONS_TRADING_ENABLED", "true")

    result = run_options_paper_cycle(
        broker_factory=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("broker should not load")),
        market_data_factory=lambda: (_ for _ in ()).throw(AssertionError("data should not load")),
        now=datetime(2026, 8, 24, 2, 0, tzinfo=timezone.utc),
        status_path=tmp_path / "options.json",
        trades_path=tmp_path / "options.jsonl",
    )

    assert result["cycle_status"] == "market_closed"
    assert result["confirmed_order_count"] == 0
