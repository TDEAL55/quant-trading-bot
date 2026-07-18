from __future__ import annotations

from factor_attribution import build_factor_attribution_analytics, fetch_factor_attribution_dashboard_payload
from evaluation_repository import MonitoringEvaluationRepository


def _row(
    symbol: str,
    score: float,
    confidence: float,
    trend: float,
    momentum: float,
    volume: float,
    volatility: float,
    liquidity: float,
    regime_score: float,
    risk_quality: float,
    rank: int,
    signal: str,
    regime: str,
    sector: str,
    one_day: float,
    excess_one_day: float,
    twenty_day: float | None,
    excess_twenty_day: float | None,
    twenty_status: str = "complete",
) -> dict[str, object]:
    row = {
        "symbol": symbol,
        "overall_score": score,
        "confidence": confidence,
        "trend_score": trend,
        "momentum_score": momentum,
        "volume_score": volume,
        "volatility_score": volatility,
        "liquidity_score": liquidity,
        "market_regime_score": regime_score,
        "risk_quality_score": risk_quality,
        "rank": rank,
        "signal": signal,
        "market_regime": regime,
        "sector": sector,
        "forward_1d_status": "complete",
        "forward_1d_return": one_day,
        "forward_1d_excess_return": excess_one_day,
        "forward_5d_status": "complete",
        "forward_5d_return": one_day * 2,
        "forward_5d_excess_return": excess_one_day * 2,
        "forward_10d_status": "complete",
        "forward_10d_return": one_day * 3,
        "forward_10d_excess_return": excess_one_day * 3,
        "forward_20d_status": twenty_status,
        "forward_20d_return": twenty_day,
        "forward_20d_excess_return": excess_twenty_day,
        "label_status": "partial" if twenty_status != "complete" else "complete",
    }
    return row


def test_build_factor_attribution_analytics_returns_exact_core_statistics():
    rows = [
        _row("AAA", 20, 30, 15, 40, 50, 60, 70, 30, 20, 10, "HOLD", "bear", "Utilities", -0.02, -0.01, -0.01, -0.005),
        _row("BBB", 40, 35, 30, 45, 50, 60, 70, 35, 25, 8, "BUY", "neutral", "Energy", 0.00, 0.00, 0.01, 0.005),
        _row("CCC", 60, 50, 45, 55, 50, 60, 70, 50, 40, 5, "BUY", "strong_bull", "Technology", 0.01, 0.01, 0.03, 0.02),
        _row("DDD", 80, 65, 60, 65, 50, 60, 70, 65, 55, 3, "BUY", "strong_bull", "Technology", 0.02, 0.02, 0.05, 0.04),
        _row("EEE", 100, 80, 75, 75, 50, 60, 70, 80, 70, 1, "STRONG_BUY", "strong_bull", "Technology", 0.03, 0.03, 0.07, 0.06),
    ]

    analytics = build_factor_attribution_analytics(rows)

    overall_distribution = analytics["factor_distributions"]["overall_score"]
    overall_correlations = next(row for row in analytics["factor_correlations"] if row["factor"] == "overall_score")
    overall_buckets = analytics["factor_bucket_analysis"]["overall_score"]["1d"]

    assert overall_distribution["sample_size"] == 5
    assert overall_distribution["mean"] == 60.0
    assert overall_distribution["median"] == 60.0
    assert overall_correlations["1d_return_correlation"] > 0.98
    assert overall_correlations["1d_excess_correlation"] == 1.0
    assert analytics["strongest_predictive_factors"][0]["factor"] == "overall_score"
    assert overall_buckets[0]["bucket"] == "20_39"
    assert overall_buckets[-1]["bucket"] == "80_100"
    assert analytics["top_factor_combinations"]["1d"] == []
    assert analytics["minimum_sample_warnings"]


def test_factor_attribution_detects_diminishing_returns_and_combinations():
    rows = [
        _row("AAA", 82, 82, 20, 40, 50, 60, 70, 50, 40, 1, "BUY", "bull", "Technology", 0.01, 0.01, 0.02, 0.02),
        _row("BBB", 84, 84, 40, 45, 50, 60, 70, 52, 42, 2, "BUY", "bull", "Technology", 0.03, 0.03, 0.05, 0.05),
        _row("CCC", 86, 86, 60, 50, 50, 60, 70, 54, 44, 3, "BUY", "bull", "Technology", 0.04, 0.04, 0.06, 0.06),
        _row("DDD", 88, 88, 80, 55, 50, 60, 70, 56, 46, 4, "BUY", "bull", "Technology", 0.02, 0.02, 0.03, 0.03),
        _row("EEE", 90, 90, 95, 60, 50, 60, 70, 58, 48, 5, "BUY", "bull", "Technology", 0.01, 0.01, None, None, twenty_status="pending"),
    ]

    analytics = build_factor_attribution_analytics(rows, minimum_sample_size=1, combination_min_sample_size=2)
    trend_summary = next(row for row in analytics["feature_importance_summary"] if row["factor"] == "trend_score")
    combos = analytics["top_factor_combinations"]["1d"]

    assert trend_summary["diminishing_returns_detected"] is True
    assert combos
    assert combos[0]["sample_size"] >= 2


def test_empty_factor_attribution_dashboard_payload_returns_safe_defaults(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'factor_attribution.db'}"
    repo = MonitoringEvaluationRepository(database_url=db_url)
    repo.db.ensure_schema()
    payload = fetch_factor_attribution_dashboard_payload(db_url)
    assert payload["factor_attribution_analytics"]["feature_importance_summary"] == []
    assert payload["factor_attribution_analytics"]["factor_correlations"] == []
    assert payload["factor_attribution_analytics"]["top_factor_combinations"] == {}
    repo.close()