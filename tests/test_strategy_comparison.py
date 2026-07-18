from strategy_comparison import leaderboard, pairwise_common_snapshot_comparison, strategy_metrics


def _snapshots():
    return [
        {
            "_run_id": "r1",
            "_observation_date": "2024-01-01",
            "status": "completed",
            "portfolio_return": 0.02,
            "benchmark_return": 0.01,
            "net_portfolio_return": 0.019,
            "net_excess_return": 0.009,
            "gross_excess_return": 0.01,
            "estimated_transaction_cost": 0.001,
            "turnover": 0.2,
            "concentration_metrics": {"hhi": 0.25},
        },
        {
            "_run_id": "r2",
            "_observation_date": "2024-01-02",
            "status": "completed",
            "portfolio_return": -0.01,
            "benchmark_return": -0.02,
            "net_portfolio_return": -0.011,
            "net_excess_return": 0.009,
            "gross_excess_return": 0.01,
            "estimated_transaction_cost": 0.001,
            "turnover": 0.1,
            "concentration_metrics": {"hhi": 0.30},
        },
    ]


def test_strategy_metrics_basic_fields():
    metrics = strategy_metrics(_snapshots(), eligible_candidate_count=10, warnings=[])
    assert metrics["formation_snapshot_count"] == 2
    assert metrics["completed_portfolio_count"] == 2
    assert metrics["average_net_excess_return"] is not None


def test_pairwise_common_snapshot_comparison_returns_rows():
    rows = pairwise_common_snapshot_comparison(
        [
            {"strategy_id": "a", "snapshots": _snapshots()},
            {"strategy_id": "b", "snapshots": _snapshots()},
        ]
    )
    assert len(rows) == 1
    assert rows[0]["common_snapshot_count"] == 2


def test_leaderboard_orders_by_score():
    ranked = leaderboard(
        [
            {"strategy_id": "a", "scorecard": {"composite_score": 40}},
            {"strategy_id": "b", "scorecard": {"composite_score": 80}},
        ]
    )
    assert ranked[0]["strategy_id"] == "b"
