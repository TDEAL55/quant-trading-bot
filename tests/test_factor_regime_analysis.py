from factor_regime_analysis import compute_regime_statistics


def _rows():
    rows = []
    for idx in range(1, 15):
        rows.append(
            {
                "factor_id": "f1",
                "factor_version": "v1",
                "factor_value": float(idx),
                "market_regime": "strong_bull" if idx % 2 == 0 else "strong_bear",
                "forward_20d_return": float(idx) / 100.0,
                "forward_20d_excess_return": float(idx) / 100.0 - 0.01,
            }
        )
    return rows


def test_unknown_regime_retained():
    result = compute_regime_statistics(_rows(), {"f1": "higher_is_better"}, 20, 5)
    regimes = {row["regime_label"] for row in result}
    assert "unknown" in regimes


def test_low_sample_marked_insufficient():
    result = compute_regime_statistics(_rows()[:2], {"f1": "higher_is_better"}, 20, 5)
    assert any(row["status"] == "insufficient_data" for row in result)
