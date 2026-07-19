from factor_intelligence_data_quality import evaluate_alignment_quality


def test_quality_counts_invalid_and_missing():
    rows = [
        {
            "observation_id": "1",
            "factor_id": "a",
            "factor_version": "v1",
            "value_status": "missing",
            "observation_timestamp": "2024-01-01",
            "universe_size": 10,
            "forward_20d_status": "complete",
        },
        {
            "observation_id": "2",
            "factor_id": "a",
            "factor_version": "v1",
            "value_status": "valid",
            "observation_timestamp": "2024-01-01",
            "universe_size": 1,
            "forward_20d_status": "complete",
        },
    ]
    out = evaluate_alignment_quality(rows, 20, {("a", "v1")}, 2)
    assert out["missing_value_count"] == 1
    assert out["excluded_rows"] == 2
