from __future__ import annotations

from strategy_profitability import allocate_equal_risk, build_strategy_leaderboard, paused_strategies_from_drawdown


def _trade(strategy_id, strategy_version, net_pnl, pct_return, regime="bull", hold_hours=24.0):
    return {
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "net_pnl": net_pnl,
        "percentage_return": pct_return,
        "market_regime": regime,
        "holding_duration_hours": hold_hours,
    }


def test_leaderboard_separates_strategies_and_flags_small_samples():
    trades = [
        _trade("trend_momentum", "1.0.0", 100.0, 0.05),
        _trade("trend_momentum", "1.0.0", -20.0, -0.01),
        _trade("ma_trend_follow", "1.0.0", 50.0, 0.02),
    ]
    board = build_strategy_leaderboard(trades)

    assert len(board) == 2
    by_id = {row["strategy_id"]: row for row in board}
    assert by_id["trend_momentum"]["net_profit"] == 80.0
    assert by_id["ma_trend_follow"]["net_profit"] == 50.0
    assert by_id["trend_momentum"]["sample_status"] == "INSUFFICIENT_SAMPLE"


def test_equal_risk_allocation_and_drawdown_pause():
    allocations = allocate_equal_risk(["a", "b", "c", "d"])
    assert set(allocations.keys()) == {"a", "b", "c", "d"}
    assert allocations["a"] == allocations["b"] == allocations["c"] == allocations["d"]

    paused = paused_strategies_from_drawdown(
        [
            {"strategy_id": "a", "maximum_drawdown": 0.10},
            {"strategy_id": "b", "maximum_drawdown": 0.25},
        ],
        max_drawdown_threshold=0.20,
    )
    assert paused == ["b"]


def test_drawdown_is_a_fraction_even_when_first_trade_loses():
    board = build_strategy_leaderboard(
        [
            _trade("stock_trend_ensemble_v2", "2.0.0", -100.0, -0.10),
            _trade("stock_trend_ensemble_v2", "2.0.0", 25.0, 0.03),
        ]
    )
    assert board[0]["maximum_drawdown"] == 0.10
