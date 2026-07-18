from __future__ import annotations

from pathlib import Path

from evaluation_data import build_evaluation_analytics, fetch_evaluation_dashboard_payload
from evaluation_repository import MonitoringEvaluationRepository


REPO_ROOT = Path(__file__).resolve().parents[1]


def _row(symbol: str, rank: int, score: float, confidence: float, sector: str, regime: str, signal: str, ret_1d: float, ret_5d: float, ret_10d: float, ret_20d: float, status_20d: str = "complete"):
    row = {
        "research_candidate_id": rank,
        "research_run_id": f"research-{rank}",
        "symbol": symbol,
        "observation_date": "2024-01-02",
        "observation_price": 100.0,
        "benchmark_symbol": "SPY",
        "benchmark_observation_price": 100.0,
        "overall_score": score,
        "confidence": confidence,
        "sector": sector,
        "market_regime": regime,
        "signal": signal,
        "rank": rank,
        "label_status": "partial" if status_20d != "complete" else "complete",
        "last_attempted_at": "2024-01-10T15:00:00+00:00",
        "updated_at": "2024-01-10T15:00:00+00:00",
        "created_at": "2024-01-10T15:00:00+00:00",
    }
    for horizon, value in [(1, ret_1d), (5, ret_5d), (10, ret_10d), (20, ret_20d)]:
        prefix = f"forward_{horizon}d"
        row[f"{prefix}_status"] = "complete" if not (horizon == 20 and status_20d != "complete") else status_20d
        row[f"{prefix}_target_date"] = "2024-01-03"
        row[f"{prefix}_actual_date"] = "2024-01-03"
        row[f"{prefix}_future_price"] = 100.0 * (1.0 + (0.0 if value is None else value)) if value is not None else None
        row[f"{prefix}_benchmark_future_price"] = 100.0
        row[f"{prefix}_return"] = value
        row[f"{prefix}_benchmark_return"] = 0.0 if value is not None else None
        row[f"{prefix}_excess_return"] = value
    return row


def test_empty_database_behavior_returns_zero_metrics(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'evaluation.db'}"
    repo = MonitoringEvaluationRepository(database_url=db_url)
    repo.db.ensure_schema()

    payload = fetch_evaluation_dashboard_payload(db_url)
    assert payload["evaluation_analytics"]["total_observations"] == 0
    assert payload["evaluation_analytics"]["status_counts"]["complete"] == 0
    assert payload["recent_labeled_observations"] == []
    assert payload["recent_label_failures"] == []
    repo.close()


def test_build_evaluation_analytics_calculates_groups_and_correlations():
    rows = [
        _row("AAA", 1, 35.0, 40.0, "Technology", "strong_bull", "BUY", 0.02, 0.05, 0.08, 0.10),
        _row("BBB", 2, 45.0, 50.0, "Technology", "weak_bull", "BUY", 0.03, 0.06, 0.09, 0.12),
        _row("CCC", 3, 55.0, 60.0, "Healthcare", "neutral", "HOLD", -0.01, 0.01, 0.02, 0.04),
        _row("DDD", 4, 65.0, 70.0, "Healthcare", "bear", "SELL", -0.02, -0.01, 0.01, 0.02),
        _row("EEE", 5, 75.0, 80.0, "Energy", "strong_bull", "BUY", 0.04, 0.08, 0.11, 0.16),
        _row("FFF", 6, 85.0, 90.0, "Energy", "strong_bull", "BUY", 0.05, 0.09, 0.13, 0.18),
        _row("AAA", 1, 88.0, 92.0, "Technology", "strong_bull", "BUY", 0.01, 0.03, 0.05, None, status_20d="pending"),
    ]

    analytics = build_evaluation_analytics(rows)
    horizon_1d = analytics["horizons"]["1d"]
    horizon_20d = analytics["horizons"]["20d"]

    assert analytics["status_counts"]["complete"] == 6
    assert analytics["status_counts"]["partial"] == 1
    assert horizon_1d["sample_size"] == 7
    assert horizon_20d["sample_size"] == 6
    assert horizon_1d["average_raw_return"] is not None
    assert analytics["score_buckets"]["1d"][0]["bucket"] == "below_40"
    assert analytics["confidence_buckets"]["1d"][-1]["bucket"] == "80_plus"
    assert analytics["regime_analysis"]["1d"][0]["market_regime"]
    assert analytics["sector_analysis"]["1d"][0]["sector"]
    assert analytics["signal_analysis"]["1d"][0]["signal"]
    assert analytics["rank_analysis"]["1d"][0]["bucket"] == "top_1"
    assert analytics["recurring_symbol_analysis"]["1d"][0]["symbol"] == "AAA"
    assert analytics["correlations"]["1d"]["sample_size"] == 7
    assert analytics["correlations"]["1d"]["score_vs_forward_return"] is not None


def test_correlations_return_none_for_insufficient_sample_and_zero_variance():
    sparse_rows = [_row("AAA", 1, 50.0, 50.0, "Technology", "bull", "BUY", 0.01, 0.02, 0.03, 0.04)]
    sparse = build_evaluation_analytics(sparse_rows)
    assert sparse["correlations"]["1d"]["score_vs_forward_return"] is None

    flat_rows = [
        _row("AAA", 1, 50.0, 50.0, "Technology", "bull", "BUY", 0.01, 0.02, 0.03, 0.04),
        _row("BBB", 2, 50.0, 50.0, "Technology", "bull", "BUY", 0.01, 0.02, 0.03, 0.04),
        _row("CCC", 3, 50.0, 50.0, "Technology", "bull", "BUY", 0.01, 0.02, 0.03, 0.04),
        _row("DDD", 4, 50.0, 50.0, "Technology", "bull", "BUY", 0.01, 0.02, 0.03, 0.04),
        _row("EEE", 5, 50.0, 50.0, "Technology", "bull", "BUY", 0.01, 0.02, 0.03, 0.04),
    ]
    flat = build_evaluation_analytics(flat_rows)
    assert flat["correlations"]["1d"]["score_vs_forward_return"] is None