from __future__ import annotations

import math
import statistics
from datetime import date, datetime, timezone
from typing import Any

from config import WALK_FORWARD_RELATIVE_DEGRADATION_EPSILON
from evaluation_repository import MonitoringEvaluationRepository
from walk_forward_repository import MonitoringWalkForwardRepository


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except Exception:
        return None


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _horizon_key(horizon: int) -> str:
    return f"forward_{int(horizon)}d"


def normalize_walk_forward_rows(
    rows: list[dict[str, Any]],
    horizon: int,
    start_date: str | None = None,
    end_date: str | None = None,
    symbol_filter: str | None = None,
    research_run_id: str | None = None,
) -> list[dict[str, Any]]:
    start_dt = _parse_date(start_date)
    end_dt = _parse_date(end_date)
    deduped: dict[int, dict[str, Any]] = {}
    observation_keys: dict[tuple[str, str, str], int] = {}
    duplicates_removed = 0
    malformed_dates = 0
    for row in rows:
        candidate_id = int(_as_float(row.get("research_candidate_id"), 0) or 0)
        if not candidate_id:
            continue
        if research_run_id and str(row.get("research_run_id") or "") != str(research_run_id):
            continue
        if symbol_filter and str(row.get("symbol") or "").upper() != str(symbol_filter).upper():
            continue
        status = str(row.get(f"forward_{horizon}d_status") or "").lower()
        if status != "complete":
            continue
        observation_date = _parse_date(row.get("observation_date"))
        if observation_date is None:
            malformed_dates += 1
            continue
        if start_dt and observation_date < start_dt:
            continue
        if end_dt and observation_date > end_dt:
            continue
        forward_return = _as_float(row.get(f"forward_{horizon}d_return"), None)
        benchmark_return = _as_float(row.get(f"forward_{horizon}d_benchmark_return"), None)
        excess_return = _as_float(row.get(f"forward_{horizon}d_excess_return"), None)
        if forward_return is None or benchmark_return is None or excess_return is None:
            continue
        normalized = dict(row)
        normalized["observation_date_dt"] = observation_date
        normalized["period_start"] = _month_start(observation_date)
        normalized["selected_forward_return"] = float(forward_return)
        normalized["selected_benchmark_return"] = float(benchmark_return)
        normalized["selected_excess_return"] = float(excess_return)
        observation_key = (
            str(row.get("research_run_id") or ""),
            str(row.get("symbol") or "").upper(),
            observation_date.isoformat(),
        )
        if observation_key in observation_keys:
            duplicates_removed += 1
            existing_candidate_id = observation_keys[observation_key]
            existing_row = deduped.get(existing_candidate_id)
            if existing_row is not None and candidate_id > existing_candidate_id:
                deduped.pop(existing_candidate_id, None)
                observation_keys[observation_key] = candidate_id
                deduped[candidate_id] = normalized
            continue
        if candidate_id in deduped:
            duplicates_removed += 1
            existing_date = deduped[candidate_id]["observation_date_dt"]
            if observation_date > existing_date:
                deduped[candidate_id] = normalized
        else:
            deduped[candidate_id] = normalized
            observation_keys[observation_key] = candidate_id
    normalized_rows = sorted(deduped.values(), key=lambda row: (row["observation_date_dt"], str(row.get("symbol") or ""), int(row.get("research_candidate_id") or 0)))
    for row in normalized_rows:
        row["normalization_metadata"] = {"duplicates_removed": duplicates_removed, "malformed_dates": malformed_dates}
    return normalized_rows


