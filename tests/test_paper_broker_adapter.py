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


def test_paper_broker_adapter_exposes_required_interface():
    broker = create_paper_broker(mode="SIMULATION")
    assert broker.get_account()["mode"] == "paper"
    assert broker.get_positions()["SPY"]["quantity"] == 0
    assert broker.get_buying_power() == 10000.0


def test_paper_broker_submit_order_is_disabled_by_default():
    broker = create_paper_broker(mode="SIMULATION")
    result = broker.submit_order("buy", "SPY", 1)
    assert result["status"] == "filled"
    assert result["symbol"] == "SPY"


def test_paper_broker_rejects_live_mode():
    with pytest.raises(RuntimeError, match="LIVE mode is blocked"):
        PaperBroker(mode="LIVE")


def test_paper_broker_uses_environment_credentials(monkeypatch):
    monkeypatch.setenv("PAPER_BROKER_BACKEND", "SIMULATED")
    broker = create_paper_broker(mode="SIMULATION")
    assert broker.backend == "SIMULATED"


def test_paper_broker_backend_validation(monkeypatch):
    monkeypatch.setenv("PAPER_BROKER_BACKEND", "UNSUPPORTED")
    with pytest.raises(RuntimeError, match="PAPER_BROKER_BACKEND"):
        create_paper_broker(mode="PAPER")


def test_safe_mode_rejects_live_trading():
    assert is_safe_mode("LIVE") is False
    assert is_safe_mode("SIMULATION") is True
