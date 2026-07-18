from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from config import BENCHMARK_SYMBOL, FORWARD_RETURN_HORIZONS, FORWARD_RETURN_MIN_CORRELATION_SAMPLE_SIZE
from monitoring_db import MonitoringDatabase
from evaluation_repository import MonitoringEvaluationRepository


HORIZON_LABELS = {1: "1d", 5: "5d", 10: "10d", 20: "20d"}
SCORE_BUCKETS = [
    (float("-inf"), 40.0, "below_40"),
    (40.0, 50.0, "40_to_49_99"),
    (50.0, 60.0, "50_to_59_99"),
    (60.0, 70.0, "60_to_69_99"),
    (70.0, 80.0, "70_to_79_99"),
    (80.0, float("inf"), "80_plus"),
]

CONFIDENCE_BUCKETS = SCORE_BUCKETS


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_float(value: Any, default: float | None = 0.0) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _positive_rate(values: list[float]) -> float | None:
    if not values:
        return None
    return round(len([value for value in values if value > 0]) / len(values), 4)


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(statistics.mean(values), 4)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return round(statistics.median(values), 4)


def _horizon_key(horizon: int) -> str:
    return HORIZON_LABELS.get(int(horizon), f"{int(horizon)}d")


def build_evaluation_config_snapshot() -> dict[str, Any]:
    return {
        "benchmark_symbol": BENCHMARK_SYMBOL,
        "forward_return_horizons": list(FORWARD_RETURN_HORIZONS),
        "minimum_correlation_sample_size": FORWARD_RETURN_MIN_CORRELATION_SAMPLE_SIZE,
    }


def _complete_rows(rows: list[dict[str, Any]], horizon: int) -> list[dict[str, Any]]:
    status_key = f"forward_{horizon}d_status"
    return [row for row in rows if str(row.get(status_key) or "").lower() == "complete"]


