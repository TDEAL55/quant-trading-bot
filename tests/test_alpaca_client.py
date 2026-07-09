import pytest

from alpaca_client import AlpacaClient, create_alpaca_client


class MockAccount:
    def __init__(self, status="ACTIVE", buying_power="12500.75"):
        self.status = status
        self.buying_power = buying_power


class MockPosition:
    def __init__(self, symbol, qty, avg_entry_price, market_value):
        self.symbol = symbol
        self.qty = qty
        self.avg_entry_price = avg_entry_price
        self.market_value = market_value


class MockTradingClient:
    def __init__(self, account=None, positions=None):
        self.account = account or MockAccount()
        self.positions = positions or [MockPosition("SPY", "2", "500.10", "1000.20")]

    def get_account(self):
        return self.account

    def get_all_positions(self):
        return self.positions


def test_alpaca_client_fails_safely_when_credentials_missing(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)

    with pytest.raises(ValueError, match="Missing required Alpaca credentials"):
        AlpacaClient(mode="PAPER", trading_client=MockTradingClient())


def test_alpaca_client_blocks_live_mode(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "demo-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo-secret")

    with pytest.raises(RuntimeError, match="LIVE mode is blocked"):
        AlpacaClient(mode="LIVE", trading_client=MockTradingClient())


def test_alpaca_client_read_only_methods(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "demo-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo-secret")

    client = create_alpaca_client(mode="PAPER", trading_client=MockTradingClient())

    assert client.get_account_status() == "ACTIVE"
    assert client.get_buying_power() == 12500.75
    assert client.get_current_positions()[0]["symbol"] == "SPY"
    assert client.get_positions()[0]["qty"] == "2"


def test_alpaca_client_submit_order_is_disabled(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "demo-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo-secret")

    client = create_alpaca_client(mode="SIMULATION", trading_client=MockTradingClient())

    with pytest.raises(NotImplementedError, match="disabled in alpaca_client"):
        client.submit_order("buy", "SPY", 1)