from strategy_execution_policy import StrategyExecutionPolicySettings, evaluate_strategy_execution_policy


SETTINGS = StrategyExecutionPolicySettings(
    minimum_ready_sample=50,
    minimum_out_of_sample_trades=20,
    minimum_expectancy=0,
    minimum_profit_factor=1.2,
    maximum_drawdown_percent=15,
    probation_max_position_percent=10,
    shadow_strategy_ids=("trend_momentum_v1",),
)


def test_losing_legacy_strategy_is_always_shadow_only():
    result = evaluate_strategy_execution_policy(
        "trend_momentum_v1",
        [{"strategy_id": "trend_momentum_v1", "completed_trade_count": 100, "expectancy": 10, "profit_factor": 2, "sample_status": "READY"}],
        settings=SETTINGS,
    )
    assert result["state"] == "SHADOW"
    assert result["execution_allowed"] is False


def test_new_strategy_collects_evidence_at_base_cap():
    result = evaluate_strategy_execution_policy("stock_trend_pullback_v3", [], settings=SETTINGS)
    assert result["state"] == "PROBATION"
    assert result["execution_allowed"] is True
    assert result["max_position_percent"] == 10


def test_ready_positive_strategy_can_unlock_confidence_sizing():
    row = {
        "strategy_id": "stock_trend_pullback_v3",
        "completed_trade_count": 50,
        "expectancy": 5,
        "profit_factor": 1.3,
        "sample_status": "READY",
        "out_of_sample_trade_count": 20,
        "out_of_sample_expectancy": 2,
        "maximum_drawdown_percent": 8,
    }
    result = evaluate_strategy_execution_policy("stock_trend_pullback_v3", [row], settings=SETTINGS)
    assert result["state"] == "ACTIVE"
    assert result["execution_allowed"] is True
    assert result["max_position_percent"] == 100


def test_ready_losing_strategy_returns_to_shadow_mode():
    row = {"strategy_id": "stock_mean_reversion_v2", "completed_trade_count": 50, "expectancy": -1, "profit_factor": 0.8, "sample_status": "READY"}
    result = evaluate_strategy_execution_policy("stock_mean_reversion_v2", [row], settings=SETTINGS)
    assert result["state"] == "SHADOW"
    assert result["execution_allowed"] is False
    assert result["reason"] == "nonpositive_or_weak_ready_sample"


def test_ready_strategy_stays_on_probation_without_positive_oos_evidence():
    row = {
        "strategy_id": "stock_trend_pullback_v3",
        "completed_trade_count": 50,
        "expectancy": 5,
        "profit_factor": 1.4,
        "sample_status": "READY",
        "out_of_sample_trade_count": 10,
        "out_of_sample_expectancy": 2,
        "maximum_drawdown_percent": 8,
    }
    result = evaluate_strategy_execution_policy("stock_trend_pullback_v3", [row], settings=SETTINGS)
    assert result["state"] == "PROBATION"
    assert result["reason"] == "awaiting_positive_out_of_sample_validation"
