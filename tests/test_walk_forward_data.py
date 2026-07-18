from __future__ import annotations

from walk_forward_data import (
    build_window_analysis,
    compare_training_validation_metrics,
    generate_walk_forward_windows,
    normalize_walk_forward_rows,
)


def _row(candidate_id: int, observation_date: str, status: str, raw_return: float | None, benchmark_return: float | None, excess_return: float | None, score: float = 70.0, confidence: float = 60.0, rank: int = 1, signal: str = "BUY", regime: str = "bull", sector: str = "Technology"):
    return {
        "research_candidate_id": candidate_id,
        "research_run_id": f"research-{candidate_id}",
        "symbol": f"SYM{candidate_id}",
        "observation_date": observation_date,
        "forward_20d_status": status,
        "forward_20d_return": raw_return,
        "forward_20d_benchmark_return": benchmark_return,
        "forward_20d_excess_return": excess_return,
        "overall_score": score,
        "confidence": confidence,
        "rank": rank,
        "signal": signal,
        "market_regime": regime,
        "sector": sector,
    }


def test_normalize_walk_forward_rows_filters_and_deduplicates():
    duplicate_observation = _row(6, "2024-02-03", "complete", 0.02, 0.0, 0.02)
    duplicate_observation["research_run_id"] = "research-dup"
    duplicate_observation["symbol"] = "DUP"
    original_observation = _row(5, "2024-02-03", "complete", 0.01, 0.0, 0.01)
    original_observation["research_run_id"] = "research-dup"
    original_observation["symbol"] = "DUP"
    rows = [
        _row(1, "2024-01-05", "complete", 0.02, 0.01, 0.01),
        _row(1, "2024-01-06", "complete", 0.03, 0.01, 0.02),
        _row(2, "2024-01-07", "pending", 0.01, 0.00, 0.01),
        _row(3, "bad-date", "complete", 0.01, 0.00, 0.01),
        _row(4, "2024-02-02", "complete", None, 0.0, None),
        original_observation,
        duplicate_observation,
    ]
    normalized = normalize_walk_forward_rows(rows, horizon=20)
    assert [row["research_candidate_id"] for row in normalized] == [1, 6]
    assert normalized[0]["selected_excess_return"] == 0.02
    assert normalized[0]["period_start"].isoformat() == "2024-01-01"


def test_generate_walk_forward_windows_supports_rolling_and_expanding():
    rows = []
    candidate_id = 1
    for month in ["2024-01-05", "2024-02-06", "2024-03-07", "2024-04-08", "2024-05-09", "2024-06-10"]:
        rows.append(_row(candidate_id, month, "complete", 0.01, 0.0, 0.01))
        candidate_id += 1
    normalized = normalize_walk_forward_rows(rows, horizon=20)

    rolling = generate_walk_forward_windows(normalized, horizon=20, benchmark_symbol="SPY", window_type="rolling", training_periods=3, validation_periods=1, step_periods=1, min_training_sample=1, min_validation_sample=1)
    expanding = generate_walk_forward_windows(normalized, horizon=20, benchmark_symbol="SPY", window_type="expanding", training_periods=3, validation_periods=1, step_periods=1, min_training_sample=1, min_validation_sample=1)

    assert len(rolling) == 3
    assert rolling[0]["training_start_date"] == "2024-01-01"
    assert rolling[0]["validation_start_date"] == "2024-04-01"
    assert rolling[1]["training_start_date"] == "2024-02-01"
    assert expanding[1]["training_start_date"] == "2024-01-01"
    assert all(window["training_end_date"] < window["validation_start_date"] for window in rolling)


def test_build_window_analysis_and_degradation_metrics_are_exact():
    rows = normalize_walk_forward_rows(
        [
            _row(1, "2024-01-05", "complete", 0.10, 0.02, 0.08, score=80, confidence=75, rank=1),
            _row(2, "2024-01-06", "complete", -0.05, 0.01, -0.06, score=60, confidence=55, rank=3),
            _row(3, "2024-01-07", "complete", 0.00, 0.00, 0.00, score=40, confidence=45, rank=5),
        ],
        horizon=20,
    )
    metrics = build_window_analysis(rows)
    all_candidates = metrics["all_candidates"]

    assert all_candidates["average_raw_return"] == 0.016667
    assert all_candidates["median_raw_return"] == 0.0
    assert all_candidates["average_benchmark_return"] == 0.01
    assert all_candidates["average_excess_return"] == 0.006667
    assert all_candidates["positive_return_rate"] == 0.333333
    assert all_candidates["positive_excess_rate"] == 0.333333
    assert all_candidates["cumulative_return"] == 0.045
    assert all_candidates["maximum_drawdown"] == -0.05

    degraded = compare_training_validation_metrics({"all_candidates": all_candidates}, {"all_candidates": {**all_candidates, "average_excess_return": -0.003333, "positive_excess_rate": 0.0, "sharpe_like_ratio": 0.0, "sortino_like_ratio": 0.0, "average_raw_return": 0.0, "excess_standard_deviation": all_candidates["excess_standard_deviation"]}})
    assert degraded["average_excess_return"]["validation_degradation"] == -0.01
    assert degraded["average_excess_return"]["relative_degradation"] == -1.499925


def test_generate_walk_forward_windows_skips_insufficient_samples():
    normalized = normalize_walk_forward_rows([_row(1, "2024-01-05", "complete", 0.01, 0.0, 0.01)], horizon=20)
    windows = generate_walk_forward_windows(normalized, horizon=20, benchmark_symbol="SPY", window_type="rolling", training_periods=1, validation_periods=1, step_periods=1, min_training_sample=2, min_validation_sample=2)
    assert windows == []