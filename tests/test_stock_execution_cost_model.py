from stock_execution_cost_model import StockExecutionCostSettings, estimate_stock_round_trip_costs


def test_long_round_trip_models_slippage_and_sell_fee():
    result = estimate_stock_round_trip_costs(
        entry_notional=10_000,
        exit_notional=11_000,
        direction="long",
        holding_hours=24,
        settings=StockExecutionCostSettings(5, 0.5, 3),
    )
    assert result["estimated_slippage"] == 10.5
    assert result["estimated_regulatory_fees"] == 0.55
    assert result["estimated_borrow_cost"] == 0.0
    assert result["estimated_total_cost"] == 11.05


def test_short_round_trip_adds_time_weighted_borrow_cost():
    result = estimate_stock_round_trip_costs(
        entry_notional=10_000,
        exit_notional=9_000,
        direction="short",
        holding_hours=24 * 365,
        settings=StockExecutionCostSettings(0, 0, 3),
    )
    assert result["estimated_borrow_cost"] == 300.0
    assert result["estimated_total_cost"] == 300.0
