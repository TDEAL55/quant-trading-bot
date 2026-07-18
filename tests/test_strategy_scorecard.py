from strategy_scorecard import build_strategy_scorecard


def test_build_strategy_scorecard_returns_composite():
    metrics = {
        "average_net_excess_return": 0.01,
        "positive_net_excess_rate": 0.6,
        "maximum_drawdown": -0.05,
        "average_turnover": 0.3,
        "average_concentration": 0.25,
        "completed_portfolio_count": 20,
    }
    walk_forward = {
        "completed_windows": 3,
        "validation_average_net_excess_return": 0.005,
        "positive_validation_window_rate": 0.66,
        "degradation_average": -0.001,
        "degradation_volatility": 0.01,
        "performance_decay_flag": False,
        "unstable_window_count": 0,
    }
    quality = {"missing_forward_return": 0, "missing_benchmark_return": 0, "rule_excluded": 1}
    result = build_strategy_scorecard(metrics, walk_forward, quality, min_windows=2)
    assert "composite_score" in result
    assert "overall_status" in result
    assert isinstance(result.get("categories"), list)
