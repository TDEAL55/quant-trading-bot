from pathlib import Path

import paper_test_profile
from deployment_config import load_deployment_config


def test_aggressive_profile_only_applies_to_paper():
    assert paper_test_profile.aggressive_paper_test_enabled(
        {"TRADING_MODE": "PAPER", "PAPER_AGGRESSIVE_TEST_MODE": "true"},
        allow_marker=False,
    ) is True
    assert paper_test_profile.aggressive_paper_test_enabled(
        {"TRADING_MODE": "LIVE", "PAPER_AGGRESSIVE_TEST_MODE": "true"},
        allow_marker=False,
    ) is False


def test_explicit_false_disables_aggressive_profile():
    assert paper_test_profile.aggressive_paper_test_enabled(
        {"TRADING_MODE": "PAPER", "PAPER_AGGRESSIVE_TEST_MODE": "false"},
        allow_marker=True,
    ) is False


def test_deployment_config_uses_aggressive_limits_without_enabling_live(tmp_path):
    config = load_deployment_config(
        {
            "APP_ENV": "test",
            "DATABASE_URL": f"sqlite:///{Path(tmp_path) / 'paper.db'}",
            "TRADING_MODE": "PAPER",
            "PAPER_BROKER_BACKEND": "SIMULATED",
            "PAPER_AGGRESSIVE_TEST_MODE": "true",
            "MAX_DAILY_ORDERS": "5",
            "MAX_OPEN_POSITIONS": "10",
            "MAX_POSITION_EQUITY_PERCENT": "5",
        }
    )

    assert config.trading_mode == "PAPER"
    assert config.max_daily_orders == paper_test_profile.AGGRESSIVE_MAX_DAILY_ORDERS
    assert config.max_open_positions == paper_test_profile.AGGRESSIVE_MAX_OPEN_POSITIONS
    assert config.max_position_equity_percent == 10.0
