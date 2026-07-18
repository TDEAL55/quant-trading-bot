from portfolio_research_data import (
    build_formation_snapshots,
    build_walk_forward_portfolio_validation,
    normalize_portfolio_research_rows,
    run_method_comparison,
    run_portfolio_method,
)


def _rows():
    base = []
    template = [
        ("run-1", "2024-01-05", "AAA", 1, 80.0, 70.0, "Tech", "BUY", "bull", 0.10, 0.05, 0.05, 0.2),
        ("run-1", "2024-01-05", "BBB", 2, 60.0, 50.0, "Energy", "BUY", "bull", -0.05, -0.01, -0.04, 0.4),
        ("run-2", "2024-02-06", "AAA", 1, 75.0, 68.0, "Tech", "BUY", "neutral", 0.08, 0.03, 0.05, 0.2),
        ("run-2", "2024-02-06", "CCC", 2, 55.0, 52.0, "Health", "HOLD", "neutral", 0.01, 0.00, 0.01, 0.3),
        ("run-3", "2024-03-07", "DDD", 1, 50.0, 45.0, "Energy", "SELL", "bear", -0.04, -0.02, -0.02, 0.5),
        ("run-3", "2024-03-07", "EEE", 2, 40.0, 42.0, "Tech", "BUY", "bear", 0.02, 0.01, 0.01, 0.6),
        ("run-4", "2024-04-08", "FFF", 1, 90.0, 85.0, "Tech", "BUY", "bull", 0.06, 0.02, 0.04, 0.2),
        ("run-4", "2024-04-08", "GGG", 2, 65.0, 60.0, "Utilities", "BUY", "bull", 0.03, 0.01, 0.02, 0.35),
    ]
    for run_id, date_str, symbol, rank, score, confidence, sector, signal, regime, raw, bench, excess, vol in template:
        row = {
            "research_run_id": run_id,
            "observation_date": date_str,
            "symbol": symbol,
            "rank": rank,
            "overall_score": score,
            "confidence": confidence,
            "sector": sector,
            "signal": signal,
            "market_regime": regime,
            "volatility_measure": vol,
            "forward_20d_status": "complete",
            "forward_20d_return": raw,
            "forward_20d_benchmark_return": bench,
            "forward_20d_excess_return": excess,
        }
        base.append(row)
    return base


def test_normalization_filters_duplicates_and_missing_values():
    rows = _rows()
    rows.append(dict(rows[0]))
    rows.append({"research_run_id": "run-9", "observation_date": "bad-date", "symbol": "ZZZ", "forward_20d_status": "complete", "forward_20d_return": 0.01, "forward_20d_benchmark_return": 0.0, "forward_20d_excess_return": 0.01})
    rows.append({"research_run_id": "run-9", "observation_date": "2024-05-01", "symbol": "YYY", "forward_20d_status": "pending"})

    normalized = normalize_portfolio_research_rows(rows, horizon=20)
    assert len(normalized["rows"]) == 8
    assert normalized["warnings"]["duplicate_candidates"] == 1
    assert normalized["warnings"]["malformed_dates"] == 1
    assert normalized["warnings"]["missing_labels"] == 1


def test_portfolio_method_and_comparison_are_deterministic():
    normalized = normalize_portfolio_research_rows(_rows(), horizon=20)["rows"]
    snapshots = build_formation_snapshots(normalized)
    assert len(snapshots) == 4

    equal = run_portfolio_method(normalized, method="equal_weight", horizon=20, top_n=2)
    score = run_portfolio_method(normalized, method="score_proportional", horizon=20, top_n=2)
    assert equal["portfolio_count"] == 4
    assert score["portfolio_count"] == 4
    assert equal["snapshots"][0]["holding_count"] == 2

    comparison = run_method_comparison(normalized, methods=["equal_weight", "score_proportional"], horizon=20, top_n=2, benchmark_symbol="SPY")
    table = comparison["comparison_table"]
    assert len(table) == 2
    assert {row["method"] for row in table} == {"equal_weight", "score_proportional"}


def test_walk_forward_portfolio_validation_no_leakage_shape():
    normalized = normalize_portfolio_research_rows(_rows(), horizon=20)["rows"]
    result = build_walk_forward_portfolio_validation(
        normalized,
        method="equal_weight",
        horizon=20,
        benchmark_symbol="SPY",
        top_n=2,
        window_type="rolling",
        training_periods=2,
        validation_periods=1,
        step_periods=1,
    )
    assert len(result["windows"]) >= 1
    first = result["windows"][0]
    assert "training_portfolio_excess_return" in first
    assert "validation_portfolio_excess_return" in first
    assert "degradation" in first
