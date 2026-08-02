from __future__ import annotations

import pytest

from alpaca_paper_broker import ALPACA_PAPER_ENDPOINT, AlpacaPaperBroker
from sprint_10_2_execution_validation import _client_order_id


class _Account:
    def __init__(self, status="ACTIVE"):
        self.status = status
        self.buying_power = "10000"
        self.cash = "9000"
        self.equity = "11000"
        self.portfolio_value = "11000"
        self.trading_blocked = False
        self.account_blocked = False
        self.account_number = "PA-123"
        self.currency = "USD"


class _Position:
    def __init__(self, symbol="AAPL", qty="2", avg_entry_price="180", market_value="360"):
        self.symbol = symbol
        self.qty = qty
        self.avg_entry_price = avg_entry_price
        self.market_value = market_value


class _Order:
    def __init__(self, status="filled", client_order_id="cid-1", order_id="oid-1"):
        self.id = order_id
        self.client_order_id = client_order_id
        self.symbol = "AAPL"
        self.side = "buy"
        self.qty = "1"
        self.filled_qty = "1"
        self.order_type = "market"
        self.time_in_force = "day"
        self.submitted_at = "2026-08-01T13:00:00Z"
        self.updated_at = "2026-08-01T13:00:01Z"
        self.status = status
        self.filled_avg_price = "200"
        self.failed_at = ""


class _Asset:
    tradable = True
    fractionable = True


class _TradingClient:
    def __init__(self):
        self.submit_calls = 0
        self.orders_by_client_id = {}
        self.orders_by_id = {}

    def get_account(self):
        return _Account()

    def get_all_positions(self):
        return [_Position()]

    def get_orders(self, filter=None):
        del filter
        return []

    def get_order_by_id(self, order_id):
        return self.orders_by_id[order_id]

    def get_order_by_client_id(self, client_order_id):
        return self.orders_by_client_id[client_order_id]

    def submit_order(self, order_data=None):
        del order_data
        self.submit_calls += 1
        order = _Order(status="filled", client_order_id="new-cid", order_id="new-oid")
        self.orders_by_client_id[order.client_order_id] = order
        self.orders_by_id[order.id] = order
        return order

    def cancel_order_by_id(self, order_id):
        self.orders_by_id[order_id].status = "canceled"

    def get_asset(self, symbol):
        del symbol
        return _Asset()


def test_alpaca_paper_endpoint_is_enforced(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "demo-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo-secret")
    monkeypatch.setenv("ALPACA_PAPER_BASE_URL", ALPACA_PAPER_ENDPOINT)
    monkeypatch.setenv("ALPACA_ORDER_SUBMISSION_ENABLED", "false")

    broker = AlpacaPaperBroker(mode="PAPER", trading_client=_TradingClient())
    account = broker.get_account()

    assert account["paper_endpoint_confirmed"] is True
    assert account["status"] == "ACTIVE"


def test_alpaca_live_endpoint_is_rejected(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "demo-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo-secret")
    monkeypatch.setenv("ALPACA_PAPER_BASE_URL", "https://api.alpaca.markets")

    with pytest.raises(RuntimeError, match="ALPACA_PAPER_BASE_URL"):
        AlpacaPaperBroker(mode="PAPER", trading_client=_TradingClient())


def test_alpaca_missing_credentials_fails_closed(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)
    monkeypatch.setenv("ALPACA_PAPER_BASE_URL", ALPACA_PAPER_ENDPOINT)

    with pytest.raises(RuntimeError, match="Missing required Alpaca credentials"):
        AlpacaPaperBroker(mode="PAPER", trading_client=_TradingClient())


def test_deterministic_client_order_id_is_stable():
    first = _client_order_id("strategy-1", "fp-1", "AAPL", "BUY", 1.25)
    second = _client_order_id("strategy-1", "fp-1", "AAPL", "BUY", 1.25)
    third = _client_order_id("strategy-1", "fp-2", "AAPL", "BUY", 1.25)

    assert first == second
    assert first != third


def test_existing_client_order_is_recovered_without_duplicate_submit(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "demo-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo-secret")
    monkeypatch.setenv("ALPACA_PAPER_BASE_URL", ALPACA_PAPER_ENDPOINT)
    monkeypatch.setenv("ALPACA_ORDER_SUBMISSION_ENABLED", "true")

    client = _TradingClient()
    existing = _Order(status="new", client_order_id="cid-existing", order_id="oid-existing")
    client.orders_by_client_id[existing.client_order_id] = existing
    client.orders_by_id[existing.id] = existing

    broker = AlpacaPaperBroker(mode="PAPER", trading_client=client)
    recovered = broker.submit_order(
        side="buy",
        ticker="AAPL",
        quantity=1.0,
        client_order_id="cid-existing",
        wait_for_fill=False,
    )

    assert recovered["client_order_id"] == "cid-existing"
    assert recovered["recovered_existing"] is True
    assert client.submit_calls == 0