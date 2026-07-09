import pytest

from paper_broker import PaperBroker, create_paper_broker
from config import is_safe_mode


class MockBroker:
    def get_account(self):
        return {"status": "paper"}

    def get_positions(self):
        return {"SPY": {"quantity": 1, "avg_price": 100.0}}

    def get_buying_power(self):
        return 5000.0

    def submit_order(self, *args, **kwargs):
        return {"status": "submitted"}


class MockPaperClient:
    def __init__(self):
        self.credentials = {
            "base_url": "https://paper.example",
            "username": "demo-user",
            "password": "demo-pass",
            "token": "demo-token",
        }

    def get_account_status(self):
        return "paper_trading"

    def get_positions(self):
        return {"SPY": {"quantity": 2, "avg_price": 500.0}}

    def get_buying_power(self):
        return 7500.0


def test_paper_broker_adapter_exposes_required_interface():
    broker = create_paper_broker(mode="SIMULATION")
    assert broker.get_account()["mode"] == "paper"
    assert broker.get_positions()["SPY"]["quantity"] == 0
    assert broker.get_buying_power() == 10000.0


def test_paper_broker_submit_order_is_disabled_by_default():
    broker = create_paper_broker(mode="SIMULATION")
    with pytest.raises(NotImplementedError):
        broker.submit_order("buy", "SPY", 1)


def test_paper_broker_rejects_live_mode():
    broker = PaperBroker(mode="LIVE")
    assert broker.is_safe() is False


def test_paper_broker_uses_environment_credentials(monkeypatch):
    monkeypatch.setenv("PAPER_API_USERNAME", "demo")
    monkeypatch.setenv("PAPER_API_PASSWORD", "secret")
    broker = create_paper_broker(mode="SIMULATION")
    assert broker.credentials["username"] == "demo"
    assert broker.credentials["password"] == "secret"


def test_paper_broker_paper_mode_allows_only_read_only_methods(monkeypatch):
    monkeypatch.setenv("PAPER_API_BASE_URL", "https://paper.example")
    monkeypatch.setenv("PAPER_API_USERNAME", "demo-user")
    monkeypatch.setenv("PAPER_API_PASSWORD", "demo-pass")
    monkeypatch.setenv("PAPER_API_TOKEN", "demo-token")
    monkeypatch.setattr("paper_broker.create_paper_api_client", lambda **kwargs: MockPaperClient())

    broker = create_paper_broker(mode="PAPER")

    assert broker.get_account_status() == "paper_trading"
    assert broker.get_buying_power() == 7500.0
    assert broker.get_positions()["SPY"]["quantity"] == 2

    with pytest.raises(RuntimeError, match="disabled in PAPER mode"):
        broker.get_account()

    with pytest.raises(NotImplementedError):
        broker.submit_order("buy", "SPY", 1)


def test_safe_mode_rejects_live_trading():
    assert is_safe_mode("LIVE") is False
    assert is_safe_mode("SIMULATION") is True
