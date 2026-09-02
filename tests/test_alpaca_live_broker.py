from __future__ import annotations

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


class NestedOrderClient(Client):
    def get_orders(self, filter=None):
        del filter
        leg = SimpleNamespace(id="leg", client_order_id="leg-cid", symbol="F", side="sell", qty="2",
            filled_qty="0", order_type="stop", time_in_force="gtc", submitted_at="now", updated_at="now",
            status="new", filled_avg_price=None, failed_at=None)
        parent = SimpleNamespace(id="parent", client_order_id="parent-cid", symbol="F", side="buy", qty="2",
            filled_qty="2", order_type="market", time_in_force="gtc", submitted_at="now", updated_at="now",
            status="filled", filled_avg_price="12", failed_at=None, legs=[leg, leg])
        return [parent]


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


def test_open_orders_flattens_protective_bracket_legs():
    broker = AlpacaLiveBroker(trading_client=NestedOrderClient(), environ=env(), read_only=True)
    orders = broker.get_open_orders()
    assert len([order for order in orders if order["side"] == "sell"]) == 2


def test_order_history_flattens_bracket_legs_for_realized_pnl():
    broker = AlpacaLiveBroker(trading_client=NestedOrderClient(), environ=env(), read_only=True)
    orders = broker.get_order_history(limit=50)
    assert len(orders) == 3
    assert len([order for order in orders if order["side"] == "sell"]) == 2
