from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from factor_registry import FactorDefinition
from factor_intelligence_utils import as_float, percentile_ranks, short_hash, stddev


def _snapshot_id(row: dict[str, Any]) -> str:
    run_id = str(row.get("research_run_id") or "")
    observation_date = str(row.get("observation_date") or "")
    return f"{run_id}:{observation_date}"


def _observation_timestamp(row: dict[str, Any]) -> str:
    return str(row.get("candidate_created_at") or row.get("research_completed_at") or row.get("observation_date") or "")


def _value_status(value: float | None) -> str:
    if value is None:
        return "missing"
    if not math.isfinite(value):
        return "invalid"
    return "valid"


def build_factor_observations(
    rows: list[dict[str, Any]],
    factors: list[FactorDefinition],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    duplicates: set[str] = set()
    duplicate_count = 0
    missing_count = 0
    invalid_count = 0

    per_snapshot_factor_values: dict[tuple[str, str, str], list[tuple[int, float]]] = defaultdict(list)
    factor_field_map = {definition.factor_id: str((definition.metadata or {}).get("field") or definition.factor_id) for definition in factors}

    for row in rows:
        candidate_id = int(row.get("research_candidate_id") or 0)
        symbol = str(row.get("symbol") or "").upper()
        snapshot_id = _snapshot_id(row)
        observation_timestamp = _observation_timestamp(row)
        regime_label = str(row.get("market_regime") or "unknown")

        for definition in factors:
            field_name = factor_field_map[definition.factor_id]
            numeric = as_float(row.get(field_name), None)
            status = _value_status(numeric)
            if status == "missing":
                missing_count += 1
            elif status == "invalid":
                invalid_count += 1
            key_parts = [snapshot_id, candidate_id, symbol, definition.factor_id, definition.version, observation_timestamp]
            observation_id = short_hash(key_parts, length=32)
            if observation_id in duplicates:
                duplicate_count += 1
                continue
            duplicates.add(observation_id)

            if status == "valid":
                per_snapshot_factor_values[(snapshot_id, definition.factor_id, definition.version)].append((candidate_id, float(numeric)))

            observations.append(
                {
                    "observation_id": observation_id,
                    "snapshot_id": snapshot_id,
                    "candidate_id": candidate_id,
                    "symbol": symbol,
                    "factor_id": definition.factor_id,
                    "factor_version": definition.version,
                    "observation_timestamp": observation_timestamp,
                    "factor_value": numeric,
                    "normalized_value": None,
                    "percentile_rank": None,
                    "universe_size": None,
                    "regime_label": regime_label,
                    "data_freshness_timestamp": str(row.get("observation_date") or ""),
                    "value_status": status,
                    "metadata": {
                        "research_run_id": row.get("research_run_id"),
                        "field_name": field_name,
                    },
                }
            )

    # Assign deterministic cross-sectional percentiles/z-scores within snapshot x factor.
    observations_by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for obs in observations:
        observations_by_key[(obs["snapshot_id"], obs["factor_id"], obs["factor_version"])].append(obs)

    for key, grouped in observations_by_key.items():
        valid_items = [item for item in grouped if item.get("value_status") == "valid" and item.get("factor_value") is not None]
        valid_items.sort(key=lambda item: (float(item["factor_value"]), int(item["candidate_id"]), str(item["symbol"])))
        values = [float(item["factor_value"]) for item in valid_items]
        universe_size = len(values)
        percentiles = percentile_ranks(values)
        sd = stddev(values)
        mean_value = sum(values) / universe_size if universe_size else None

        for idx, item in enumerate(valid_items):
            item["percentile_rank"] = percentiles[idx]
            item["universe_size"] = universe_size
            if sd is None or sd == 0 or mean_value is None:
                item["normalized_value"] = 0.0 if mean_value is not None else None
            else:
                item["normalized_value"] = round((float(item["factor_value"]) - mean_value) / sd, 6)

        for item in grouped:
            if item.get("universe_size") is None:
                item["universe_size"] = universe_size

    summary = {
        "total_rows_loaded": len(rows) * len(factors),
        "valid_rows": len([obs for obs in observations if obs.get("value_status") == "valid"]),
        "excluded_rows": duplicate_count,
        "missing_value_count": missing_count,
        "invalid_value_count": invalid_count,
        "duplicate_count": duplicate_count,
        "stale_data_count": 0,
        "unsupported_version_count": 0,
        "reasons_for_exclusion": {
            "duplicate_observation": duplicate_count,
            "missing_value": missing_count,
            "invalid_value": invalid_count,
        },
    }
    return observations, summary
