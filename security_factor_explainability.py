from __future__ import annotations

from typing import Any

from factor_intelligence_utils import as_float, mean


def build_security_explanation(
    symbol: str,
    snapshot_id: str,
    factor_rows: list[dict[str, Any]],
    factor_weights: dict[str, float],
    universe_size: int | None,
    final_rank: int | None,
    tolerance: float = 1e-6,
) -> dict[str, Any]:
    contributions: list[dict[str, Any]] = []
    unavailable: list[str] = []
    warnings: list[str] = []

    for row in sorted(factor_rows, key=lambda item: (str(item.get("factor_id")), str(item.get("factor_version")))):
        factor_id = str(row.get("factor_id") or "")
        weight = float(factor_weights.get(factor_id, 0.0))
        raw_value = as_float(row.get("factor_value"), None)
        normalized = as_float(row.get("normalized_value"), None)
        percentile = as_float(row.get("percentile_rank"), None)

        if raw_value is None or normalized is None:
            unavailable.append(factor_id)
            continue

        contribution = round(weight * normalized, 6)
        contributions.append(
            {
                "factor_id": factor_id,
                "factor_version": row.get("factor_version"),
                "raw_factor_value": raw_value,
                "normalized_value": normalized,
                "percentile_rank": percentile,
                "factor_weight": weight,
                "weighted_contribution": contribution,
                "direction": row.get("direction"),
                "factor_name": row.get("name"),
            }
        )

    contributions.sort(key=lambda item: (-(item["weighted_contribution"]), item["factor_id"]))
    overall_score = round(sum(item["weighted_contribution"] for item in contributions), 6)
    reconciliation_total = round(sum(item["weighted_contribution"] for item in contributions), 6)
    if abs(reconciliation_total - overall_score) > tolerance:
        warnings.append("score contribution reconciliation failed")

    positive = [item for item in contributions if item["weighted_contribution"] > 0]
    negative = [item for item in contributions if item["weighted_contribution"] < 0]

    confidence_inputs = [abs(item["weighted_contribution"]) for item in contributions]
    confidence = mean(confidence_inputs)
    confidence_label = "low"
    if confidence is None:
        confidence_label = "insufficient_data"
    elif confidence >= 0.20:
        confidence_label = "high"
    elif confidence >= 0.10:
        confidence_label = "medium"

    return {
        "symbol": symbol.upper(),
        "snapshot_id": snapshot_id,
        "overall_score": overall_score,
        "final_rank": final_rank,
        "universe_size": universe_size,
        "factor_contributions": contributions,
        "positive_contributors": positive,
        "negative_contributors": negative,
        "unavailable_factors": sorted(unavailable),
        "confidence": confidence_label,
        "warnings": warnings,
        "score_calculation_reconciliation": {
            "sum_of_contributions": reconciliation_total,
            "reported_score": overall_score,
            "difference": round(reconciliation_total - overall_score, 12),
            "tolerance": tolerance,
            "within_tolerance": abs(reconciliation_total - overall_score) <= tolerance,
        },
    }
