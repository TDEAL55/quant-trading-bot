import importlib

import config


def test_safe_mode_allows_simulation():
    assert config.is_safe_mode("SIMULATION") is True


def test_safe_mode_blocks_live():
    assert config.is_safe_mode("LIVE") is False


def test_config_handles_missing_alpaca_credentials_safely(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "")
    monkeypatch.setenv("ALPACA_API_SECRET", "")

    importlib.reload(config)

    assert config.ALPACA_API_KEY == ""
    assert config.ALPACA_API_SECRET == ""
