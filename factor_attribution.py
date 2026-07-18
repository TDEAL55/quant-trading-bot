from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from config import (
    FACTOR_ATTRIBUTION_COMBINATION_MIN_SAMPLE_SIZE,
    FACTOR_ATTRIBUTION_MIN_SAMPLE_SIZE,
    FORWARD_RETURN_HORIZONS,
)
from evaluation_repository import MonitoringEvaluationRepository
from monitoring_db import MonitoringDatabase


HORIZON_LABELS = {1: "1d", 5: "5d", 10: "10d", 20: "20d"}

NUMERIC_FACTOR_SPECS = {
    "overall_score": {"label": "Overall Score", "bucket_type": "score"},
    "confidence": {"label": "Confidence", "bucket_type": "score"},
    "trend_score": {"label": "Trend", "bucket_type": "score"},
    "momentum_score": {"label": "Momentum", "bucket_type": "score"},
    "volume_score": {"label": "Volume", "bucket_type": "score"},
    "volatility_score": {"label": "Volatility", "bucket_type": "score"},
    "liquidity_score": {"label": "Liquidity", "bucket_type": "score"},
    "market_regime_score": {"label": "Market Regime Score", "bucket_type": "score"},
    "risk_quality_score": {"label": "Risk Quality", "bucket_type": "score"},
    "rank": {"label": "Scanner Rank", "bucket_type": "rank"},
}

CATEGORICAL_FACTOR_SPECS = {
    "signal": {"label": "Signal"},
    "market_regime": {"label": "Market Regime"},
    "sector": {"label": "Sector"},
}


def _as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _complete_rows(rows: list[dict[str, Any]], horizon: int) -> list[dict[str, Any]]:
    return [row for row in rows if str(row.get(f"forward_{horizon}d_status") or "").lower() == "complete"]


def _mean(values: list[float]) -> float | None:
    return round(statistics.mean(values), 6) if values else None


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 6) if values else None


def _variance(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    return round(statistics.pvariance(values), 6)


def _stddev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    return round(statistics.pstdev(values), 6)


def _quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 6)
    index = (len(ordered) - 1) * fraction
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return round(ordered[int(index)], 6)
    lower_value = ordered[lower]
    upper_value = ordered[upper]
    interpolated = lower_value + (upper_value - lower_value) * (index - lower)
    return round(interpolated, 6)


def _positive_rate(values: list[float]) -> float | None:
    if not values:
        return None
    return round(len([value for value in values if value > 0]) / len(values), 6)


def _mean_ci(values: list[float]) -> tuple[float | None, float | None]:
    if len(values) < 2:
        return None, None
    mean_value = statistics.mean(values)
    stddev = statistics.pstdev(values)
    if stddev == 0:
        rounded = round(mean_value, 6)
        return rounded, rounded
    margin = 1.96 * stddev / math.sqrt(len(values))
    return round(mean_value - margin, 6), round(mean_value + margin, 6)


def _pearson(xs: list[float], ys: list[float], minimum_sample_size: int) -> float | None:
    if len(xs) != len(ys) or len(xs) < minimum_sample_size:
        return None
    if len(set(xs)) <= 1 or len(set(ys)) <= 1:
        return None
    numerator = sum((x - statistics.mean(xs)) * (y - statistics.mean(ys)) for x, y in zip(xs, ys))
    denominator = math.sqrt(sum((x - statistics.mean(xs)) ** 2 for x in xs) * sum((y - statistics.mean(ys)) ** 2 for y in ys))
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


def _score_bucket(value: Any) -> str:
    numeric = _as_float(value, None)
    if numeric is None:
        return "unbucketed"
    if numeric < 20:
        return "0_19"
    if numeric < 40:
        return "20_39"
    if numeric < 60:
        return "40_59"
    if numeric < 80:
        return "60_79"
    return "80_100"


def _rank_bucket(value: Any) -> str:
    numeric = _as_float(value, None)
    if numeric is None:
        return "unbucketed"
    rank = int(numeric)
    if rank <= 1:
        return "top_1"
    if rank <= 3:
        return "top_3"
    if rank <= 5:
        return "top_5"
    if rank <= 10:
        return "top_10"
    return "above_10"


def _bucket_for_factor(factor_name: str, row: dict[str, Any]) -> str:
    if factor_name in CATEGORICAL_FACTOR_SPECS:
        return str(row.get(factor_name) or "Unknown")
    bucket_type = NUMERIC_FACTOR_SPECS.get(factor_name, {}).get("bucket_type", "score")
    value = row.get(factor_name)
    if bucket_type == "rank":
        return _rank_bucket(value)
    return _score_bucket(value)


