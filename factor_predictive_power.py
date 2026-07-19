from __future__ import annotations

from collections import defaultdict
from typing import Any

from factor_intelligence_utils import as_float, mean, median, pearson, short_hash, spearman


def _confidence_classification(sample_size: int, rank_corr: float | None) -> str:
    if sample_size < 20 or rank_corr is None:
        return "insufficient_data"
    strength = abs(rank_corr)
    if sample_size >= 200 and strength >= 0.15:
        return "high"
    if sample_size >= 100 and strength >= 0.10:
        return "medium"
    if strength >= 0.05:
        return "low"
    return "weak"


def compute_predictive_power(
    aligned_rows: list[dict[str, Any]],
    forward_horizon: int,
    minimum_sample_size: int,
    analysis_start_date: str | None,
    analysis_end_date: str | None,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in aligned_rows:
        grouped[(str(row.get("factor_id")), str(row.get("factor_version")))].append(row)

    results: list[dict[str, Any]] = []
    for (factor_id, factor_version), rows in sorted(grouped.items()):
        valid = []
        missing_count = 0
        for row in rows:
            factor_value = as_float(row.get("factor_value"), None)
            forward_return = as_float(row.get(f"forward_{forward_horizon}d_return"), None)
            excess_return = as_float(row.get(f"forward_{forward_horizon}d_excess_return"), None)
            if factor_value is None or forward_return is None:
                missing_count += 1
                continue
            valid.append((factor_value, forward_return, excess_return))

        sample_count = len(rows)
        valid_sample_count = len(valid)
        warnings: list[str] = []
        if valid_sample_count < minimum_sample_size:
            warnings.append("insufficient sample size")

        factor_values = [row[0] for row in valid]
        forward_values = [row[1] for row in valid]
        excess_values = [row[2] for row in valid if row[2] is not None]

        top_bucket_return = None
        bottom_bucket_return = None
        spread = None
        if valid_sample_count >= minimum_sample_size:
            ordered = sorted(valid, key=lambda item: item[0])
            bucket_size = max(valid_sample_count // 5, 1)
            bottom = [item[1] for item in ordered[:bucket_size]]
            top = [item[1] for item in ordered[-bucket_size:]]
            bottom_bucket_return = mean(bottom)
            top_bucket_return = mean(top)
            if top_bucket_return is not None and bottom_bucket_return is not None:
                spread = round(top_bucket_return - bottom_bucket_return, 6)

        rank_corr = spearman(factor_values, forward_values, minimum_sample_size)
        linear_corr = pearson(factor_values, forward_values, minimum_sample_size)
        confidence = _confidence_classification(valid_sample_count, rank_corr)
        status = "completed" if valid_sample_count >= minimum_sample_size else "insufficient_data"

        results.append(
            {
                "stat_id": short_hash([factor_id, factor_version, forward_horizon, analysis_start_date, analysis_end_date], length=32),
                "factor_id": factor_id,
                "factor_version": factor_version,
                "forward_horizon": int(forward_horizon),
                "sample_count": sample_count,
                "valid_sample_count": valid_sample_count,
                "missing_count": missing_count,
                "pearson_correlation": linear_corr,
                "spearman_correlation": rank_corr,
                "mean_forward_return": mean(forward_values),
                "median_forward_return": median(forward_values),
                "top_bucket_return": top_bucket_return,
                "bottom_bucket_return": bottom_bucket_return,
                "top_minus_bottom_spread": spread,
                "positive_return_rate": round(len([v for v in forward_values if v > 0]) / len(forward_values), 6) if forward_values else None,
                "mean_excess_return": mean(excess_values),
                "median_excess_return": median(excess_values),
                "confidence_classification": confidence,
                "status": status,
                "analysis_start_date": analysis_start_date,
                "analysis_end_date": analysis_end_date,
                "warnings": warnings,
                "metadata": {
                    "note": "correlations are descriptive and do not imply causation",
                },
            }
        )
    return results