def generate_walk_forward_windows(
    rows: list[dict[str, Any]],
    horizon: int,
    benchmark_symbol: str,
    window_type: str,
    training_periods: int,
    validation_periods: int,
    step_periods: int,
    min_training_sample: int,
    min_validation_sample: int,
) -> list[dict[str, Any]]:
    if not rows:
        return []
    periods = sorted({row["period_start"] for row in rows})
    if not periods:
        return []
    windows: list[dict[str, Any]] = []
    step = max(int(step_periods), 1)
    training_size = max(int(training_periods), 1)
    validation_size = max(int(validation_periods), 1)
    window_kind = str(window_type or "rolling").lower()
    max_train_start = len(periods) - training_size - validation_size
    if max_train_start < 0:
        return []
    index = 0
    while index <= max_train_start:
        train_start_index = 0 if window_kind == "expanding" else index
        train_end_index = index + training_size - 1
        validation_start_index = train_end_index + 1
        validation_end_index = validation_start_index + validation_size - 1
        if validation_end_index >= len(periods):
            break
        training_period_set = set(periods[train_start_index : train_end_index + 1])
        validation_period_set = set(periods[validation_start_index : validation_end_index + 1])
        training_rows = [row for row in rows if row["period_start"] in training_period_set]
        validation_rows = [row for row in rows if row["period_start"] in validation_period_set]
        warnings: list[str] = []
        status = "completed"
        if len(training_rows) < int(min_training_sample):
            warnings.append(f"insufficient training data: {len(training_rows)} < {int(min_training_sample)}")
            status = "skipped"
        if len(validation_rows) < int(min_validation_sample):
            warnings.append(f"insufficient validation data: {len(validation_rows)} < {int(min_validation_sample)}")
            status = "skipped"
        windows.append(
            {
                "window_id": f"{window_kind}-{horizon}-{len(windows)+1}",
                "training_start_date": periods[train_start_index].isoformat(),
                "training_end_date": periods[train_end_index].isoformat(),
                "validation_start_date": periods[validation_start_index].isoformat(),
                "validation_end_date": periods[validation_end_index].isoformat(),
                "training_rows": training_rows,
                "validation_rows": validation_rows,
                "training_observation_count": len(training_rows),
                "validation_observation_count": len(validation_rows),
                "horizon": int(horizon),
                "benchmark_symbol": str(benchmark_symbol),
                "window_type": window_kind,
                "status": status,
                "warnings": warnings,
            }
        )
        index += step
    return windows


def _mean(values: list[float]) -> float | None:
    return round(statistics.mean(values), 6) if values else None


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 6) if values else None


def _stddev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    return round(statistics.pstdev(values), 6)


def _downside_deviation(values: list[float]) -> float | None:
    negatives = [min(value, 0.0) for value in values]
    if not negatives:
        return None
    squares = [value * value for value in negatives]
    return round(math.sqrt(sum(squares) / len(squares)), 6)


def _cumulative_return(values: list[float]) -> float | None:
    if not values:
        return None
    total = 1.0
    for value in values:
        total *= 1.0 + float(value)
    return round(total - 1.0, 6)


def _max_drawdown(values: list[float]) -> float | None:
    if not values:
        return None
    total = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for value in values:
        total *= 1.0 + float(value)
        peak = max(peak, total)
        if peak > 0:
            max_drawdown = min(max_drawdown, total / peak - 1.0)
    return round(max_drawdown, 6)


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None:
        return None
    if abs(float(denominator)) <= WALK_FORWARD_RELATIVE_DEGRADATION_EPSILON:
        return None
    return round(float(numerator) / float(denominator), 6)


def _series_metrics(raw_returns: list[float], benchmark_returns: list[float], excess_returns: list[float]) -> dict[str, Any]:
    average_excess = _mean(excess_returns)
    stddev_excess = _stddev(excess_returns)
    downside_excess = _downside_deviation(excess_returns)
    return {
        "observation_count": len(raw_returns),
        "average_raw_return": _mean(raw_returns),
        "median_raw_return": _median(raw_returns),
        "average_benchmark_return": _mean(benchmark_returns),
        "average_excess_return": average_excess,
        "median_excess_return": _median(excess_returns),
        "positive_return_rate": round(len([value for value in raw_returns if value > 0]) / len(raw_returns), 6) if raw_returns else None,
        "positive_excess_rate": round(len([value for value in excess_returns if value > 0]) / len(excess_returns), 6) if excess_returns else None,
        "standard_deviation": _stddev(raw_returns),
        "excess_standard_deviation": stddev_excess,
        "downside_deviation": _downside_deviation(raw_returns),
        "excess_downside_deviation": downside_excess,
        "cumulative_return": _cumulative_return(raw_returns),
        "cumulative_excess_return": _cumulative_return(excess_returns),
        "maximum_drawdown": _max_drawdown(raw_returns),
        "excess_maximum_drawdown": _max_drawdown(excess_returns),
        "sharpe_like_ratio": _ratio(average_excess, stddev_excess),
        "sortino_like_ratio": _ratio(average_excess, downside_excess),
    }


def _score_bucket(value: Any) -> str:
    numeric = _as_float(value, None)
    if numeric is None:
        return "unbucketed"
    if numeric < 40:
        return "below_40"
    if numeric < 50:
        return "40_to_49_99"
    if numeric < 60:
        return "50_to_59_99"
    if numeric < 70:
        return "60_to_69_99"
    if numeric < 80:
        return "70_to_79_99"
    return "80_plus"


