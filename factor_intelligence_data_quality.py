from __future__ import annotations

from typing import Any


def evaluate_alignment_quality(
    aligned_rows: list[dict[str, Any]],
    forward_horizon: int,
    supported_versions: set[tuple[str, str]],
    minimum_universe_size: int,
) -> dict[str, Any]:
    reasons: dict[str, int] = {
        "duplicate_observation": 0,
        "missing_factor_value": 0,
        "invalid_factor_value": 0,
        "invalid_timestamp": 0,
        "post_label_cutoff": 0,
        "mismatched_symbol": 0,
        "mismatched_snapshot": 0,
        "unsupported_factor_version": 0,
        "insufficient_universe_size": 0,
        "stale_data": 0,
        "benchmark_alignment": 0,
        "regime_alignment": 0,
    }

    seen: set[str] = set()
    valid_rows = 0
    for row in aligned_rows:
        row_id = str(row.get("observation_id") or "")
        if row_id in seen:
            reasons["duplicate_observation"] += 1
            continue
        seen.add(row_id)

        if (str(row.get("factor_id")), str(row.get("factor_version"))) not in supported_versions:
            reasons["unsupported_factor_version"] += 1
            continue
        if row.get("value_status") != "valid":
            if row.get("value_status") == "missing":
                reasons["missing_factor_value"] += 1
            else:
                reasons["invalid_factor_value"] += 1
            continue
        if not str(row.get("observation_timestamp") or "").strip():
            reasons["invalid_timestamp"] += 1
            continue
        if int(row.get("universe_size") or 0) < int(minimum_universe_size):
            reasons["insufficient_universe_size"] += 1
            continue
        if row.get(f"forward_{forward_horizon}d_status") != "complete":
            reasons["benchmark_alignment"] += 1
            continue
        valid_rows += 1

    excluded_rows = len(aligned_rows) - valid_rows
    return {
        "total_rows_loaded": len(aligned_rows),
        "valid_rows": valid_rows,
        "excluded_rows": excluded_rows,
        "missing_value_count": reasons["missing_factor_value"],
        "invalid_value_count": reasons["invalid_factor_value"],
        "duplicate_count": reasons["duplicate_observation"],
        "stale_data_count": reasons["stale_data"],
        "unsupported_version_count": reasons["unsupported_factor_version"],
        "reasons_for_exclusion": reasons,
    }
