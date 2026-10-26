from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import alpaca_live_broker as module
from alpaca_live_broker import AlpacaLiveBroker


class Client:
    def __init__(self):
        self.submitted = []
    def get_account(self):
        return SimpleNamespace(status="ACTIVE", buying_power="300", non_marginable_buying_power="300", cash="300",
            equity="300", last_equity="300", portfolio_value="300", multiplier="1", trading_blocked=False,
            account_blocked=False, account_number="secret", currency="USD")
    def get_order_by_client_id(self, client_id):
        raise RuntimeError("not found")
    def submit_order(self, order_data):
        self.submitted.append(order_data)
        return SimpleNamespace(id="oid", client_order_id="cid", symbol="F", side="buy", qty="2", filled_qty="0",
            order_type="market", time_in_force="gtc", submitted_at="now", updated_at="now", status="accepted",
            filled_avg_price=None, failed_at=None)
    def replace_order_by_id(self, order_id, order_data):
        self.replaced = (order_id, order_data)
        return SimpleNamespace(
            id="new-stop-id", client_order_id="stop-cid", symbol="F", side="sell", qty="2",
            filled_qty="0", order_type="stop", time_in_force="gtc", submitted_at="now",
            updated_at="now", status="new", filled_avg_price=None, failed_at=None,
            stop_price=order_data["stop_price"], limit_price=None, position_intent="sell_to_close",
        )


class NestedOrderClient(Client):
    def __init__(self):
        super().__init__()
        self.order_filter = None

    def get_orders(self, filter=None):
        self.order_filter = filter
        take_profit = SimpleNamespace(id="take-profit", client_order_id="take-profit-cid", symbol="F", side="sell", qty="2",
            filled_qty="0", order_type="limit", time_in_force="gtc", submitted_at="2026-09-14T14:00:00+00:00",
            updated_at="2026-09-15T14:00:00+00:00", status="new", filled_avg_price=None, failed_at=None,
            asset_class="us_equity", position_intent="sell_to_close", limit_price="13.2", stop_price=None)
        stop_loss = SimpleNamespace(id="stop-loss", client_order_id="stop-loss-cid", symbol="F", side="sell", qty="2",
            filled_qty="0", order_type="stop", time_in_force="gtc", submitted_at="2026-09-14T14:00:00+00:00",
            updated_at="2026-09-15T14:00:01+00:00", status="held", filled_avg_price=None, failed_at=None,
            asset_class="us_equity", position_intent="sell_to_close", limit_price=None, stop_price="11.4")
        parent = SimpleNamespace(id="parent", client_order_id="qtb-parent-cid", symbol="F", side="buy", qty="2",
            filled_qty="2", order_type="market", time_in_force="gtc", submitted_at="now", updated_at="now",
            filled_at="2026-09-14T14:00:10+00:00", status="filled", filled_avg_price="12",
            failed_at=None, legs=[take_profit, stop_loss],
            asset_class="us_equity", position_intent="buy_to_open")
        return [parent]


class AssetClient(Client):
    def get_all_assets(self, request=None):
        del request
        return [
            SimpleNamespace(symbol="F", name="Ford Motor", asset_class="us_equity", status="active", exchange="NYSE", tradable=True),
            SimpleNamespace(symbol="SPY", name="SPDR S&P 500 ETF", asset_class="us_equity", status="active", exchange="ARCA", tradable=True),
            SimpleNamespace(symbol="OLD", name="Old Corp", asset_class="us_equity", status="inactive", exchange="NYSE", tradable=True),
            SimpleNamespace(symbol="BTC/USD", name="Bitcoin", asset_class="crypto", status="active", exchange="", tradable=True),
        ]


class InvalidAssetModelClient(Client):
    def get_all_assets(self, request=None):
        del request
        raise ValueError("unknown asset attribute")


def env(**updates):
    payload = {"TRADING_MODE": "LIVE", "LIVE_TRADING_ENABLED": "true",
        "ALPACA_LIVE_ORDER_SUBMISSION_ENABLED": "true", "LIVE_TRADING_CONFIRMATION": "ENABLE_LIVE_MICRO_TRADING",
        "ALPACA_LIVE_API_KEY": "key", "ALPACA_LIVE_API_SECRET": "secret",
        "ALPACA_LIVE_BASE_URL": "https://api.alpaca.markets"}
    payload.update(updates)
    return payload