def _group_metrics(rows: list[dict[str, Any]], group_key: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = str(row.get(group_key) or "Unknown")
        grouped.setdefault(key, []).append(row)
    results = []
    for key in sorted(grouped):
        group_rows = grouped[key]
        metrics = build_window_return_metrics(group_rows)
        metrics[group_key] = key
        results.append(metrics)
    return results


def build_window_return_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: (row["observation_date_dt"], str(row.get("symbol") or ""), int(row.get("research_candidate_id") or 0)))
    raw_returns = [float(row["selected_forward_return"]) for row in ordered if row.get("selected_forward_return") is not None]
    benchmark_returns = [float(row["selected_benchmark_return"]) for row in ordered if row.get("selected_benchmark_return") is not None]
    excess_returns = [float(row["selected_excess_return"]) for row in ordered if row.get("selected_excess_return") is not None]
    metrics = _series_metrics(raw_returns, benchmark_returns, excess_returns)
    metrics["observation_dates"] = [row["observation_date_dt"].isoformat() for row in ordered]
    return metrics


def build_window_analysis(rows: list[dict[str, Any]]) -> dict[str, Any]:
    score_buckets = []
    confidence_buckets = []
    score_grouped: dict[str, list[dict[str, Any]]] = {}
    confidence_grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        score_grouped.setdefault(_score_bucket(row.get("overall_score")), []).append(row)
        confidence_grouped.setdefault(_score_bucket(row.get("confidence")), []).append(row)
    for bucket_name in sorted(score_grouped):
        metrics = build_window_return_metrics(score_grouped[bucket_name])
        metrics["bucket"] = bucket_name
        score_buckets.append(metrics)
    for bucket_name in sorted(confidence_grouped):
        metrics = build_window_return_metrics(confidence_grouped[bucket_name])
        metrics["bucket"] = bucket_name
        confidence_buckets.append(metrics)
    top_rank_sets = {}
    eligible_rank_rows = [row for row in rows if _as_float(row.get("rank"), None) is not None]
    for rank_limit in [1, 3, 5, 10]:
        subset = [row for row in eligible_rank_rows if int(_as_float(row.get("rank"), 10**9)) <= rank_limit]
        top_rank_sets[f"top_{rank_limit}"] = build_window_return_metrics(subset)
    return {
        "all_candidates": build_window_return_metrics(rows),
        "top_ranks": top_rank_sets,
        "score_buckets": score_buckets,
        "confidence_buckets": confidence_buckets,
        "signals": _group_metrics(rows, "signal"),
        "sectors": _group_metrics(rows, "sector"),
        "market_regimes": _group_metrics(rows, "market_regime"),
    }


def compare_training_validation_metrics(training_metrics: dict[str, Any], validation_metrics: dict[str, Any], epsilon: float = WALK_FORWARD_RELATIVE_DEGRADATION_EPSILON) -> dict[str, Any]:
    train_all = training_metrics.get("all_candidates") or {}
    validation_all = validation_metrics.get("all_candidates") or {}
    keys = [
        "average_raw_return",
        "average_excess_return",
        "positive_excess_rate",
        "excess_standard_deviation",
        "sharpe_like_ratio",
        "sortino_like_ratio",
    ]
    degradation = {}
    for key in keys:
        training_value = _as_float(train_all.get(key), None)
        validation_value = _as_float(validation_all.get(key), None)
        absolute = None if training_value is None or validation_value is None else round(validation_value - training_value, 6)
        relative = None
        if absolute is not None and training_value is not None and abs(training_value) > float(epsilon):
            relative = round(absolute / abs(training_value), 6)
        degradation[key] = {
            "training": training_value,
            "validation": validation_value,
            "validation_degradation": absolute,
            "relative_degradation": relative,
        }
    return degradation


def fetch_walk_forward_dashboard_payload(database_url: str | None) -> dict[str, Any]:
    repository = MonitoringWalkForwardRepository(database_url=database_url)
    payload = {
        "db_connected": repository.db.enabled,
        "total_validation_runs": 0,
        "latest_run": {},
        "windows": [],
    }
    if not repository.db.enabled:
        return payload
    try:
        repository.db.ensure_schema()
        latest_run = repository.fetch_latest_run() or {}
        windows = repository.fetch_windows_for_run(str(latest_run.get("run_id") or "")) if latest_run.get("run_id") else []
        payload["total_validation_runs"] = repository.count_runs()
        payload["latest_run"] = latest_run
        payload["windows"] = windows
        return payload
    finally:
        repository.close()