def _extract_metric_values(rows: list[dict[str, Any]], field_name: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = _as_float(row.get(field_name), None)
        if value is not None:
            values.append(float(value))
    return values


def _build_horizon_summary(rows: list[dict[str, Any]], horizon: int) -> dict[str, Any]:
    complete_rows = _complete_rows(rows, horizon)
    symbol_returns = _extract_metric_values(complete_rows, f"forward_{horizon}d_return")
    benchmark_returns = _extract_metric_values(complete_rows, f"forward_{horizon}d_benchmark_return")
    excess_returns = _extract_metric_values(complete_rows, f"forward_{horizon}d_excess_return")
    return {
        "sample_size": len(complete_rows),
        "average_raw_return": _mean(symbol_returns),
        "average_benchmark_return": _mean(benchmark_returns),
        "average_excess_return": _mean(excess_returns),
        "median_raw_return": _median(symbol_returns),
        "median_excess_return": _median(excess_returns),
        "positive_return_rate": _positive_rate(symbol_returns),
        "positive_excess_return_rate": _positive_rate(excess_returns),
    }


def _bucket_for_value(value: float | None, buckets: list[tuple[float, float, str]]) -> str:
    numeric = _as_float(value, None)
    if numeric is None:
        return "unbucketed"
    for minimum, maximum, label in buckets:
        if minimum <= numeric < maximum:
            return label
    return "unbucketed"


def _bucket_metrics(rows: list[dict[str, Any]], horizon: int, bucket_field: str, buckets: list[tuple[float, float, str]]) -> list[dict[str, Any]]:
    complete_rows = _complete_rows(rows, horizon)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in complete_rows:
        grouped[_bucket_for_value(row.get(bucket_field), buckets)].append(row)

    results: list[dict[str, Any]] = []
    for _, _, label in buckets:
        bucket_rows = grouped.get(label, [])
        raw_returns = _extract_metric_values(bucket_rows, f"forward_{horizon}d_return")
        excess_returns = _extract_metric_values(bucket_rows, f"forward_{horizon}d_excess_return")
        results.append(
            {
                "bucket": label,
                "candidate_count": len(bucket_rows),
                "average_score": _mean(_extract_metric_values(bucket_rows, "overall_score")),
                "average_confidence": _mean(_extract_metric_values(bucket_rows, "confidence")),
                "average_forward_return": _mean(raw_returns),
                "median_forward_return": _median(raw_returns),
                "average_excess_return": _mean(excess_returns),
                "positive_return_rate": _positive_rate(raw_returns),
                "positive_excess_return_rate": _positive_rate(excess_returns),
            }
        )
    return results


def _group_metrics(rows: list[dict[str, Any]], horizon: int, group_field: str) -> list[dict[str, Any]]:
    complete_rows = _complete_rows(rows, horizon)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in complete_rows:
        grouped[str(row.get(group_field) or "Unknown")].append(row)
    results = []
    for group_name in sorted(grouped):
        group_rows = grouped[group_name]
        raw_returns = _extract_metric_values(group_rows, f"forward_{horizon}d_return")
        excess_returns = _extract_metric_values(group_rows, f"forward_{horizon}d_excess_return")
        results.append(
            {
                group_field: group_name,
                "candidate_count": len(group_rows),
                "average_score": _mean(_extract_metric_values(group_rows, "overall_score")),
                "average_confidence": _mean(_extract_metric_values(group_rows, "confidence")),
                "average_forward_return": _mean(raw_returns),
                "median_forward_return": _median(raw_returns),
                "average_excess_return": _mean(excess_returns),
                "positive_return_rate": _positive_rate(raw_returns),
                "positive_excess_return_rate": _positive_rate(excess_returns),
            }
        )
    return results


def _rank_group_rows(rows: list[dict[str, Any]], horizon: int, max_rank: int | None = None) -> list[dict[str, Any]]:
    complete_rows = [row for row in _complete_rows(rows, horizon) if _as_float(row.get("rank"), None) is not None]
    if max_rank is None:
        return complete_rows
    return [row for row in complete_rows if int(_as_float(row.get("rank"), 10**9)) <= int(max_rank)]


def _rank_metrics(rows: list[dict[str, Any]], horizon: int) -> list[dict[str, Any]]:
    rank_sets = [(1, "top_1"), (3, "top_3"), (5, "top_5"), (10, "top_10"), (None, "all")]
    results = []
    for rank_limit, label in rank_sets:
        group_rows = _rank_group_rows(rows, horizon, rank_limit)
        raw_returns = _extract_metric_values(group_rows, f"forward_{horizon}d_return")
        excess_returns = _extract_metric_values(group_rows, f"forward_{horizon}d_excess_return")
        results.append(
            {
                "bucket": label,
                "candidate_count": len(group_rows),
                "average_score": _mean(_extract_metric_values(group_rows, "overall_score")),
                "average_confidence": _mean(_extract_metric_values(group_rows, "confidence")),
                "average_forward_return": _mean(raw_returns),
                "median_forward_return": _median(raw_returns),
                "average_excess_return": _mean(excess_returns),
                "positive_return_rate": _positive_rate(raw_returns),
                "positive_excess_return_rate": _positive_rate(excess_returns),
            }
        )
    return results


def _recurring_symbol_metrics(rows: list[dict[str, Any]], horizon: int) -> list[dict[str, Any]]:
    complete_rows = _complete_rows(rows, horizon)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in complete_rows:
        grouped[str(row.get("symbol") or "").upper()].append(row)
    results = []
    for symbol, group_rows in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        raw_returns = _extract_metric_values(group_rows, f"forward_{horizon}d_return")
        excess_returns = _extract_metric_values(group_rows, f"forward_{horizon}d_excess_return")
        results.append(
            {
                "symbol": symbol,
                "observation_count": len(group_rows),
                "average_score": _mean(_extract_metric_values(group_rows, "overall_score")),
                "average_confidence": _mean(_extract_metric_values(group_rows, "confidence")),
                "average_forward_return": _mean(raw_returns),
                "average_excess_return": _mean(excess_returns),
                "positive_excess_return_rate": _positive_rate(excess_returns),
            }
        )
    return results[:25]


def _pearson_correlation(xs: list[float], ys: list[float], minimum_sample_size: int) -> float | None:
    if len(xs) != len(ys):
        return None
    if len(xs) < minimum_sample_size:
        return None
    if len(set(xs)) <= 1 or len(set(ys)) <= 1:
        return None
    try:
        import pandas as pd

        return round(float(pd.Series(xs).corr(pd.Series(ys), method="pearson")), 6)
    except Exception:
        return None


def _correlation_metrics(rows: list[dict[str, Any]], horizon: int, minimum_sample_size: int) -> dict[str, Any]:
    complete_rows = _complete_rows(rows, horizon)
    score_values = _extract_metric_values(complete_rows, "overall_score")
    confidence_values = _extract_metric_values(complete_rows, "confidence")
    rank_values = _extract_metric_values(complete_rows, "rank")
    forward_returns = _extract_metric_values(complete_rows, f"forward_{horizon}d_return")
    excess_returns = _extract_metric_values(complete_rows, f"forward_{horizon}d_excess_return")
    return {
        "sample_size": len(complete_rows),
        "score_vs_forward_return": _pearson_correlation(score_values, forward_returns, minimum_sample_size),
        "score_vs_excess_return": _pearson_correlation(score_values, excess_returns, minimum_sample_size),
        "confidence_vs_forward_return": _pearson_correlation(confidence_values, forward_returns, minimum_sample_size),
        "confidence_vs_excess_return": _pearson_correlation(confidence_values, excess_returns, minimum_sample_size),
        "rank_vs_forward_return": _pearson_correlation(rank_values, forward_returns, minimum_sample_size),
        "rank_vs_excess_return": _pearson_correlation(rank_values, excess_returns, minimum_sample_size),
    }


def build_evaluation_analytics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_observations = len(rows)
    status_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        status_counts[str(row.get("label_status") or "pending").lower()] += 1

    horizon_payload: dict[str, Any] = {}
    score_buckets: dict[str, Any] = {}
    confidence_buckets: dict[str, Any] = {}
    regime_analysis: dict[str, Any] = {}
    sector_analysis: dict[str, Any] = {}
    signal_analysis: dict[str, Any] = {}
    rank_analysis: dict[str, Any] = {}
    recurring_analysis: dict[str, Any] = {}
    correlation_analysis: dict[str, Any] = {}

    for horizon in FORWARD_RETURN_HORIZONS:
        key = _horizon_key(horizon)
        horizon_payload[key] = _build_horizon_summary(rows, horizon)
        score_buckets[key] = _bucket_metrics(rows, horizon, "overall_score", SCORE_BUCKETS)
        confidence_buckets[key] = _bucket_metrics(rows, horizon, "confidence", CONFIDENCE_BUCKETS)
        regime_analysis[key] = _group_metrics(rows, horizon, "market_regime")
        sector_analysis[key] = _group_metrics(rows, horizon, "sector")
        signal_analysis[key] = _group_metrics(rows, horizon, "signal")
        rank_analysis[key] = _rank_metrics(rows, horizon)
        recurring_analysis[key] = _recurring_symbol_metrics(rows, horizon)
        correlation_analysis[key] = _correlation_metrics(rows, horizon, FORWARD_RETURN_MIN_CORRELATION_SAMPLE_SIZE)

    labeled_candidates = total_observations - int(status_counts.get("pending", 0))
    latest_attempt = max((str(row.get("last_attempted_at") or row.get("updated_at") or row.get("created_at") or "") for row in rows), default="")
    benchmark = next((str(row.get("benchmark_symbol") or "") for row in rows if row.get("benchmark_symbol")), BENCHMARK_SYMBOL)

    return {
        "benchmark_symbol": benchmark,
        "total_observations": total_observations,
        "labeled_candidates": labeled_candidates,
        "status_counts": {
            "pending": int(status_counts.get("pending", 0)),
            "partial": int(status_counts.get("partial", 0)),
            "complete": int(status_counts.get("complete", 0)),
            "unavailable": int(status_counts.get("unavailable", 0)),
            "data_error": int(status_counts.get("data_error", 0)),
        },
        "horizons": horizon_payload,
        "score_buckets": score_buckets,
        "confidence_buckets": confidence_buckets,
        "regime_analysis": regime_analysis,
        "sector_analysis": sector_analysis,
        "signal_analysis": signal_analysis,
        "rank_analysis": rank_analysis,
        "recurring_symbol_analysis": recurring_analysis,
        "correlations": correlation_analysis,
        "latest_attempted_at": latest_attempt or None,
    }


def fetch_evaluation_dashboard_payload(
    database_url: str | None,
    selected_horizon: str | None = None,
    database_factory=MonitoringDatabase,
) -> dict[str, Any]:
    repository = MonitoringEvaluationRepository(database_url=database_url)
    payload = {
        "db_connected": repository.db.enabled,
        "latest_labeling_run": {},
        "recent_labeled_observations": [],
        "recent_label_failures": [],
        "selected_horizon": selected_horizon,
        "evaluation_analytics": {
            "benchmark_symbol": BENCHMARK_SYMBOL,
            "total_observations": 0,
            "labeled_candidates": 0,
            "status_counts": {"pending": 0, "partial": 0, "complete": 0, "unavailable": 0, "data_error": 0},
            "horizons": {},
            "score_buckets": {},
            "confidence_buckets": {},
            "regime_analysis": {},
            "sector_analysis": {},
            "signal_analysis": {},
            "rank_analysis": {},
            "recurring_symbol_analysis": {},
            "correlations": {},
            "latest_attempted_at": None,
        },
        "evaluation_config": build_evaluation_config_snapshot(),
    }
    if not repository.db.enabled:
        return payload

    try:
        repository.db.ensure_schema()
        rows = repository.fetch_evaluation_rows_for_dashboard(limit=1000)
        payload["recent_labeled_observations"] = rows[:25]
        payload["recent_label_failures"] = repository.fetch_recent_label_failures(limit=25)
        payload["evaluation_analytics"] = build_evaluation_analytics(rows)
        latest_attempt = repository.fetch_latest_labeling_timestamp() or {}
        payload["latest_labeling_run"] = latest_attempt
        return payload
    finally:
        repository.close()