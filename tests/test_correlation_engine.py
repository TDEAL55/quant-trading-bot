from correlation_engine import (
    INSUFFICIENT_DATA,
    CorrelationPolicy,
    assess_symbol_correlation,
    calculate_pair_correlation,
    summarize_portfolio_correlation,
)


def _series(start=100.0, step=1.0, days=90):
    return [{"date": f"2026-01-{idx + 1:02d}", "close": start + step * idx} for idx in range(days)]


def test_aligned_return_correlation_basic():
    prices = {
        "AAA": _series(100.0, 1.0, 95),
        "BBB": _series(50.0, 0.5, 95),
    }
    row = calculate_pair_correlation("AAA", "BBB", prices, CorrelationPolicy(lookback_days=90, min_overlap_days=40))
    assert row["status"] == "OK"
    assert row["correlation"] is not None
    assert row["correlation"] > 0.95


def test_insufficient_data_status():
    prices = {
        "AAA": _series(100.0, 1.0, 20),
        "BBB": _series(100.0, -1.0, 20),
    }
    row = calculate_pair_correlation("AAA", "BBB", prices, CorrelationPolicy(lookback_days=20, min_overlap_days=40))
    assert row["status"] == INSUFFICIENT_DATA
    assert row["correlation"] is None


def test_high_correlation_detection_deterministic():
    prices = {
        "AAA": _series(100.0, 1.0, 100),
        "BBB": _series(120.0, 1.2, 100),
        "CCC": _series(90.0, 0.1, 100),
    }
    first = assess_symbol_correlation("AAA", ["BBB", "CCC"], prices)
    second = assess_symbol_correlation("AAA", ["CCC", "BBB"], prices)
    assert first["maximum_correlation"] == second["maximum_correlation"]
    assert first["pair_details"] == second["pair_details"]


def test_portfolio_correlation_summary_includes_pairs():
    prices = {
        "AAA": _series(100.0, 1.0, 100),
        "BBB": _series(120.0, 1.2, 100),
        "CCC": _series(80.0, -0.1, 100),
    }
    summary = summarize_portfolio_correlation(["AAA", "BBB", "CCC"], prices)
    assert summary["pair_count"] == 3
    assert summary["status"] == "OK"