def _bucket_sort_key(bucket: str) -> tuple[int, str]:
    ordered = ["0_19", "20_39", "40_59", "60_79", "80_100", "top_1", "top_3", "top_5", "top_10", "above_10", "unbucketed"]
    if bucket in ordered:
        return ordered.index(bucket), bucket
    return len(ordered), bucket


def _distribution(values: list[float]) -> dict[str, Any]:
    return {
        "sample_size": len(values),
        "mean": _mean(values),
        "variance": _variance(values),
        "stddev": _stddev(values),
        "minimum": round(min(values), 6) if values else None,
        "quartile_1": _quantile(values, 0.25),
        "median": _median(values),
        "quartile_3": _quantile(values, 0.75),
        "percentile_10": _quantile(values, 0.10),
        "percentile_90": _quantile(values, 0.90),
        "maximum": round(max(values), 6) if values else None,
    }


def _bucket_rows(rows: list[dict[str, Any]], factor_name: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_bucket_for_factor(factor_name, row)].append(row)
    bucket_names = sorted(grouped.keys(), key=_bucket_sort_key)
    summaries: list[dict[str, Any]] = []
    for bucket in bucket_names:
        bucket_group = grouped[bucket]
        factor_values = [_as_float(item.get(factor_name), None) for item in bucket_group]
        factor_values = [float(value) for value in factor_values if value is not None]
        raw_returns = [_as_float(item.get("_selected_forward_return"), None) for item in bucket_group]
        raw_returns = [float(value) for value in raw_returns if value is not None]
        excess_returns = [_as_float(item.get("_selected_excess_return"), None) for item in bucket_group]
        excess_returns = [float(value) for value in excess_returns if value is not None]
        ci_low, ci_high = _mean_ci(excess_returns)
        summaries.append(
            {
                "bucket": bucket,
                "sample_size": len(bucket_group),
                "average_factor_value": _mean(factor_values),
                "average_forward_return": _mean(raw_returns),
                "average_excess_return": _mean(excess_returns),
                "positive_return_rate": _positive_rate(raw_returns),
                "positive_excess_return_rate": _positive_rate(excess_returns),
                "forward_return_variance": _variance(raw_returns),
                "forward_return_stddev": _stddev(raw_returns),
                "excess_return_variance": _variance(excess_returns),
                "excess_return_stddev": _stddev(excess_returns),
                "quartile_1_forward_return": _quantile(raw_returns, 0.25),
                "median_forward_return": _median(raw_returns),
                "quartile_3_forward_return": _quantile(raw_returns, 0.75),
                "percentile_10_forward_return": _quantile(raw_returns, 0.10),
                "percentile_90_forward_return": _quantile(raw_returns, 0.90),
                "mean_excess_return_ci_low": ci_low,
                "mean_excess_return_ci_high": ci_high,
            }
        )
    return summaries


def _numeric_factor_correlations(rows: list[dict[str, Any]], factor_name: str, minimum_sample_size: int) -> dict[str, Any]:
    result: dict[str, Any] = {"factor": factor_name, "factor_label": NUMERIC_FACTOR_SPECS[factor_name]["label"]}
    raw_corrs: list[float] = []
    excess_corrs: list[float] = []
    for horizon in FORWARD_RETURN_HORIZONS:
        horizon_rows = _complete_rows(rows, horizon)
        pairs = [
            (float(_as_float(row.get(factor_name), 0.0)), float(_as_float(row.get(f"forward_{horizon}d_return"), 0.0)), float(_as_float(row.get(f"forward_{horizon}d_excess_return"), 0.0)))
            for row in horizon_rows
            if _as_float(row.get(factor_name), None) is not None
            and _as_float(row.get(f"forward_{horizon}d_return"), None) is not None
            and _as_float(row.get(f"forward_{horizon}d_excess_return"), None) is not None
        ]
        xs = [pair[0] for pair in pairs]
        raws = [pair[1] for pair in pairs]
        excesses = [pair[2] for pair in pairs]
        raw_corr = _pearson(xs, raws, minimum_sample_size)
        excess_corr = _pearson(xs, excesses, minimum_sample_size)
        result[f"{HORIZON_LABELS[horizon]}_return_correlation"] = raw_corr
        result[f"{HORIZON_LABELS[horizon]}_excess_correlation"] = excess_corr
        if raw_corr is not None:
            raw_corrs.append(raw_corr)
        if excess_corr is not None:
            excess_corrs.append(excess_corr)
    result["average_abs_return_correlation"] = _mean([abs(value) for value in raw_corrs])
    result["average_abs_excess_correlation"] = _mean([abs(value) for value in excess_corrs])
    sign_values = [1 if value > 0 else -1 for value in excess_corrs if value != 0]
    result["consistency_score"] = round(abs(sum(sign_values)) / len(sign_values), 6) if sign_values else None
    predictive_strength = []
    if result["average_abs_return_correlation"] is not None:
        predictive_strength.append(result["average_abs_return_correlation"])
    if result["average_abs_excess_correlation"] is not None:
        predictive_strength.append(result["average_abs_excess_correlation"])
    result["predictive_strength_score"] = _mean(predictive_strength)
    return result


