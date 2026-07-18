from __future__ import annotations

from stability_analyzer import (
    aggregate_factor_stability,
    analyze_factor_stability,
    analyze_performance_decay,
    analyze_regime_robustness,
    build_validation_scorecard,
)


def _row(score: float, trend: float, signal: str, regime: str, sector: str, rank: int, one_day: float, excess: float) -> dict[str, object]:
    return {
        "overall_score": score,
        "confidence": score,
        "trend_score": trend,
        "momentum_score": trend,
        "volume_score": trend,
        "volatility_score": trend,
        "liquidity_score": trend,
        "market_regime_score": trend,
        "risk_quality_score": trend,
        "rank": rank,
        "signal": signal,
        "market_regime": regime,
        "sector": sector,
        "forward_1d_status": "complete",
        "forward_1d_return": one_day,
        "forward_1d_excess_return": excess,
        "forward_5d_status": "complete",
        "forward_5d_return": one_day,
        "forward_5d_excess_return": excess,
        "forward_10d_status": "complete",
        "forward_10d_return": one_day,
        "forward_10d_excess_return": excess,
        "forward_20d_status": "complete",
        "forward_20d_return": one_day,
        "forward_20d_excess_return": excess,
    }


def test_factor_stability_handles_stable_and_sign_reversing_factors():
    training_rows = [_row(20, 10, "BUY", "bull", "Tech", 1, 0.01, 0.01), _row(40, 20, "BUY", "bull", "Tech", 2, 0.02, 0.02), _row(60, 30, "BUY", "bull", "Tech", 3, 0.03, 0.03)]
    validation_rows = [_row(20, 10, "BUY", "bull", "Tech", 1, -0.01, -0.01), _row(40, 20, "BUY", "bull", "Tech", 2, -0.02, -0.02), _row(60, 30, "BUY", "bull", "Tech", 3, -0.03, -0.03)]
    stability = analyze_factor_stability(training_rows, validation_rows)
    overall = next(row for row in stability if row["factor"] == "overall_score")
    assert overall["correlation_sign_consistency"] is False
    aggregated = aggregate_factor_stability([stability])
    assert aggregated[0]["window_count"] == 1


def test_performance_decay_and_regime_robustness_are_deterministic():
    windows = [
        {"status": "completed", "validation_metrics": {"all_candidates": {"average_excess_return": 0.03}, "market_regimes": [{"market_regime": "bull", "observation_count": 4, "average_raw_return": 0.02, "average_excess_return": 0.03, "positive_excess_rate": 1.0, "excess_standard_deviation": 0.01, "sharpe_like_ratio": 3.0}]}, "warnings": []},
        {"status": "completed", "validation_metrics": {"all_candidates": {"average_excess_return": 0.01}, "market_regimes": [{"market_regime": "bear", "observation_count": 2, "average_raw_return": -0.01, "average_excess_return": -0.02, "positive_excess_rate": 0.0, "excess_standard_deviation": 0.02, "sharpe_like_ratio": -1.0}]}, "warnings": []},
        {"status": "completed", "validation_metrics": {"all_candidates": {"average_excess_return": -0.02}, "market_regimes": [{"market_regime": "neutral", "observation_count": 6, "average_raw_return": 0.0, "average_excess_return": 0.0, "positive_excess_rate": 0.5, "excess_standard_deviation": 0.01, "sharpe_like_ratio": 0.0}]}, "warnings": []},
    ]
    decay = analyze_performance_decay(windows, minimum_windows=3)
    assert decay["performance_decay_flag"] is True
    assert decay["difference"] < 0
    regimes = analyze_regime_robustness(windows)
    bear = next(row for row in regimes if row["market_regime"] == "bear")
    assert "sparse regime sample" in bear["warnings"]


def test_scorecard_statuses_are_transparent():
    windows = [
        {"status": "completed", "warnings": [], "validation_metrics": {"all_candidates": {"average_excess_return": 0.02}}, "degradation_metrics": {"average_excess_return": {"validation_degradation": -0.005}}},
        {"status": "completed", "warnings": [], "validation_metrics": {"all_candidates": {"average_excess_return": 0.01}}, "degradation_metrics": {"average_excess_return": {"validation_degradation": -0.002}}},
    ]
    factor_summary = [{"factor": "trend_score", "stability_percentage": 0.8}]
    decay = {"trend_slope": -0.001, "performance_decay_flag": False, "explanation": "no material performance decay detected"}
    regime_robustness = [{"market_regime": "bull", "average_validation_excess_return": 0.02}]
    scorecard = build_validation_scorecard(windows, factor_summary, decay, regime_robustness)
    assert scorecard["overall_validation_status"] in {"strong", "acceptable", "unstable", "insufficient_data"}
    assert any(row["category"] == "Factor stability" for row in scorecard["categories"])