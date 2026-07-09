from datetime import datetime

import manual_paper_trade


class MockClock:
    def __init__(self, is_open):
        self.is_open = is_open


class MockOrder:
    def __init__(self, order_id="paper-order-1", status="accepted"):
        self.id = order_id
        self.status = status


class MockTradingClient:
    def __init__(self, api_key=None, secret_key=None, paper=None, is_open=True):
        self.api_key = api_key
        self.secret_key = secret_key
        self.paper = paper
        self.is_open = is_open
        self.submit_calls = 0
        self.last_order_data = None

    def get_clock(self):
        return MockClock(is_open=self.is_open)

    def submit_order(self, order_data=None):
        self.submit_calls += 1
        self.last_order_data = order_data
        return MockOrder()


def test_blocks_live_mode(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "LIVE")
    monkeypatch.setenv("ALPACA_API_KEY", "demo-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo-secret")

    result = manual_paper_trade.run_manual_paper_trade(input_fn=lambda _: "YES")

    assert result["submitted_or_canceled"] == "canceled"
    assert result["status"] == "error"
    assert "LIVE mode is blocked" in result["error"]


def test_requires_paper_mode(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "SIMULATION")
    monkeypatch.setenv("ALPACA_API_KEY", "demo-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo-secret")

    result = manual_paper_trade.run_manual_paper_trade(input_fn=lambda _: "YES")

    assert result["submitted_or_canceled"] == "canceled"
    assert result["status"] == "error"
    assert "requires TRADING_MODE=PAPER" in result["error"]


def test_cancels_when_market_closed(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("ALPACA_API_KEY", "demo-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo-secret")

    client = MockTradingClient(is_open=False)
    result = manual_paper_trade.run_manual_paper_trade(
        input_fn=lambda _: "YES",
        client_factory=lambda **kwargs: client,
    )

    assert result["submitted_or_canceled"] == "canceled"
    assert result["status"] == "market_closed"
    assert client.submit_calls == 0


def test_cancels_when_confirmation_is_not_exact_yes(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("ALPACA_API_KEY", "demo-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo-secret")

    client = MockTradingClient(is_open=True)
    result = manual_paper_trade.run_manual_paper_trade(
        input_fn=lambda _: "yes",
        client_factory=lambda **kwargs: client,
    )

    assert result["submitted_or_canceled"] == "canceled"
    assert result["status"] == "canceled"
    assert client.submit_calls == 0


def test_submits_one_paper_order_when_yes(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("ALPACA_API_KEY", "demo-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo-secret")

    client = MockTradingClient(is_open=True)
    result = manual_paper_trade.run_manual_paper_trade(
        input_fn=lambda _: "YES",
        client_factory=lambda **kwargs: client,
    )

    assert result["submitted_or_canceled"] == "submitted"
    assert result["symbol"] == "SPY"
    assert result["notional_amount"] == 10.0
    assert result["order_id"] == "paper-order-1"
    assert result["status"] == "accepted"
    assert client.submit_calls == 1
    assert str(client.last_order_data.symbol) == "SPY"
    assert float(client.last_order_data.notional) == 10.0


def test_missing_credentials_returns_error(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)

    isolated_env = tmp_path / ".env"
    isolated_env.write_text("TRADING_MODE=PAPER\n", encoding="utf-8")

    result = manual_paper_trade.run_manual_paper_trade(input_fn=lambda _: "YES", env_path=isolated_env)

    assert result["submitted_or_canceled"] == "canceled"
    assert result["status"] == "error"
    assert "Missing required Alpaca credentials" in result["error"]