from performance_comparison import build_performance_comparison


def test_comparison_does_not_make_allocation_decisions_from_small_samples():
    result = build_performance_comparison(
        backtest={"metrics": {"number_of_trades": 100, "expectancy_per_trade": 1.2}},
        paper={"metrics": {"number_of_trades": 5, "expectancy_per_trade": -0.3}},
        live={"metrics": {"number_of_trades": 2, "expectancy_per_trade": -7.5}},
    )
    assert result["comparison_ready"] is False
    assert result["allocation_changed"] is False
    assert "one_or_more_samples_below_minimum" in result["flags"]


def test_comparison_flags_expectancy_sign_divergence_only_with_sufficient_samples():
    result = build_performance_comparison(
        backtest={"metrics": {"number_of_trades": 40, "expectancy_per_trade": 1.2}},
        paper={"metrics": {"number_of_trades": 35, "expectancy_per_trade": 0.1}},
        live={"metrics": {"number_of_trades": 30, "expectancy_per_trade": -0.2}},
    )
    assert result["comparison_ready"] is True
    assert "expectancy_sign_differs_across_environments" in result["flags"]


def test_strategy_comparison_keeps_missing_environments_explicit():
    result = build_performance_comparison(
        backtest={"breakdowns": {"by_strategy": [{"strategy": "trend", "trade_count": 40, "pnl": 20}]}},
        paper={},
        live={"breakdowns": {"by_strategy": [{"strategy": "trend", "trade_count": 2, "pnl": -5}]}},
    )
    rows = [row for row in result["strategy_rows"] if row["strategy"] == "trend"]
    assert [row["source"] for row in rows] == ["backtest", "paper", "live"]
    assert rows[1]["available"] is False
    assert rows[2]["sample_sufficient"] is False
