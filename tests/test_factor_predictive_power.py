from factor_predictive_power import compute_predictive_power


def _rows(values, returns):
    out = []
    for idx, (value, ret) in enumerate(zip(values, returns), start=1):
        out.append(
            {
                "factor_id": "f1",
                "factor_version": "v1",
                "factor_value": value,
                "candidate_id": idx,
                "snapshot_id": "s1",
                "forward_20d_return": ret,
                "forward_20d_excess_return": ret - 0.01,
            }
        )
    return out


def test_positive_monotonic_factor():
    rows = _rows([1, 2, 3, 4, 5, 6], [0.01, 0.02, 0.03, 0.04, 0.05, 0.06])
    result = compute_predictive_power(rows, 20, 5, "2024-01-01", "2024-01-31")
    assert result[0]["spearman_correlation"] == 1.0
    assert result[0]["top_minus_bottom_spread"] > 0


def test_constant_factor_is_safe():
    rows = _rows([1, 1, 1, 1, 1, 1], [0.01, -0.01, 0.01, -0.01, 0.01, -0.01])
    result = compute_predictive_power(rows, 20, 5, None, None)
    assert result[0]["pearson_correlation"] is None
    assert result[0]["spearman_correlation"] is None


def test_insufficient_sample_status():
    rows = _rows([1, 2], [0.1, -0.1])
    result = compute_predictive_power(rows, 20, 5, None, None)
    assert result[0]["status"] == "insufficient_data"