def test_live_broker_rejects_wrong_mode_and_endpoint():
    with pytest.raises(RuntimeError):
        AlpacaLiveBroker(trading_client=Client(), environ=env(TRADING_MODE="PAPER"))
    with pytest.raises(RuntimeError):
        AlpacaLiveBroker(trading_client=Client(), environ=env(ALPACA_LIVE_BASE_URL="https://paper-api.alpaca.markets"))


def test_read_only_check_needs_no_arm_flags_and_cannot_submit():
    broker = AlpacaLiveBroker(trading_client=Client(), environ=env(LIVE_TRADING_ENABLED="false",
        ALPACA_LIVE_ORDER_SUBMISSION_ENABLED="false", LIVE_TRADING_CONFIRMATION=""), read_only=True)
    assert broker.get_account()["equity"] == 300
    assert not broker.order_submission_enabled


def test_newly_funded_account_does_not_count_deposit_as_daily_profit():
    client = Client()
    account = client.get_account()
    account.last_equity = "0"
    client.get_account = lambda: account
    broker = AlpacaLiveBroker(trading_client=client, environ=env(), read_only=True)
    assert broker.get_account()["day_pl"] == 0.0


def test_intraday_cash_adjustment_does_not_mask_trading_loss():
    client = Client()
    account = client.get_account()
    account.equity = "1435.75"
    account.last_equity = "1333.83"
    account.intraday_adjustments = "105.70"
    client.get_account = lambda: account

    snapshot = AlpacaLiveBroker(trading_client=client, environ=env(), read_only=True).get_account()

    assert snapshot["account_balance_change"] == pytest.approx(101.92)
    assert snapshot["intraday_adjustments"] == pytest.approx(105.70)
    assert snapshot["day_pl"] == pytest.approx(-3.78)


def test_intraday_withdrawal_does_not_create_false_trading_loss():
    client = Client()
    account = client.get_account()
    account.equity = "900"
    account.last_equity = "1000"
    account.intraday_adjustments = "-100"
    client.get_account = lambda: account

    snapshot = AlpacaLiveBroker(trading_client=client, environ=env(), read_only=True).get_account()

    assert snapshot["account_balance_change"] == -100.0
    assert snapshot["intraday_adjustments"] == -100.0
    assert snapshot["day_pl"] == 0.0


def test_bracket_order_is_whole_share_gtc(monkeypatch):
    monkeypatch.setattr(module, "MarketOrderRequest", lambda **kwargs: kwargs)
    monkeypatch.setattr(module, "TakeProfitRequest", lambda **kwargs: kwargs)
    monkeypatch.setattr(module, "StopLossRequest", lambda **kwargs: kwargs)
    monkeypatch.setattr(module, "OrderClass", SimpleNamespace(BRACKET="bracket"))
    monkeypatch.setattr(module, "OrderSide", SimpleNamespace(BUY="buy"))
    monkeypatch.setattr(module, "TimeInForce", SimpleNamespace(GTC="gtc"))
    client = Client()
    broker = AlpacaLiveBroker(trading_client=client, environ=env())
    result = broker.submit_bracket_entry(symbol="F", quantity=2, reference_price=12, stop_price=11.4,
        target_price=13.2, client_order_id="cid")
    assert result["status"] == "accepted"
    assert client.submitted[0]["order_class"] == "bracket"
    assert client.submitted[0]["time_in_force"] == "gtc"
    with pytest.raises(RuntimeError):
        broker.get_tradable_crypto_assets()


def test_live_broker_only_tightens_verified_stop_below_market(monkeypatch):
    monkeypatch.setattr(module, "ReplaceOrderRequest", lambda **kwargs: kwargs)
    client = Client()
    broker = AlpacaLiveBroker(trading_client=client, environ=env())

    result = broker.tighten_protective_stop(
        symbol="F",
        stop_order_id="old-stop-id",
        current_stop_price=10,
        proposed_stop_price=11,
        current_market_price=12,
    )

    assert client.replaced == ("old-stop-id", {"stop_price": 11.0})
    assert result["status"] == "new"
    assert result["previous_stop_price"] == 10.0
    assert result["requested_stop_price"] == 11.0
    with pytest.raises(RuntimeError):
        broker.tighten_protective_stop(
            symbol="F",
            stop_order_id="old-stop-id",
            current_stop_price=10,
            proposed_stop_price=9,
            current_market_price=12,
        )


