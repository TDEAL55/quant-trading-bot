from strategy_execution_policy import StrategyExecutionPolicySettings, evaluate_strategy_execution_policy


SETTINGS = StrategyExecutionPolicySettings(
    minimum_ready_sample=30,
    minimum_expectancy=0,
    minimum_profit_factor=1.2,
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
    result = evaluate_strategy_execution_policy("stock_trend_ensemble_v2", [], settings=SETTINGS)
    assert result["state"] == "PROBATION"
    assert result["execution_allowed"] is True
    assert result["max_position_percent"] == 10


def test_ready_positive_strategy_can_unlock_confidence_sizing():
    row = {"strategy_id": "stock_trend_ensemble_v2", "completed_trade_count": 30, "expectancy": 5, "profit_factor": 1.3, "sample_status": "READY"}
    result = evaluate_strategy_execution_policy("stock_trend_ensemble_v2", [row], settings=SETTINGS)
    assert result["state"] == "ACTIVE"
    assert result["execution_allowed"] is True
    assert result["max_position_percent"] == 100


def test_ready_losing_strategy_returns_to_shadow_mode():
    row = {"strategy_id": "stock_mean_reversion_v2", "completed_trade_count": 30, "expectancy": -1, "profit_factor": 0.8, "sample_status": "READY"}
    result = evaluate_strategy_execution_policy("stock_mean_reversion_v2", [row], settings=SETTINGS)
    assert result["state"] == "SHADOW"
    assert result["execution_allowed"] is False
    assert result["reason"] == "nonpositive_or_weak_ready_sample"
