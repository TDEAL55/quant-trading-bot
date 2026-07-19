from security_factor_explainability import build_security_explanation


def test_contributions_reconcile_to_score():
    rows = [
        {
            "factor_id": "a",
            "factor_version": "v1",
            "factor_value": 10,
            "normalized_value": 0.5,
            "percentile_rank": 0.9,
            "direction": "higher_is_better",
            "name": "A",
        },
        {
            "factor_id": "b",
            "factor_version": "v1",
            "factor_value": 5,
            "normalized_value": -0.2,
            "percentile_rank": 0.4,
            "direction": "higher_is_better",
            "name": "B",
        },
    ]
    out = build_security_explanation("AAA", "r1:2024-01-01", rows, {"a": 0.6, "b": 0.4}, 20, 1)
    assert out["score_calculation_reconciliation"]["within_tolerance"] is True
    assert out["positive_contributors"][0]["factor_id"] == "a"
