import pytest

from paper_order import PaperOrderManager, create_paper_order_manager


class MockClock:
    def __init__(self, is_open):
        self.is_open = is_open


class MockTradingClient:
    def __init__(self, is_open=True):
        self._clock = MockClock(is_open=is_open)
        self.submitted = False

    def get_clock(self):
        return self._clock

    def submit_order(self, *args, **kwargs):
        self.submitted = True
        raise AssertionError("submit_order should not be called")


def set_credentials(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "demo-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo-secret")


def test_live_mode_is_blocked(monkeypatch):
    set_credentials(monkeypatch)
    with pytest.raises(RuntimeError, match="LIVE mode is blocked"):
        PaperOrderManager(mode="LIVE", trading_client=MockTradingClient())


def test_missing_credentials_are_rejected(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)

    manager = create_paper_order_manager(mode="PAPER", trading_client=MockTradingClient(is_open=True))
    result = manager.place_order(command="BUY $10 of SPY")

    assert result["approved"] is False
    assert "missing credentials" in result["reason"]


def test_accepts_buy_notional_command_without_share_price(monkeypatch):
    set_credentials(monkeypatch)
    manager = create_paper_order_manager(mode="PAPER", trading_client=MockTradingClient(is_open=True))

    result = manager.place_order(command="BUY $10 of SPY")

    assert result["approved"] is True
    assert result["reason"] == "approved"
    assert result["simulated_order"] == {
        "symbol": "SPY",
        "notional": 10.0,
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "extended_hours": False,
    }


def test_rejects_invalid_command_format(monkeypatch):
    set_credentials(monkeypatch)
    manager = create_paper_order_manager(mode="PAPER", trading_client=MockTradingClient(is_open=True))

    result = manager.place_order(command="BUY SPY")

    assert result["approved"] is False
    assert "command must be in the form" in result["reason"]


def test_rejects_sell_options_margin_and_leverage(monkeypatch):
    set_credentials(monkeypatch)
    manager = create_paper_order_manager(mode="PAPER", trading_client=MockTradingClient(is_open=True))

    sell_result = manager.place_order(command="SELL $10 of SPY")
    options_result = manager.place_order(command="BUY $10 of SPY", asset_class="option")
    leverage_result = manager.place_order(command="BUY $10 of SPY", leverage=2)

    assert sell_result["approved"] is False
    assert "only BUY notional orders are supported" in sell_result["reason"]
    assert options_result["approved"] is False
    assert options_result["reason"] == "options are not supported"
    assert leverage_result["approved"] is False
    assert leverage_result["reason"] == "margin, shorting, and leverage are not supported"


def test_rejects_non_market_orders(monkeypatch):
    set_credentials(monkeypatch)
    manager = create_paper_order_manager(mode="PAPER", trading_client=MockTradingClient(is_open=True))

    result = manager.place_order(command="BUY $10 of SPY", order_type="limit")

    assert result["approved"] is False
    assert result["reason"] == "only market orders are supported"


def test_rejects_notional_over_max_value(monkeypatch):
    set_credentials(monkeypatch)
    manager = create_paper_order_manager(mode="PAPER", trading_client=MockTradingClient(is_open=True))

    result = manager.place_order(command="BUY $25.01 of SPY")

    assert result["approved"] is False
    assert result["reason"] == "maximum notional value is $25"


def test_rejects_duplicate_orders(monkeypatch):
    set_credentials(monkeypatch)
    manager = create_paper_order_manager(mode="PAPER", trading_client=MockTradingClient(is_open=True))

    first = manager.place_order(command="BUY $10 of SPY")
    second = manager.place_order(command="BUY $10 of SPY")

    assert first["approved"] is True
    assert second["approved"] is False
    assert second["reason"] == "duplicate order rejected"


def test_rejects_when_market_closed(monkeypatch):
    set_credentials(monkeypatch)
    manager = create_paper_order_manager(mode="PAPER", trading_client=MockTradingClient(is_open=False))

    result = manager.place_order(command="BUY $10 of SPY")

    assert result["approved"] is False
    assert result["reason"] == "market is closed"


def test_dry_run_default_is_true_and_no_submission(monkeypatch):
    set_credentials(monkeypatch)
    client = MockTradingClient(is_open=True)
    manager = create_paper_order_manager(mode="PAPER", trading_client=client)

    result = manager.place_order(command="BUY $10 of SPY")

    assert manager.dry_run is True
    assert result["approved"] is True
    assert result["submitted"] is False
    assert client.submitted is False


def test_submission_disabled_when_not_dry_run(monkeypatch):
    set_credentials(monkeypatch)
    manager = create_paper_order_manager(
        mode="PAPER",
        dry_run=False,
        submit_enabled=False,
        trading_client=MockTradingClient(is_open=True),
    )

    result = manager.place_order(command="BUY $10 of SPY")

    assert result["approved"] is False
    assert result["reason"] == "order submission disabled"
    assert result["simulated_order"]["notional"] == 10.0


def test_non_paper_mode_is_rejected(monkeypatch):
    set_credentials(monkeypatch)
    manager = create_paper_order_manager(mode="SIMULATION", trading_client=MockTradingClient(is_open=True))

    result = manager.place_order(command="BUY $10 of SPY")

    assert result["approved"] is False
    assert result["reason"] == "paper_order only supports PAPER mode"
