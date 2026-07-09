from unittest.mock import patch

import pytest

from paper_api_client import PaperAPIClient, create_paper_api_client


def test_paper_api_client_uses_env_credentials_and_exposes_read_only_data(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("PAPER_API_BASE_URL", "https://paper.example")
    monkeypatch.setenv("PAPER_API_USERNAME", "demo-user")
    monkeypatch.setenv("PAPER_API_PASSWORD", "demo-pass")
    monkeypatch.setenv("PAPER_API_TOKEN", "demo-token")

    client = create_paper_api_client(positions={"SPY": {"quantity": 2, "avg_price": 501.25}})

    assert client.get_account_status() == "paper_trading"
    assert client.get_buying_power() == 10000.0
    assert client.get_positions()["SPY"]["quantity"] == 2
    assert client.credentials["username"] == "demo-user"


def test_paper_api_client_fails_safely_when_credentials_are_missing(monkeypatch):
    monkeypatch.delenv("TRADING_MODE", raising=False)
    monkeypatch.delenv("PAPER_API_BASE_URL", raising=False)
    monkeypatch.delenv("PAPER_API_USERNAME", raising=False)
    monkeypatch.delenv("PAPER_API_PASSWORD", raising=False)
    monkeypatch.delenv("PAPER_API_TOKEN", raising=False)

    with pytest.raises(ValueError, match="Missing required paper API credentials"):
        PaperAPIClient(mode="PAPER")


def test_paper_api_client_blocks_live_mode(monkeypatch):
    monkeypatch.setenv("PAPER_API_BASE_URL", "https://paper.example")
    monkeypatch.setenv("PAPER_API_USERNAME", "demo-user")
    monkeypatch.setenv("PAPER_API_PASSWORD", "demo-pass")
    monkeypatch.setenv("PAPER_API_TOKEN", "demo-token")

    with pytest.raises(RuntimeError, match="LIVE mode is blocked"):
        PaperAPIClient(mode="LIVE")


def test_paper_api_client_submit_order_is_disabled(monkeypatch):
    monkeypatch.setenv("PAPER_API_BASE_URL", "https://paper.example")
    monkeypatch.setenv("PAPER_API_USERNAME", "demo-user")
    monkeypatch.setenv("PAPER_API_PASSWORD", "demo-pass")
    monkeypatch.setenv("PAPER_API_TOKEN", "demo-token")

    client = PaperAPIClient(mode="PAPER")

    with pytest.raises(NotImplementedError, match="disabled in paper_api_client"):
        client.submit_order("buy", "SPY", 1)


def test_paper_api_client_reads_env_via_mock(monkeypatch):
    values = {
        "TRADING_MODE": "PAPER",
        "PAPER_API_BASE_URL": "https://paper.example",
        "PAPER_API_USERNAME": "demo-user",
        "PAPER_API_PASSWORD": "demo-pass",
        "PAPER_API_TOKEN": "demo-token",
    }

    def fake_getenv(name, default=""):
        return values.get(name, default)

    monkeypatch.delenv("TRADING_MODE", raising=False)
    monkeypatch.delenv("PAPER_API_BASE_URL", raising=False)
    monkeypatch.delenv("PAPER_API_USERNAME", raising=False)
    monkeypatch.delenv("PAPER_API_PASSWORD", raising=False)
    monkeypatch.delenv("PAPER_API_TOKEN", raising=False)

    with patch("paper_api_client.os.getenv", side_effect=fake_getenv) as mocked_getenv:
        client = PaperAPIClient()

    assert client.get_account_status() == "paper_trading"
    assert mocked_getenv.call_count >= 4