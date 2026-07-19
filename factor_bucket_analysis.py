from __future__ import annotations

from collections import defaultdict
from typing import Any

from factor_intelligence_utils import as_float, mean, median, short_hash, spearman, stddev


def _assign_bucket_indexes(values: list[tuple[int, float]], bucket_count: int) -> dict[int, int]:
    ordered = sorted(values, key=lambda item: (item[1], item[0]))
    n = len(ordered)
    mapping: dict[int, int] = {}
    for index, (candidate_id, _) in enumerate(ordered):
        bucket = min((index * bucket_count) // max(n, 1), bucket_count - 1) + 1
        mapping[candidate_id] = bucket
    return mapping


def _monotonicity_score(bucket_means: list[float]) -> float | None:
    if len(bucket_means) < 2:
        return None
    xs = list(range(1, len(bucket_means) + 1))
    return spearman([float(x) for x in xs], bucket_means, minimum_sample_size=2)


def compute_bucket_statistics(
    aligned_rows: list[dict[str, Any]],
    forward_horizon: int,
    requested_bucket_count: int,
    minimum_sample_size: int,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in aligned_rows:
        grouped[(str(row.get("factor_id")), str(row.get("factor_version")))].append(row)

    results: list[dict[str, Any]] = []
    for (factor_id, factor_version), rows in sorted(grouped.items()):
        valid_rows = [
            row
            for row in rows
            if as_float(row.get("factor_value"), None) is not None and as_float(row.get(f"forward_{forward_horizon}d_return"), None) is not None
        ]
        sample_count = len(valid_rows)
        bucket_count = int(requested_bucket_count)
        warnings: list[str] = []
        status = "completed"

        if sample_count < minimum_sample_size:
            status = "insufficient_data"
            warnings.append("insufficient sample size")
            bucket_count = 1
        elif sample_count < bucket_count * 2:
            bucket_count = max(2, sample_count // 2)
            warnings.append("reduced bucket count due to small sample")

        candidate_values = [(int(row.get("candidate_id") or 0), float(row.get("factor_value"))) for row in valid_rows]
        bucket_map = _assign_bucket_indexes(candidate_values, max(bucket_count, 1))
        per_bucket: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in valid_rows:
            per_bucket[bucket_map[int(row.get("candidate_id") or 0)]].append(row)

        bucket_means: list[float] = []
        top_minus_bottom = None
        for bucket_number in range(1, max(bucket_count, 1) + 1):
            bucket_rows = per_bucket.get(bucket_number, [])
            returns = [float(row[f"forward_{forward_horizon}d_return"]) for row in bucket_rows if as_float(row.get(f"forward_{forward_horizon}d_return"), None) is not None]
            excesses = [float(row[f"forward_{forward_horizon}d_excess_return"]) for row in bucket_rows if as_float(row.get(f"forward_{forward_horizon}d_excess_return"), None) is not None]
            factors = [float(row["factor_value"]) for row in bucket_rows if as_float(row.get("factor_value"), None) is not None]
            avg_return = mean(returns)
            if avg_return is not None:
                bucket_means.append(avg_return)

            lower_bound = min(factors) if factors else None
            upper_bound = max(factors) if factors else None
            results.append(
                {
                    "bucket_id": short_hash([factor_id, factor_version, forward_horizon, bucket_count, bucket_number], length=32),
                    "factor_id": factor_id,
                    "factor_version": factor_version,
                    "forward_horizon": int(forward_horizon),
                    "bucket_count": int(bucket_count),
                    "bucket_number": int(bucket_number),
                    "lower_bound": round(lower_bound, 6) if lower_bound is not None else None,
                    "upper_bound": round(upper_bound, 6) if upper_bound is not None else None,
                    "observation_count": len(bucket_rows),
                    "average_forward_return": avg_return,
                    "median_forward_return": median(returns),
                    "positive_return_rate": round(len([v for v in returns if v > 0]) / len(returns), 6) if returns else None,
                    "average_excess_return": mean(excesses),
                    "return_volatility": stddev(returns),
                    "min_return": round(min(returns), 6) if returns else None,
                    "max_return": round(max(returns), 6) if returns else None,
                    "top_minus_bottom_spread": None,
                    "monotonicity_score": None,
                    "direction_consistency": None,
                    "bucket_coverage": round(len(bucket_rows) / sample_count, 6) if sample_count else 0.0,
                    "status": status,
                    "warnings": list(warnings),
                }
            )

        if len(bucket_means) >= 2:
            top_minus_bottom = round(bucket_means[-1] - bucket_means[0], 6)
            mono = _monotonicity_score(bucket_means)
            direction_consistency = None
            if mono is not None:
                direction_consistency = abs(float(mono))
            for row in results:
                if row["factor_id"] == factor_id and row["factor_version"] == factor_version and row["forward_horizon"] == int(forward_horizon):
                    row["top_minus_bottom_spread"] = top_minus_bottom
                    row["monotonicity_score"] = mono
                    row["direction_consistency"] = direction_consistency
        else:
            warnings.append("insufficient buckets for spread")
    return results