def _diminishing_returns(bucket_summaries: list[dict[str, Any]], minimum_sample_size: int) -> bool | None:
    usable = [row for row in bucket_summaries if row.get("sample_size", 0) >= minimum_sample_size and row.get("average_excess_return") is not None]
    if len(usable) < 2:
        return None
    tail = usable[-2:]
    return bool((tail[-1].get("average_excess_return") or 0.0) <= (tail[-2].get("average_excess_return") or 0.0))


def _bucket_spread_score(bucket_summaries: list[dict[str, Any]], minimum_sample_size: int) -> float | None:
    usable = [row for row in bucket_summaries if row.get("sample_size", 0) >= minimum_sample_size and row.get("average_excess_return") is not None]
    if len(usable) < 2:
        return None
    values = [float(row["average_excess_return"]) for row in usable]
    return round(max(values) - min(values), 6)


def _factor_summary(
    factor_name: str,
    factor_label: str,
    bucket_analysis: dict[str, list[dict[str, Any]]],
    correlations: dict[str, Any] | None,
    minimum_sample_size: int,
) -> dict[str, Any]:
    spreads = [_bucket_spread_score(rows, minimum_sample_size) for rows in bucket_analysis.values()]
    valid_spreads = [value for value in spreads if value is not None]
    predictive_inputs: list[float] = []
    if correlations and correlations.get("predictive_strength_score") is not None:
        predictive_inputs.append(float(correlations["predictive_strength_score"]))
    predictive_inputs.extend([abs(value) for value in valid_spreads])
    return {
        "factor": factor_name,
        "factor_label": factor_label,
        "predictive_strength_score": _mean(predictive_inputs),
        "average_bucket_spread": _mean(valid_spreads),
        "consistency_score": None if correlations is None else correlations.get("consistency_score"),
        "diminishing_returns_detected": any(value is True for value in (_diminishing_returns(rows, minimum_sample_size) for rows in bucket_analysis.values())),
    }


def _selected_rows_for_horizon(rows: list[dict[str, Any]], horizon: int) -> list[dict[str, Any]]:
    selected = []
    for row in _complete_rows(rows, horizon):
        copied = dict(row)
        copied["_selected_forward_return"] = row.get(f"forward_{horizon}d_return")
        copied["_selected_excess_return"] = row.get(f"forward_{horizon}d_excess_return")
        selected.append(copied)
    return selected


def _combination_key(row: dict[str, Any]) -> str:
    return " | ".join(
        [
            f"score={_score_bucket(row.get('overall_score'))}",
            f"confidence={_score_bucket(row.get('confidence'))}",
            f"trend={_score_bucket(row.get('trend_score'))}",
            f"signal={str(row.get('signal') or 'Unknown')}",
            f"regime={str(row.get('market_regime') or 'Unknown')}",
        ]
    )


def _top_factor_combinations(rows: list[dict[str, Any]], minimum_sample_size: int) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_combination_key(row)].append(row)
    results = []
    for key, grouped_rows in grouped.items():
        if len(grouped_rows) < minimum_sample_size:
            continue
        raw_returns = [float(row["_selected_forward_return"]) for row in grouped_rows if row.get("_selected_forward_return") is not None]
        excess_returns = [float(row["_selected_excess_return"]) for row in grouped_rows if row.get("_selected_excess_return") is not None]
        results.append(
            {
                "combination": key,
                "sample_size": len(grouped_rows),
                "average_forward_return": _mean(raw_returns),
                "average_excess_return": _mean(excess_returns),
                "positive_return_rate": _positive_rate(raw_returns),
                "positive_excess_return_rate": _positive_rate(excess_returns),
            }
        )
    results.sort(key=lambda row: (-(row.get("average_excess_return") or -999.0), -(row.get("sample_size") or 0), row.get("combination") or ""))
    return results[:10]


