from __future__ import annotations

from collections import defaultdict
from typing import Any

from factor_intelligence_utils import as_float, mean, short_hash, spearman, stddev
from factor_registry import FactorDefinition
from walk_forward_data import generate_walk_forward_windows, normalize_walk_forward_rows


def _direction_ok(direction: str, value: float | None) -> int | None:
    if value is None:
        return None
    if direction == "higher_is_better":
        return 1 if value >= 0 else 0
    if direction == "lower_is_better":
        return 1 if value <= 0 else 0
    return None


def _window_factor_stats(window_rows: list[dict[str, Any]], field_name: str, forward_horizon: int, minimum_sample_size: int) -> tuple[float | None, float | None, int]:
    pairs = []
    for row in window_rows:
        factor = as_float(row.get(field_name), None)
        ret = as_float(row.get(f"forward_{forward_horizon}d_return"), None)
        if factor is None or ret is None:
            continue
        pairs.append((factor, ret))
    if len(pairs) < minimum_sample_size:
        return None, None, len(pairs)

    ordered = sorted(pairs, key=lambda item: item[0])
    bucket_size = max(len(ordered) // 5, 1)
    bottom = [item[1] for item in ordered[:bucket_size]]
    top = [item[1] for item in ordered[-bucket_size:]]
    spread = None
    if top and bottom:
        spread = round((sum(top) / len(top)) - (sum(bottom) / len(bottom)), 6)

    corr = spearman([item[0] for item in pairs], [item[1] for item in pairs], minimum_sample_size=minimum_sample_size)
    return corr, spread, len(pairs)


def compute_stability(
    evaluation_rows: list[dict[str, Any]],
    factors: list[FactorDefinition],
    forward_horizon: int,
    minimum_sample_size: int,
    window_type: str = "rolling",
    training_periods: int = 3,
    validation_periods: int = 1,
    step_periods: int = 1,
) -> list[dict[str, Any]]:
    normalized = normalize_walk_forward_rows(evaluation_rows, horizon=forward_horizon)
    windows = generate_walk_forward_windows(
        normalized,
        horizon=forward_horizon,
        benchmark_symbol="SPY",
        window_type=window_type,
        training_periods=training_periods,
        validation_periods=validation_periods,
        step_periods=step_periods,
        min_training_sample=minimum_sample_size,
        min_validation_sample=minimum_sample_size,
    )

    rows: list[dict[str, Any]] = []
    factor_map = {factor.factor_id: factor for factor in factors}
    field_map = {factor.factor_id: str((factor.metadata or {}).get("field") or factor.factor_id) for factor in factors}
    aggregate_values: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)

    for window in windows:
        if str(window.get("status") or "") != "completed":
            continue
        validation_rows = list(window.get("validation_rows") or [])
        for factor in factors:
            field_name = field_map[factor.factor_id]
            corr, spread, sample_count = _window_factor_stats(validation_rows, field_name, forward_horizon, minimum_sample_size)
            direction_ok = _direction_ok(factor.direction, corr)
            score_parts = [abs(corr) if corr is not None else 0.0, abs(spread) if spread is not None else 0.0]
            window_score = round(sum(score_parts) / len(score_parts), 6)
            record = {
                "stability_id": short_hash([factor.factor_id, factor.version, window.get("window_id"), "window"], length=32),
                "factor_id": factor.factor_id,
                "factor_version": factor.version,
                "window_id": window.get("window_id"),
                "per_window": True,
                "training_start_date": window.get("training_start_date"),
                "training_end_date": window.get("training_end_date"),
                "validation_start_date": window.get("validation_start_date"),
                "validation_end_date": window.get("validation_end_date"),
                "window_sample_count": sample_count,
                "window_correlation": corr,
                "window_spread": spread,
                "expected_direction_correct": direction_ok,
                "mean_window_score": window_score,
                "stddev_window_score": None,
                "min_window_score": window_score,
                "max_window_score": window_score,
                "degradation_score": None,
                "stability_score": window_score,
                "stability_classification": "insufficient_data" if sample_count < minimum_sample_size else "mixed",
                "status": "insufficient_data" if sample_count < minimum_sample_size else "completed",
                "metadata": {"factor_field": field_name},
            }
            rows.append(record)
            aggregate_values[(factor.factor_id, factor.version)].append(record)

    for (factor_id, factor_version), records in sorted(aggregate_values.items()):
        corr_values = [row["window_correlation"] for row in records if row.get("window_correlation") is not None]
        spread_values = [row["window_spread"] for row in records if row.get("window_spread") is not None]
        direction_values = [row["expected_direction_correct"] for row in records if row.get("expected_direction_correct") is not None]
        scores = [row["stability_score"] for row in records if row.get("stability_score") is not None]
        degradation = None
        if len(scores) >= 2:
            degradation = round(scores[-1] - scores[0], 6)
        stability_score = mean(scores)

        classification = "insufficient_data"
        if stability_score is not None and len(scores) >= 2:
            if stability_score >= 0.20 and (mean(direction_values) or 0.0) >= 0.70:
                classification = "highly_stable"
            elif stability_score >= 0.12:
                classification = "stable"
            elif stability_score >= 0.06:
                classification = "mixed"
            else:
                classification = "unstable"

        rows.append(
            {
                "stability_id": short_hash([factor_id, factor_version, "aggregate"], length=32),
                "factor_id": factor_id,
                "factor_version": factor_version,
                "window_id": None,
                "per_window": False,
                "training_start_date": None,
                "training_end_date": None,
                "validation_start_date": None,
                "validation_end_date": None,
                "window_sample_count": len(records),
                "window_correlation": mean(corr_values),
                "window_spread": mean(spread_values),
                "expected_direction_correct": round(sum(direction_values) / len(direction_values), 6) if direction_values else None,
                "mean_window_score": mean(scores),
                "stddev_window_score": stddev(scores),
                "min_window_score": round(min(scores), 6) if scores else None,
                "max_window_score": round(max(scores), 6) if scores else None,
                "degradation_score": degradation,
                "stability_score": stability_score,
                "stability_classification": classification,
                "status": "completed" if classification != "insufficient_data" else "insufficient_data",
                "metadata": {"window_count": len(records)},
            }
        )

    return sorted(rows, key=lambda item: (item["factor_id"], item["factor_version"], 0 if item["per_window"] else 1, str(item.get("window_id") or "")))
