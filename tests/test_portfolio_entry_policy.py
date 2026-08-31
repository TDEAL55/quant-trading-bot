from portfolio_entry_policy import PortfolioEntryPolicySettings, evaluate_portfolio_entry_policy


def test_overextended_account_is_exit_only_without_automatic_liquidation():
    result = evaluate_portfolio_entry_policy(
        {"equity": 100_000, "cash": -5_000},
        {
            "JPM": {"quantity": 400, "current_price": 200, "asset_class": "us_equity"},
            "MCO": {"quantity": 80, "current_price": 500, "asset_class": "us_equity"},
            "SBLK": {"quantity": -200, "current_price": 50, "asset_class": "us_equity"},
        },
        settings=PortfolioEntryPolicySettings(
            maximum_gross_exposure_percent=125,
            maximum_open_stock_positions=15,
            minimum_cash=0,
            normalization_position_percent=10,
        ),
    )

    assert result["exit_only"] is True
    assert result["new_entries_allowed"] is False
    assert result["gross_exposure_percent"] == 130.0
    assert result["reasons"] == ["cash_below_entry_floor", "gross_exposure_above_limit"]
    assert result["automatic_liquidation_enabled"] is False
    assert [row["symbol"] for row in result["normalization_candidates"]] == ["JPM", "MCO"]


def test_healthy_account_allows_entries_and_ignores_non_stock_positions():
    result = evaluate_portfolio_entry_policy(
        {"equity": 100_000, "cash": 30_000},
        {
            "AAPL": {"quantity": 100, "current_price": 100, "asset_class": "us_equity"},
            "BTC/USD": {"quantity": 1, "current_price": 100_000, "asset_class": "crypto"},
        },
    )

    assert result["new_entries_allowed"] is True
    assert result["gross_exposure_percent"] == 10.0
    assert result["open_stock_positions"] == 1


def test_too_many_small_positions_is_exit_only():
    positions = {
        f"S{index}": {"quantity": 1, "current_price": 100, "asset_class": "us_equity"}
        for index in range(16)
    }
    result = evaluate_portfolio_entry_policy({"equity": 100_000, "cash": 50_000}, positions)
    assert result["exit_only"] is True
    assert "open_stock_positions_above_limit" in result["reasons"]