def test_open_orders_flattens_protective_bracket_legs():
    client = NestedOrderClient()
    broker = AlpacaLiveBroker(trading_client=client, environ=env(), read_only=True)
    orders = broker.get_open_orders()
    assert len(orders) == 2
    assert all(order["side"] == "sell" for order in orders)
    assert next(order for order in orders if order["order_type"] == "stop")["stop_price"] == 11.4
    assert client.order_filter.status == module.QueryOrderStatus.ALL
    assert client.order_filter.nested is True
    assert client.order_filter.limit == 500


def test_order_history_flattens_bracket_legs_for_realized_pnl():
    broker = AlpacaLiveBroker(trading_client=NestedOrderClient(), environ=env(), read_only=True)
    orders = broker.get_order_history(limit=50)
    assert len(orders) == 3
    sell_orders = [order for order in orders if order["side"] == "sell"]
    assert len(sell_orders) == 2
    assert all(order["parent_order_id"] == "parent" for order in sell_orders)
    assert all(order["parent_client_order_id"] == "qtb-parent-cid" for order in sell_orders)
    assert next(order for order in orders if order["order_id"] == "parent")["filled_at"] == (
        "2026-09-14T14:00:10+00:00"
    )


def test_live_broker_returns_broad_active_stock_universe_without_etfs():
    broker = AlpacaLiveBroker(trading_client=AssetClient(), environ=env(), read_only=True)
    assert [row["symbol"] for row in broker.get_tradable_stock_assets()] == ["F"]
    assert [row["symbol"] for row in broker.get_tradable_stock_assets(include_etfs=True)] == ["F", "SPY"]


def test_live_broker_falls_back_to_raw_assets_when_sdk_validation_fails(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [
                {
                    "symbol": "PCOR",
                    "name": "Procore Technologies",
                    "asset_class": "us_equity",
                    "status": "active",
                    "exchange": "NYSE",
                    "tradable": True,
                    "attributes": ["future_sdk_value"],
                }
            ]

    observed = {}

    def fake_get(url, **kwargs):
        observed.update({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr(module.requests, "get", fake_get)
    broker = AlpacaLiveBroker(
        trading_client=InvalidAssetModelClient(),
        environ=env(),
        read_only=True,
    )

    assert [row["symbol"] for row in broker.get_tradable_stock_assets()] == ["PCOR"]
    assert observed["url"] == "https://api.alpaca.markets/v2/assets"
    assert observed["params"] == {"status": "active", "asset_class": "us_equity"}
    assert observed["timeout"] == 20


def test_live_broker_fetches_bounded_recent_news_for_entry_gate(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "news": [
                    {
                        "headline": "Election result moves Brazil stocks",
                        "summary": "Policy repricing followed.",
                        "symbols": ["PAGS"],
                        "created_at": "2026-10-26T12:00:00Z",
                        "updated_at": "2026-10-26T12:05:00Z",
                        "source": "wire",
                        "content": "not returned by the adapter",
                    }
                ]
            }

    observed = {}

    def fake_get(url, **kwargs):
        observed.update({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr(module.requests, "get", fake_get)
    broker = AlpacaLiveBroker(trading_client=Client(), environ=env(), read_only=True)
    rows = broker.get_recent_news(
        ["PAGS", "PAGS"],
        lookback_hours=96,
        now=datetime(2026, 10, 26, 15, tzinfo=timezone.utc),
    )

    assert observed["url"] == "https://data.alpaca.markets/v1beta1/news"
    assert observed["params"]["symbols"] == "PAGS"
    assert observed["params"]["limit"] == 50
    assert observed["timeout"] == 15
    assert rows == [
        {
            "headline": "Election result moves Brazil stocks",
            "summary": "Policy repricing followed.",
            "symbols": ["PAGS"],
            "created_at": "2026-10-26T12:00:00Z",
            "updated_at": "2026-10-26T12:05:00Z",
            "source": "wire",
        }
    ]


def test_live_broker_fetches_latest_sip_prices_for_premarket_shadow(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "PAGS": {"latestTrade": {"p": 7.50}},
                "NWE": {"minuteBar": {"c": 72.40}},
            }

    observed = {}

    def fake_get(url, **kwargs):
        observed.update({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr(module.requests, "get", fake_get)
    broker = AlpacaLiveBroker(trading_client=Client(), environ=env(), read_only=True)

    assert broker.get_latest_stock_prices(["PAGS", "NWE"]) == {
        "PAGS": 7.50,
        "NWE": 72.40,
    }
    assert observed["url"] == "https://data.alpaca.markets/v2/stocks/snapshots"
    assert observed["params"] == {"symbols": "PAGS,NWE", "feed": "sip"}
    assert observed["timeout"] == 15
