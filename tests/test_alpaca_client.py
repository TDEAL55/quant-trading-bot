import pytest

from alpaca_client import AlpacaClient, create_alpaca_client


class _EnumLike:
    def __init__(self, value: str, name: str | None = None):
        self.value = value
        self.name = name or value


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
        self.assets = [
            {"symbol": "AAPL", "asset_class": "US_EQUITY", "status": "ACTIVE", "tradable": True},
            {"symbol": "MSFT", "asset_class": "US_EQUITY", "status": "ACTIVE", "tradable": True},
        ]

    def get_account(self):
        return self.account

    def get_all_positions(self):
        return self.positions

    def get_all_assets(self, filter=None):
        del filter
        return self.assets


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


def test_alpaca_client_reads_assets_api(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "demo-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo-secret")

    client = create_alpaca_client(mode="PAPER", trading_client=MockTradingClient())
    rows = client.get_assets()
    assert len(rows) == 2
    assert rows[0]["symbol"] == "AAPL"


def test_alpaca_client_assets_fallback_when_filtered_results_empty(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "demo-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo-secret")

    class EmptyFilteredTradingClient(MockTradingClient):
        def __init__(self):
            super().__init__()
            self.calls = []

        def get_all_assets(self, filter=None):
            self.calls.append(filter)
            if filter is not None:
                return []
            return self.assets

    client_backend = EmptyFilteredTradingClient()
    client = create_alpaca_client(mode="PAPER", trading_client=client_backend)
    details = client.get_assets_diagnostics()
    rows = client.get_assets()

    assert len(rows) == 2
    assert rows[0]["symbol"] == "AAPL"
    assert details["fallback_used"] is True
    assert details["filtered_api_asset_count"] == 0
    assert details["unfiltered_asset_count"] == 2
    assert details["client_filtered_asset_count"] == 2
    assert len(client_backend.calls) >= 1


def test_alpaca_client_diagnostics_accept_enum_and_string_values(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "demo-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo-secret")

    class MixedAssetsTradingClient(MockTradingClient):
        def __init__(self):
            super().__init__()
            self.assets = [
                {"symbol": "AAPL", "asset_class": _EnumLike("us_equity", "US_EQUITY"), "status": _EnumLike("active", "ACTIVE"), "tradable": "true"},
                {"symbol": "MSFT", "asset_class": "US_EQUITY", "status": "ACTIVE", "tradable": True},
                {"symbol": "SNAP", "asset_class": "US_EQUITY", "status": "INACTIVE", "tradable": True},
                {"symbol": "BTCUSD", "asset_class": "CRYPTO", "status": "ACTIVE", "tradable": True},
                {"symbol": "TSLA", "asset_class": "US_EQUITY", "status": "ACTIVE", "tradable": False},
            ]

        def get_all_assets(self, filter=None):
            if filter is not None:
                return []
            return self.assets

    client = create_alpaca_client(mode="PAPER", trading_client=MixedAssetsTradingClient())
    details = client.get_assets_diagnostics()
    rows = client.get_assets()

    assert details["fallback_used"] is True
    assert details["unfiltered_asset_count"] == 5
    assert details["active_count"] == 4
    assert details["us_equity_count"] == 4
    assert details["tradable_count"] == 4
    assert details["rejected_by_status"] == 1
    assert details["rejected_by_asset_class"] == 1
    assert details["rejected_non_tradable"] == 1
    assert len(rows) == 2
    assert {item["symbol"] for item in rows} == {"AAPL", "MSFT"}