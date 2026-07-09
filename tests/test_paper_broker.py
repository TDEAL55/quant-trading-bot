import pytest

from paper_broker import create_paper_broker


def test_paper_broker_reports_mock_account():
    broker = create_paper_broker(mode="SIMULATION")
    assert broker.get_account_status() == "paper_trading"
    assert broker.get_buying_power() == 10000.0
    assert broker.get_positions()["SPY"]["quantity"] == 0


def test_paper_broker_simulation_mode_keeps_local_state(monkeypatch):
    monkeypatch.delenv("TRADING_MODE", raising=False)
    broker = create_paper_broker(mode="SIMULATION")

    assert broker.get_account()["mode"] == "paper"
    assert broker.get_account_status() == "paper_trading"
    assert broker.get_buying_power() == 10000.0
    assert broker.get_positions()["AAPL"]["quantity"] == 0


def test_paper_broker_missing_paper_credentials_fails_safely(monkeypatch):
    monkeypatch.delenv("PAPER_API_BASE_URL", raising=False)
    monkeypatch.delenv("PAPER_API_USERNAME", raising=False)
    monkeypatch.delenv("PAPER_API_PASSWORD", raising=False)
    monkeypatch.delenv("PAPER_API_TOKEN", raising=False)

    with pytest.raises(ValueError, match="Missing required paper API credentials"):
        create_paper_broker(mode="PAPER")
