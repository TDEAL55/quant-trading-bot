import importlib
import os

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


def test_private_runtime_secrets_override_existing_environment(tmp_path, monkeypatch):
    secret_path = tmp_path / "secrets.env"
    secret_path.write_text("DISCORD_WEBHOOK_URL=https://example.invalid/replacement\n", encoding="utf-8")
    secret_path.chmod(0o600)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://example.invalid/revoked")

    assert config._load_private_runtime_secrets(secret_path) is True
    assert os.environ["DISCORD_WEBHOOK_URL"] == "https://example.invalid/replacement"


def test_paper_tuning_profile_opens_small_allocation_room():
    profile = config.resolve_paper_tuning_profile(
        {
            "TRADING_MODE": "PAPER",
            "PAPER_TUNING_PROFILE_ENABLED": "true",
            "PAPER_AGGRESSIVE_TEST_MODE": "false",
        }
    )

    assert profile == {
        "enabled": True,
        "max_position_percent": 2.0,
        "unknown_sector_max_percent": 20.0,
    }


def test_aggressive_paper_profile_keeps_only_ten_percent_position_cap():
    profile = config.resolve_paper_tuning_profile(
        {
            "TRADING_MODE": "PAPER",
            "PAPER_AGGRESSIVE_TEST_MODE": "true",
            "PAPER_TUNING_MAX_POSITION_PERCENT": "2",
        }
    )

    assert profile == {
        "enabled": True,
        "max_position_percent": 10.0,
        "unknown_sector_max_percent": 100.0,
    }


def test_live_mode_ignores_paper_tuning_profile():
    profile = config.resolve_paper_tuning_profile(
        {
            "TRADING_MODE": "LIVE",
            "PAPER_TUNING_PROFILE_ENABLED": "true",
            "PAPER_TUNING_MAX_POSITION_PERCENT": "5",
            "PAPER_TUNING_UNKNOWN_SECTOR_MAX_PERCENT": "100",
        }
    )

    assert profile == {
        "enabled": False,
        "max_position_percent": 10.0,
        "unknown_sector_max_percent": 10.0,
    }
