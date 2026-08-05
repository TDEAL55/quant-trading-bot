import pytest

from paper_broker import create_paper_broker


def test_paper_broker_reports_mock_account():
    broker = create_paper_broker(mode="SIMULATION")
    assert broker.get_account_status() == "ACTIVE"
    assert broker.get_buying_power() == 10000.0
    assert broker.get_positions()["SPY"]["quantity"] == 0


def test_paper_broker_simulation_mode_keeps_local_state(monkeypatch):
    monkeypatch.delenv("TRADING_MODE", raising=False)
    broker = create_paper_broker(mode="SIMULATION")

    assert broker.get_account()["mode"] == "paper"
    assert broker.get_account_status() == "ACTIVE"
    assert broker.get_buying_power() == 10000.0
    assert broker.get_positions()["AAPL"]["quantity"] == 0


def test_paper_broker_unsupported_backend_fails_closed(monkeypatch):
    monkeypatch.setenv("PAPER_BROKER_BACKEND", "INVALID")
    with pytest.raises(RuntimeError, match="PAPER_BROKER_BACKEND"):
        create_paper_broker(mode="PAPER")
