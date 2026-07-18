from __future__ import annotations

import math
from typing import Any

from config import WALK_FORWARD_MIN_WINDOWS_FOR_DECAY, WALK_FORWARD_RELATIVE_DEGRADATION_EPSILON
from factor_attribution import build_factor_attribution_analytics


def _as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def analyze_factor_stability(training_rows: list[dict[str, Any]], validation_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    training = build_factor_attribution_analytics(training_rows, minimum_sample_size=1, combination_min_sample_size=1)
    validation = build_factor_attribution_analytics(validation_rows, minimum_sample_size=1, combination_min_sample_size=1)
    train_importance = {row["factor"]: index + 1 for index, row in enumerate(training.get("feature_importance_summary") or [])}
    validation_importance = {row["factor"]: index + 1 for index, row in enumerate(validation.get("feature_importance_summary") or [])}
    train_correlations = {row["factor"]: row for row in training.get("factor_correlations") or []}
    validation_correlations = {row["factor"]: row for row in validation.get("factor_correlations") or []}
    factors = sorted(set(train_correlations) | set(validation_correlations))
    results = []
    for factor in factors:
        train_row = train_correlations.get(factor, {})
        validation_row = validation_correlations.get(factor, {})
        training_corr = _as_float(train_row.get("20d_excess_correlation") or train_row.get("10d_excess_correlation") or train_row.get("5d_excess_correlation") or train_row.get("1d_excess_correlation"), None)
        validation_corr = _as_float(validation_row.get("20d_excess_correlation") or validation_row.get("10d_excess_correlation") or validation_row.get("5d_excess_correlation") or validation_row.get("1d_excess_correlation"), None)
        train_rank = train_importance.get(factor)
        validation_rank = validation_importance.get(factor)
        sign_consistent = None
        if training_corr is not None and validation_corr is not None and training_corr != 0 and validation_corr != 0:
            sign_consistent = (training_corr > 0 and validation_corr > 0) or (training_corr < 0 and validation_corr < 0)
        results.append(
            {
                "factor": factor,
                "training_correlation": training_corr,
                "validation_correlation": validation_corr,
                "correlation_difference": None if training_corr is None or validation_corr is None else round(validation_corr - training_corr, 6),
                "correlation_sign_consistency": sign_consistent,
                "training_bucket_spread": next((row.get("average_bucket_spread") for row in training.get("feature_importance_summary") or [] if row.get("factor") == factor), None),
                "validation_bucket_spread": next((row.get("average_bucket_spread") for row in validation.get("feature_importance_summary") or [] if row.get("factor") == factor), None),
                "spread_difference": None,
                "training_rank": train_rank,
                "validation_rank": validation_rank,
                "rank_change": None if train_rank is None or validation_rank is None else int(validation_rank) - int(train_rank),
                "positive_relationship_count": 1 if validation_corr is not None and validation_corr > 0 else 0,
                "negative_relationship_count": 1 if validation_corr is not None and validation_corr < 0 else 0,
                "stability_percentage": 1.0 if sign_consistent is True else (0.0 if sign_consistent is False else None),
            }
        )
        results[-1]["spread_difference"] = None if results[-1]["training_bucket_spread"] is None or results[-1]["validation_bucket_spread"] is None else round(float(results[-1]["validation_bucket_spread"]) - float(results[-1]["training_bucket_spread"]), 6)
    return results


def aggregate_factor_stability(window_factor_rows: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for window_rows in window_factor_rows:
        for row in window_rows:
            grouped.setdefault(str(row.get("factor") or ""), []).append(row)
    results = []
    for factor, rows in sorted(grouped.items()):
        sign_values = [row for row in rows if row.get("correlation_sign_consistency") is not None]
        results.append(
            {
                "factor": factor,
                "window_count": len(rows),
                "average_training_correlation": round(sum(float(row.get("training_correlation") or 0.0) for row in rows if row.get("training_correlation") is not None) / len([row for row in rows if row.get("training_correlation") is not None]), 6) if any(row.get("training_correlation") is not None for row in rows) else None,
                "average_validation_correlation": round(sum(float(row.get("validation_correlation") or 0.0) for row in rows if row.get("validation_correlation") is not None) / len([row for row in rows if row.get("validation_correlation") is not None]), 6) if any(row.get("validation_correlation") is not None for row in rows) else None,
                "average_correlation_difference": round(sum(float(row.get("correlation_difference") or 0.0) for row in rows if row.get("correlation_difference") is not None) / len([row for row in rows if row.get("correlation_difference") is not None]), 6) if any(row.get("correlation_difference") is not None for row in rows) else None,
                "sign_consistency_percentage": round(len([row for row in sign_values if row.get("correlation_sign_consistency") is True]) / len(sign_values), 6) if sign_values else None,
                "positive_relationship_windows": sum(int(row.get("positive_relationship_count") or 0) for row in rows),
                "negative_relationship_windows": sum(int(row.get("negative_relationship_count") or 0) for row in rows),
                "average_rank_change": round(sum(float(row.get("rank_change") or 0.0) for row in rows if row.get("rank_change") is not None) / len([row for row in rows if row.get("rank_change") is not None]), 6) if any(row.get("rank_change") is not None for row in rows) else None,
                "stability_percentage": round(sum(float(row.get("stability_percentage") or 0.0) for row in rows if row.get("stability_percentage") is not None) / len([row for row in rows if row.get("stability_percentage") is not None]), 6) if any(row.get("stability_percentage") is not None for row in rows) else None,
            }
        )
    return results


def analyze_performance_decay(windows: list[dict[str, Any]], minimum_windows: int = WALK_FORWARD_MIN_WINDOWS_FOR_DECAY) -> dict[str, Any]:
    completed = [window for window in windows if str(window.get("status") or "").lower() == "completed"]
    series = [
        float(((window.get("validation_metrics") or {}).get("all_candidates") or {}).get("average_excess_return") or 0.0)
        for window in completed
        if ((window.get("validation_metrics") or {}).get("all_candidates") or {}).get("average_excess_return") is not None
    ]
    if len(series) < int(minimum_windows):
        return {
            "window_count": len(series),
            "validation_excess_by_window": series,
            "trend_slope": None,
            "early_window_performance": None,
            "recent_window_performance": None,
            "difference": None,
            "performance_decay_flag": False,
            "explanation": "insufficient windows for decay analysis",
        }
    xs = list(range(len(series)))
    x_mean = sum(xs) / len(xs)
    y_mean = sum(series) / len(series)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, series))
    denominator = sum((x - x_mean) ** 2 for x in xs)
    slope = round(numerator / denominator, 6) if denominator else None
    midpoint = max(len(series) // 2, 1)
    early = series[:midpoint]
    recent = series[midpoint:]
    early_mean = round(sum(early) / len(early), 6) if early else None
    recent_mean = round(sum(recent) / len(recent), 6) if recent else None
    difference = None if early_mean is None or recent_mean is None else round(recent_mean - early_mean, 6)
    decay = bool(slope is not None and slope < 0 and difference is not None and difference < 0)
    return {
        "window_count": len(series),
        "validation_excess_by_window": series,
        "trend_slope": slope,
        "early_window_performance": early_mean,
        "recent_window_performance": recent_mean,
        "difference": difference,
        "performance_decay_flag": decay,
        "explanation": "validation excess return deteriorated across windows" if decay else "no material performance decay detected",
    }


def analyze_regime_robustness(windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for window in windows:
        regime_rows = (window.get("validation_metrics") or {}).get("market_regimes") or []
        for row in regime_rows:
            grouped.setdefault(str(row.get("market_regime") or "Unknown"), []).append(row)
    results = []
    for regime, rows in sorted(grouped.items()):
        observation_count = sum(int(row.get("observation_count") or 0) for row in rows)
        avg_excess_values = [float(row.get("average_excess_return") or 0.0) for row in rows if row.get("average_excess_return") is not None]
        positive_rate_values = [float(row.get("positive_excess_rate") or 0.0) for row in rows if row.get("positive_excess_rate") is not None]
        volatility_values = [float(row.get("excess_standard_deviation") or 0.0) for row in rows if row.get("excess_standard_deviation") is not None]
        sharpe_values = [float(row.get("sharpe_like_ratio") or 0.0) for row in rows if row.get("sharpe_like_ratio") is not None]
        warnings = []
        if observation_count < 5:
            warnings.append("sparse regime sample")
        results.append(
            {
                "market_regime": regime,
                "window_count": len(rows),
                "validation_observation_count": observation_count,
                "average_validation_raw_return": round(sum(float(row.get("average_raw_return") or 0.0) for row in rows if row.get("average_raw_return") is not None) / len([row for row in rows if row.get("average_raw_return") is not None]), 6) if any(row.get("average_raw_return") is not None for row in rows) else None,
                "average_validation_excess_return": round(sum(avg_excess_values) / len(avg_excess_values), 6) if avg_excess_values else None,
                "positive_excess_rate": round(sum(positive_rate_values) / len(positive_rate_values), 6) if positive_rate_values else None,
                "volatility": round(sum(volatility_values) / len(volatility_values), 6) if volatility_values else None,
                "sharpe_like_ratio": round(sum(sharpe_values) / len(sharpe_values), 6) if sharpe_values else None,
                "factor_stability": None,
                "best_performing_signal": None,
                "worst_performing_signal": None,
                "warnings": warnings,
            }
        )
    return results


def build_validation_scorecard(
    windows: list[dict[str, Any]],
    factor_stability_summary: list[dict[str, Any]],
    decay_metrics: dict[str, Any],
    regime_robustness: list[dict[str, Any]],
) -> dict[str, Any]:
    completed = [window for window in windows if str(window.get("status") or "").lower() == "completed"]
    validation_excess = [
        float(((window.get("validation_metrics") or {}).get("all_candidates") or {}).get("average_excess_return") or 0.0)
        for window in completed
        if ((window.get("validation_metrics") or {}).get("all_candidates") or {}).get("average_excess_return") is not None
    ]
    degradation_values = [
        float((((window.get("degradation_metrics") or {}).get("average_excess_return") or {}).get("validation_degradation") or 0.0))
        for window in completed
        if (((window.get("degradation_metrics") or {}).get("average_excess_return") or {}).get("validation_degradation") is not None)
    ]
    stability_scores = [float(row.get("stability_percentage") or 0.0) for row in factor_stability_summary if row.get("stability_percentage") is not None]
    category_rows = []

    out_status = "insufficient_data"
    out_score = None
    if validation_excess:
        mean_excess = sum(validation_excess) / len(validation_excess)
        out_score = round(mean_excess, 6)
        out_status = "strong" if mean_excess > 0.01 else ("acceptable" if mean_excess >= 0 else "warning")
    category_rows.append({"category": "Out-of-sample performance", "status": out_status, "score": out_score, "explanation": "average validation excess return across completed windows", "supporting_metrics": {"average_validation_excess_return": out_score}})

    deg_status = "insufficient_data"
    deg_score = None
    if degradation_values:
        deg_score = round(sum(degradation_values) / len(degradation_values), 6)
        deg_status = "strong" if deg_score >= 0 else ("acceptable" if deg_score > -0.01 else "warning")
    category_rows.append({"category": "Training-validation degradation", "status": deg_status, "score": deg_score, "explanation": "average validation minus training excess return", "supporting_metrics": {"average_degradation": deg_score}})

    stability_status = "insufficient_data"
    stability_score = None
    if stability_scores:
        stability_score = round(sum(stability_scores) / len(stability_scores), 6)
        stability_status = "strong" if stability_score >= 0.75 else ("acceptable" if stability_score >= 0.5 else "warning")
    category_rows.append({"category": "Factor stability", "status": stability_status, "score": stability_score, "explanation": "average factor sign-consistency across windows", "supporting_metrics": {"average_factor_stability": stability_score}})

    regime_status = "insufficient_data"
    regime_score = None
    if regime_robustness:
        regime_values = [row for row in regime_robustness if row.get("average_validation_excess_return") is not None]
        if regime_values:
            regime_score = round(sum(float(row.get("average_validation_excess_return") or 0.0) for row in regime_values) / len(regime_values), 6)
            regime_status = "strong" if regime_score > 0.01 else ("acceptable" if regime_score >= 0 else "warning")
    category_rows.append({"category": "Regime robustness", "status": regime_status, "score": regime_score, "explanation": "average validation excess return across regimes", "supporting_metrics": {"average_regime_excess_return": regime_score}})

    decay_status = "insufficient_data"
    decay_score = None
    if decay_metrics.get("trend_slope") is not None:
        decay_score = float(decay_metrics.get("trend_slope") or 0.0)
        decay_status = "warning" if decay_metrics.get("performance_decay_flag") else "acceptable"
    category_rows.append({"category": "Performance decay", "status": decay_status, "score": decay_score, "explanation": str(decay_metrics.get("explanation") or ""), "supporting_metrics": decay_metrics})

    sample_status = "strong"
    if not completed:
        sample_status = "insufficient_data"
    elif any(str(window.get("status") or "") == "skipped" for window in windows):
        sample_status = "warning"
    category_rows.append({"category": "Sample sufficiency", "status": sample_status, "score": len(completed), "explanation": "completed windows versus skipped windows", "supporting_metrics": {"completed_windows": len(completed), "total_windows": len(windows)}})

    data_quality_status = "strong"
    if any(window.get("warnings") for window in windows):
        data_quality_status = "warning"
    category_rows.append({"category": "Data quality", "status": data_quality_status, "score": None, "explanation": "window warnings summarize data sufficiency and malformed/duplicate exclusions", "supporting_metrics": {"warning_count": sum(len(window.get("warnings") or []) for window in windows)}})

    status_rank = {"strong": 3, "acceptable": 2, "warning": 1, "insufficient_data": 0}
    lowest = min(category_rows, key=lambda row: status_rank.get(str(row.get("status") or ""), 0)) if category_rows else {"status": "insufficient_data"}
    overall_status = str(lowest.get("status") or "insufficient_data")
    if overall_status == "warning":
        overall_status = "unstable"
    return {"overall_validation_status": overall_status, "categories": category_rows}
