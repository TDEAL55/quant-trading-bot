from datetime import datetime, timezone

import pandas as pd

from crypto_paper_trader import run_crypto_paper_cycle


NOW = datetime(2026, 8, 24, 22, 0, tzinfo=timezone.utc)


def _bars(*, rising: bool) -> pd.DataFrame:
    index = pd.date_range(end=NOW, periods=120, freq="15min", tz="UTC")
    values = [100.0 + (i * 0.4) for i in range(120)] if rising else [160.0 - (i * 0.4) for i in range(120)]
    return pd.DataFrame({"close": values, "volume": [1000.0 + i for i in range(120)]}, index=index)


class _MarketData:
    def __init__(self, *, rising: bool):
        self.rising = rising

    def fetch_bars(self, symbols, **_kwargs):
        return {symbol: _bars(rising=self.rising) for symbol in symbols}


class _Broker:
    def __init__(self, *, positions=None):
        self.positions = dict(positions or {})
        self.calls = []

    def get_account(self):
        return {
            "equity": 100000.0,
            "portfolio_value": 100000.0,
            "cash": 50000.0,
            "non_marginable_buying_power": 50000.0,
        }

    def get_positions(self):
        return self.positions

    def get_open_orders(self):
        return []

    def submit_order(self, **kwargs):
        self.calls.append(dict(kwargs))
        return {
            "order_id": "crypto-order-1",
            "client_order_id": kwargs["client_order_id"],
            "status": "filled",
            "filled_quantity": kwargs["quantity"],
            "average_fill_price": kwargs["reference_price"],
        }


def _universe(**_kwargs):
    return [
        {
            "symbol": "BTC/USD",
            "tradable": True,
            "asset_class": "crypto",
            "min_trade_increment": 0.00000001,
        }
    ]


def _enable(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("CRYPTO_TRADING_ENABLED", "true")
    monkeypatch.setenv("CRYPTO_DRY_RUN", "false")
    monkeypatch.setenv("CRYPTO_BUY_SCORE", "55")
    monkeypatch.setenv("CRYPTO_MAX_POSITION_EQUITY_PERCENT", "50")


def test_crypto_cycle_opens_fractional_gtc_position_capped_at_ten_percent(monkeypatch, tmp_path):
    _enable(monkeypatch)
    broker = _Broker()

    result = run_crypto_paper_cycle(
        broker_factory=lambda **_kwargs: broker,
        market_data_factory=lambda: _MarketData(rising=True),
        universe_loader=_universe,
        now=NOW,
        status_path=tmp_path / "status.json",
        trades_path=tmp_path / "trades.jsonl",
    )

    assert result["cycle_status"] == "order_submitted"
    assert result["confirmed_order_count"] == 1
    assert result["maximum_position_percent"] == 10.0
    assert broker.calls[0]["side"] == "buy"
    assert broker.calls[0]["time_in_force"] == "gtc"
    assert broker.calls[0]["allow_fractional"] is True
    assert broker.calls[0]["quantity"] * broker.calls[0]["reference_price"] <= 10000.01
    assert (tmp_path / "status.json").is_file()
    assert (tmp_path / "trades.jsonl").is_file()


def test_crypto_cycle_sells_existing_position_on_bearish_exit(monkeypatch, tmp_path):
    _enable(monkeypatch)
    broker = _Broker(
        positions={
            "BTCUSD": {
                "quantity": 0.25,
                "avg_price": 150.0,
                "current_price": 118.4,
                "market_value": 29.6,
                "unrealized_pl": -7.9,
                "asset_class": "crypto",
            }
        }
    )

    result = run_crypto_paper_cycle(
        broker_factory=lambda **_kwargs: broker,
        market_data_factory=lambda: _MarketData(rising=False),
        universe_loader=_universe,
        now=NOW,
        status_path=tmp_path / "status.json",
        trades_path=tmp_path / "trades.jsonl",
    )

    assert result["confirmed_order_count"] == 1
    assert broker.calls[0]["side"] == "sell"
    assert broker.calls[0]["ticker"] == "BTC/USD"
    assert broker.calls[0]["quantity"] == 0.25
    assert broker.calls[0]["time_in_force"] == "gtc"

