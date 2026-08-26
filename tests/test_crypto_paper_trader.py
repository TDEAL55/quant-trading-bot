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
    def __init__(self, *, positions=None, account=None, open_orders=None):
        self.positions = dict(positions or {})
        self.account = dict(account or {})
        self.open_orders = list(open_orders or [])
        self.calls = []
        self.cancel_calls = []
        self.account_checks = 0
        self.position_checks = 0

    def get_account(self):
        self.account_checks += 1
        return self.account or {
            "equity": 100000.0,
            "portfolio_value": 100000.0,
            "cash": 50000.0,
            "non_marginable_buying_power": 50000.0,
        }

    def get_positions(self):
        self.position_checks += 1
        return self.positions

    def get_open_orders(self):
        return list(self.open_orders)

    def cancel_order(self, order_id):
        self.cancel_calls.append(order_id)
        canceled = next(row for row in self.open_orders if row.get("order_id") == order_id)
        self.open_orders = [row for row in self.open_orders if row.get("order_id") != order_id]
        return {**canceled, "status": "canceled"}

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


def test_crypto_runtime_cache_reuses_expensive_data_but_rechecks_broker(monkeypatch, tmp_path):
    _enable(monkeypatch)
    monkeypatch.setenv("CRYPTO_SIGNAL_REFRESH_SECONDS", "60")
    monkeypatch.setenv("CRYPTO_UNIVERSE_REFRESH_SECONDS", "900")
    broker = _Broker()
    counts = {"broker": 0, "market_data": 0, "universe": 0, "bars": 0}

    class _CountingMarketData(_MarketData):
        def fetch_bars(self, symbols, **kwargs):
            counts["bars"] += 1
            return super().fetch_bars(symbols, **kwargs)

    def _broker_factory(**_kwargs):
        counts["broker"] += 1
        return broker

    def _market_factory():
        counts["market_data"] += 1
        return _CountingMarketData(rising=True)

    def _universe_loader(**_kwargs):
        counts["universe"] += 1
        return _universe()

    runtime_cache = {}
    first = run_crypto_paper_cycle(
        broker_factory=_broker_factory,
        market_data_factory=_market_factory,
        universe_loader=_universe_loader,
        now=NOW,
        status_path=tmp_path / "status.json",
        trades_path=tmp_path / "trades.jsonl",
        dry_run=True,
        runtime_cache=runtime_cache,
    )
    second = run_crypto_paper_cycle(
        broker_factory=_broker_factory,
        market_data_factory=_market_factory,
        universe_loader=_universe_loader,
        now=NOW.replace(second=10),
        status_path=tmp_path / "status.json",
        trades_path=tmp_path / "trades.jsonl",
        dry_run=True,
        runtime_cache=runtime_cache,
    )

    assert first["signal_data_reused"] is False
    assert second["signal_data_reused"] is True
    assert second["universe_data_reused"] is True
    assert counts == {"broker": 1, "market_data": 1, "universe": 1, "bars": 1}
    assert broker.account_checks == 2
    assert broker.position_checks == 2


def test_crypto_cycle_reserves_buying_power_for_price_movement(monkeypatch, tmp_path):
    _enable(monkeypatch)
    broker = _Broker(
        account={
            "equity": 100000.0,
            "portfolio_value": 100000.0,
            "cash": 90.57,
            "non_marginable_buying_power": 90.57,
        }
    )

    result = run_crypto_paper_cycle(
        broker_factory=lambda **_kwargs: broker,
        market_data_factory=lambda: _MarketData(rising=True),
        universe_loader=_universe,
        now=NOW,
        status_path=tmp_path / "status.json",
        trades_path=tmp_path / "trades.jsonl",
    )

    assert result["confirmed_order_count"] == 1
    assert result["buying_power_usage_percent"] == 95.0
    assert result["spendable_crypto_buying_power"] == 86.0415
    assert broker.calls[0]["quantity"] * broker.calls[0]["reference_price"] <= 86.0415


def test_crypto_cycle_skips_candidate_with_open_buy_and_tries_next(monkeypatch, tmp_path):
    _enable(monkeypatch)
    broker = _Broker(
        open_orders=[
            {
                "order_id": "open-btc",
                "client_order_id": "manual-order",
                "symbol": "BTCUSD",
                "side": "buy",
                "status": "accepted",
                "submitted_at": NOW.isoformat(),
            }
        ]
    )

    def _two_coin_universe(**_kwargs):
        return [
            {"symbol": "BTC/USD", "tradable": True, "asset_class": "crypto", "min_trade_increment": 0.00000001},
            {"symbol": "ETH/USD", "tradable": True, "asset_class": "crypto", "min_trade_increment": 0.00000001},
        ]

    result = run_crypto_paper_cycle(
        broker_factory=lambda **_kwargs: broker,
        market_data_factory=lambda: _MarketData(rising=True),
        universe_loader=_two_coin_universe,
        now=NOW,
        status_path=tmp_path / "status.json",
        trades_path=tmp_path / "trades.jsonl",
    )

    assert result["confirmed_order_count"] == 1
    assert result["blocked_buy_candidates"] == ["BTC/USD"]
    assert broker.calls[0]["ticker"] == "ETH/USD"
    assert broker.cancel_calls == []


def test_crypto_cycle_cancels_stale_bot_order_before_selecting_candidate(monkeypatch, tmp_path):
    _enable(monkeypatch)
    broker = _Broker(
        open_orders=[
            {
                "order_id": "stale-btc",
                "client_order_id": "qtb-crypto-old-order",
                "symbol": "BTCUSD",
                "side": "buy",
                "status": "accepted",
                "submitted_at": "2026-08-24T21:30:00+00:00",
            }
        ]
    )

    result = run_crypto_paper_cycle(
        broker_factory=lambda **_kwargs: broker,
        market_data_factory=lambda: _MarketData(rising=True),
        universe_loader=_universe,
        now=NOW,
        status_path=tmp_path / "status.json",
        trades_path=tmp_path / "trades.jsonl",
    )

    assert broker.cancel_calls == ["stale-btc"]
    assert result["stale_orders_canceled"][0]["status"] == "canceled"
    assert result["stale_orders_canceled"][0]["cancellation_reason"] == "stale_unfilled_bot_order"
    assert result["stale_orders_canceled"][0]["order_age_minutes"] > 10
    assert result["recent_cancellations"][0]["order_id"] == "stale-btc"
    assert result["confirmed_order_count"] == 1
    assert broker.calls[0]["ticker"] == "BTC/USD"
