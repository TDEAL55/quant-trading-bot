from __future__ import annotations

from collections import defaultdict
from typing import Any

from factor_intelligence_utils import as_float, pearson, short_hash, spearman


def _classification(abs_corr: float | None, sample_count: int, minimum_sample_size: int) -> str:
    if sample_count < minimum_sample_size or abs_corr is None:
        return "insufficient_data"
    if abs_corr >= 0.98:
        return "near_duplicate"
    if abs_corr >= 0.85:
        return "high"
    if abs_corr >= 0.60:
        return "moderate"
    return "low"


def compute_factor_redundancy(
    aligned_rows: list[dict[str, Any]],
    minimum_sample_size: int,
) -> list[dict[str, Any]]:
    by_factor: dict[tuple[str, str], dict[tuple[int, str], float]] = defaultdict(dict)
    for row in aligned_rows:
        key = (str(row.get("factor_id") or ""), str(row.get("factor_version") or ""))
        row_key = (int(row.get("candidate_id") or 0), str(row.get("snapshot_id") or ""))
        value = as_float(row.get("factor_value"), None)
        if value is None:
            continue
        by_factor[key][row_key] = float(value)

    keys = sorted(by_factor.keys())
    results: list[dict[str, Any]] = []
    for i, (a_id, a_ver) in enumerate(keys):
        for b_id, b_ver in keys[i + 1 :]:
            a_values = by_factor[(a_id, a_ver)]
            b_values = by_factor[(b_id, b_ver)]
            shared = sorted(set(a_values).intersection(b_values))
            xs = [a_values[row_key] for row_key in shared]
            ys = [b_values[row_key] for row_key in shared]
            sample_count = len(shared)
            p = pearson(xs, ys, minimum_sample_size)
            s = spearman(xs, ys, minimum_sample_size)
            abs_corr = abs(s) if s is not None else (abs(p) if p is not None else None)
            classification = _classification(abs_corr, sample_count, minimum_sample_size)
            warnings: list[str] = []
            if classification == "near_duplicate":
                warnings.append("possible near-duplicate factor pair")
            if sample_count < minimum_sample_size:
                warnings.append("insufficient aligned sample")
            results.append(
                {
                    "redundancy_id": short_hash([a_id, a_ver, b_id, b_ver], length=32),
                    "factor_a_id": a_id,
                    "factor_a_version": a_ver,
                    "factor_b_id": b_id,
                    "factor_b_version": b_ver,
                    "aligned_sample_count": sample_count,
                    "pearson_correlation": p,
                    "spearman_correlation": s,
                    "absolute_correlation": abs_corr,
                    "redundancy_classification": classification,
                    "status": "completed" if classification != "insufficient_data" else "insufficient_data",
                    "warnings": warnings,
                }
            )

    return results