def build_factor_attribution_analytics(
    rows: list[dict[str, Any]],
    minimum_sample_size: int = FACTOR_ATTRIBUTION_MIN_SAMPLE_SIZE,
    combination_min_sample_size: int = FACTOR_ATTRIBUTION_COMBINATION_MIN_SAMPLE_SIZE,
) -> dict[str, Any]:
    if not rows:
        return {
            "factor_bucket_analysis": {},
            "factor_distributions": {},
            "factor_correlations": [],
            "feature_importance_summary": [],
            "strongest_predictive_factors": [],
            "weakest_predictive_factors": [],
            "minimum_sample_warnings": [],
            "top_factor_combinations": {},
        }
    factor_bucket_analysis: dict[str, dict[str, list[dict[str, Any]]]] = {}
    factor_distributions: dict[str, dict[str, Any]] = {}
    factor_correlations: list[dict[str, Any]] = []
    factor_summaries: list[dict[str, Any]] = []
    minimum_sample_warnings: list[dict[str, Any]] = []
    top_factor_combinations: dict[str, list[dict[str, Any]]] = {}

    for factor_name, spec in NUMERIC_FACTOR_SPECS.items():
        factor_values = [float(_as_float(row.get(factor_name), 0.0)) for row in rows if _as_float(row.get(factor_name), None) is not None]
        factor_distributions[factor_name] = _distribution(factor_values)
        factor_correlations.append(_numeric_factor_correlations(rows, factor_name, minimum_sample_size))

    for factor_name, spec in {**NUMERIC_FACTOR_SPECS, **CATEGORICAL_FACTOR_SPECS}.items():
        per_horizon: dict[str, list[dict[str, Any]]] = {}
        for horizon in FORWARD_RETURN_HORIZONS:
            selected_rows = _selected_rows_for_horizon(rows, horizon)
            summaries = _bucket_rows(selected_rows, factor_name)
            per_horizon[HORIZON_LABELS[horizon]] = summaries
            for summary in summaries:
                if int(summary.get("sample_size") or 0) < minimum_sample_size:
                    minimum_sample_warnings.append(
                        {
                            "factor": factor_name,
                            "factor_label": spec.get("label", factor_name),
                            "horizon": HORIZON_LABELS[horizon],
                            "bucket": summary.get("bucket"),
                            "sample_size": summary.get("sample_size", 0),
                            "minimum_sample_size": minimum_sample_size,
                        }
                    )
        factor_bucket_analysis[factor_name] = per_horizon
        correlation_row = next((row for row in factor_correlations if row.get("factor") == factor_name), None)
        factor_summaries.append(_factor_summary(factor_name, spec.get("label", factor_name), per_horizon, correlation_row, minimum_sample_size))

    for horizon in FORWARD_RETURN_HORIZONS:
        selected_rows = _selected_rows_for_horizon(rows, horizon)
        top_factor_combinations[HORIZON_LABELS[horizon]] = _top_factor_combinations(selected_rows, combination_min_sample_size)

    ranked_summaries = sorted(
        factor_summaries,
        key=lambda row: (
            -(row.get("predictive_strength_score") or -999.0),
            -(row.get("average_bucket_spread") or -999.0),
            row.get("factor_label") or "",
        ),
    )

    return {
        "factor_bucket_analysis": factor_bucket_analysis,
        "factor_distributions": factor_distributions,
        "factor_correlations": factor_correlations,
        "feature_importance_summary": ranked_summaries,
        "strongest_predictive_factors": ranked_summaries[:5],
        "weakest_predictive_factors": list(reversed(ranked_summaries[-5:])) if ranked_summaries else [],
        "minimum_sample_warnings": minimum_sample_warnings,
        "top_factor_combinations": top_factor_combinations,
    }


def fetch_factor_attribution_dashboard_payload(
    database_url: str | None,
    selected_horizon: str | None = None,
    selected_factor: str | None = None,
    database_factory=MonitoringDatabase,
) -> dict[str, Any]:
    repository = MonitoringEvaluationRepository(database_url=database_url)
    payload = {
        "db_connected": repository.db.enabled,
        "selected_horizon": selected_horizon or "20d",
        "selected_factor": selected_factor or "overall_score",
        "factor_attribution_analytics": {
            "factor_bucket_analysis": {},
            "factor_distributions": {},
            "factor_correlations": [],
            "feature_importance_summary": [],
            "strongest_predictive_factors": [],
            "weakest_predictive_factors": [],
            "minimum_sample_warnings": [],
            "top_factor_combinations": {},
        },
        "factor_options": list(NUMERIC_FACTOR_SPECS.keys()) + list(CATEGORICAL_FACTOR_SPECS.keys()),
    }
    if not repository.db.enabled:
        return payload
    try:
        repository.db.ensure_schema()
        rows = repository.fetch_evaluation_rows_for_dashboard(limit=1000)
        payload["factor_attribution_analytics"] = build_factor_attribution_analytics(rows)
        return payload
    finally:
        repository.close()