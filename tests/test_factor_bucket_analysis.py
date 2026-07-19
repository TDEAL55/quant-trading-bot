from factor_bucket_analysis import compute_bucket_statistics


def _rows():
    data = []
    for idx in range(1, 21):
        data.append(
            {
                "factor_id": "f1",
                "factor_version": "v1",
                "candidate_id": idx,
                "factor_value": float(idx),
                "forward_20d_return": idx / 1000.0,
                "forward_20d_excess_return": idx / 1000.0 - 0.001,
            }
        )
    return data


def test_deterministic_bucket_assignments():
    rows = _rows()
    first = compute_bucket_statistics(rows, 20, 10, 10)
    second = compute_bucket_statistics(rows, 20, 10, 10)
    assert first == second


def test_top_minus_bottom_spread_and_monotonicity():
    rows = _rows()
    result = compute_bucket_statistics(rows, 20, 5, 5)
    one = [row for row in result if row["bucket_number"] == 1][0]
    assert one["top_minus_bottom_spread"] is not None
    assert one["monotonicity_score"] is not None


def test_small_sample_fallback():
    rows = _rows()[:6]
    result = compute_bucket_statistics(rows, 20, 10, 5)
    assert max(row["bucket_count"] for row in result) < 10
