from strategy_lab_data import _common_snapshot_keys, apply_strategy_filters
from strategy_definitions import StrategyDefinition


def test_apply_strategy_filters_by_signal_and_threshold():
    definition = StrategyDefinition(
        strategy_id="test",
        strategy_name="Test",
        description="",
        version="1",
        enabled=True,
        filter_rules={"required_signals": ["BUY"], "min_overall_score": 70},
        ranking_convention="lower_rank_is_better",
        portfolio_configuration={},
        supported_horizons=[20],
        created_at="2024-01-01T00:00:00+00:00",
        configuration_fingerprint="fp",
    )
    rows = [
        {"signal": "BUY", "overall_score": 80, "_observation_date": "2024-01-01", "_rank": 1, "_symbol": "AAA"},
        {"signal": "SELL", "overall_score": 90, "_observation_date": "2024-01-01", "_rank": 2, "_symbol": "BBB"},
        {"signal": "BUY", "overall_score": 50, "_observation_date": "2024-01-01", "_rank": 3, "_symbol": "CCC"},
    ]
    result = apply_strategy_filters(rows, definition)
    assert len(result["rows"]) == 1


def test_common_snapshot_keys_respects_min_holdings():
    filtered = {
        "a": [
            {"_run_id": "r1", "_observation_date": "2024-01-01"},
            {"_run_id": "r1", "_observation_date": "2024-01-01"},
            {"_run_id": "r2", "_observation_date": "2024-01-02"},
        ],
        "b": [
            {"_run_id": "r1", "_observation_date": "2024-01-01"},
            {"_run_id": "r1", "_observation_date": "2024-01-01"},
            {"_run_id": "r3", "_observation_date": "2024-01-03"},
        ],
    }
    keys = _common_snapshot_keys(filtered, min_holdings=2)
    assert ("r1", "2024-01-01") in keys
    assert ("r2", "2024-01-02") not in keys
