from portfolio_analytics import aggregate_portfolio_metrics, calculate_snapshot_returns, calculate_turnover, concentration_metrics


def test_concentration_metrics_hhi_effective_holdings_and_top_weights():
    weights = {"AAA": 0.5, "BBB": 0.3, "CCC": 0.2}
    sector_weights = {"Tech": 0.8, "Energy": 0.2}
    metrics = concentration_metrics(weights, sector_weights, cash_weight=0.0)
    assert metrics["hhi"] == 0.38
    assert round(metrics["effective_holdings"], 6) == round(1 / 0.38, 6)
    assert metrics["top_1_weight"] == 0.5
    assert metrics["top_3_weight"] == 1.0
    assert metrics["largest_sector_weight"] == 0.8


def test_turnover_unchanged_full_replacement_partial_and_cash_included():
    unchanged = calculate_turnover({"AAA": 0.5, "BBB": 0.5, "CASH": 0.0}, {"AAA": 0.5, "BBB": 0.5, "CASH": 0.0})
    replaced = calculate_turnover({"AAA": 1.0, "CASH": 0.0}, {"BBB": 1.0, "CASH": 0.0})
    partial = calculate_turnover({"AAA": 0.6, "BBB": 0.4, "CASH": 0.0}, {"AAA": 0.3, "BBB": 0.4, "CCC": 0.1, "CASH": 0.2})

    assert unchanged == 0.0
    assert replaced == 1.0
    assert partial == 0.3


def test_snapshot_return_and_attribution_calculation():
    holdings = [
        {"symbol": "AAA", "weight": 0.6, "forward_return": 0.10, "benchmark_return": 0.05, "excess_return": 0.05, "rank": 1, "overall_score": 80, "confidence": 70, "sector": "Tech", "signal": "BUY", "market_regime": "bull"},
        {"symbol": "BBB", "weight": 0.4, "forward_return": -0.05, "benchmark_return": 0.00, "excess_return": -0.05, "rank": 2, "overall_score": 60, "confidence": 55, "sector": "Energy", "signal": "HOLD", "market_regime": "neutral"},
    ]
    snapshot = calculate_snapshot_returns(
        formation_date="2024-01-01",
        research_run_id="r1",
        holdings=holdings,
        benchmark_symbol="SPY",
        cash_weight=0.0,
        turnover=0.2,
        warnings=[],
        status="completed",
    )
    assert snapshot["portfolio_return"] == 0.04
    assert snapshot["benchmark_return"] == 0.03
    assert snapshot["excess_return"] == 0.01
    assert round(sum(item["return_contribution"] for item in snapshot["holdings"]), 6) == 0.04


def test_aggregate_metrics_empty_and_non_empty_samples():
    empty = aggregate_portfolio_metrics([])
    assert empty["portfolio_count"] == 0
    assert empty["average_portfolio_return"] is None

    snapshots = [
        {"formation_date": "2024-01-01", "research_run_id": "r1", "status": "completed", "portfolio_return": 0.1, "benchmark_return": 0.05, "excess_return": 0.05, "holding_count": 2, "cash_weight": 0.0, "turnover": None, "concentration_metrics": {"hhi": 0.5, "largest_sector_weight": 0.6}, "maximum_position": 0.6},
        {"formation_date": "2024-02-01", "research_run_id": "r2", "status": "completed", "portfolio_return": -0.05, "benchmark_return": -0.01, "excess_return": -0.04, "holding_count": 3, "cash_weight": 0.2, "turnover": 0.3, "concentration_metrics": {"hhi": 0.34, "largest_sector_weight": 0.5}, "maximum_position": 0.5},
    ]
    metrics = aggregate_portfolio_metrics(snapshots)
    assert metrics["portfolio_count"] == 2
    assert metrics["average_portfolio_return"] == 0.025
    assert metrics["median_portfolio_return"] == 0.025
    assert metrics["positive_return_rate"] == 0.5
    assert metrics["average_cash_weight"] == 0.1
