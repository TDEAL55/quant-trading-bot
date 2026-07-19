from __future__ import annotations

from collections import defaultdict
from typing import Any

from factor_intelligence_utils import mean, short_hash


SCORE_FORMULA = {
    "predictive_weight": 0.35,
    "stability_weight": 0.20,
    "regime_weight": 0.15,
    "sample_weight": 0.20,
    "redundancy_penalty_weight": 0.10,
}


def _confidence_label(overall_score: float | None, warnings: list[str], sample_count: int) -> str:
    if overall_score is None or sample_count < 20:
        return "insufficient_data"
    if any("small" in warning for warning in warnings):
        return "low"
    if overall_score >= 0.70:
        return "high"
    if overall_score >= 0.50:
        return "medium"
    return "low"


def build_scorecards(
    predictive_stats: list[dict[str, Any]],
    stability_results: list[dict[str, Any]],
    regime_stats: list[dict[str, Any]],
    redundancy_stats: list[dict[str, Any]],
    analysis_start_date: str | None,
    analysis_end_date: str | None,
) -> list[dict[str, Any]]:
    stability_by_factor = {
        (row["factor_id"], row["factor_version"]): row
        for row in stability_results
        if not row.get("per_window")
    }
    regime_grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in regime_stats:
        regime_grouped[(row["factor_id"], row["factor_version"])].append(row)

    redundancy_by_factor: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in redundancy_stats:
        key_a = (row["factor_a_id"], row["factor_a_version"])
        key_b = (row["factor_b_id"], row["factor_b_version"])
        redundancy_by_factor[key_a].append(row)
        redundancy_by_factor[key_b].append(row)

    scorecards: list[dict[str, Any]] = []
    for predictive in sorted(predictive_stats, key=lambda item: (item["factor_id"], item["factor_version"])):
        key = (predictive["factor_id"], predictive["factor_version"])
        predictive_score = abs(float(predictive.get("spearman_correlation") or 0.0))

        stability = stability_by_factor.get(key, {})
        stability_score = float(stability.get("stability_score") or 0.0)

        regime_rows = regime_grouped.get(key, [])
        regime_score = mean([abs(float(row.get("spearman_correlation") or 0.0)) for row in regime_rows]) or 0.0

        sample_count = int(predictive.get("valid_sample_count") or 0)
        missing_count = int(predictive.get("missing_count") or 0)
        sample_quality_score = 0.0
        if sample_count > 0:
            missing_penalty = min(missing_count / max(sample_count + missing_count, 1), 1.0)
            sample_quality_score = max(0.0, min(1.0, (sample_count / 200.0) * (1.0 - missing_penalty)))

        redundancy_rows = redundancy_by_factor.get(key, [])
        redundancy_penalty = 0.0
        if redundancy_rows:
            redundancy_penalty = max(abs(float(row.get("absolute_correlation") or 0.0)) for row in redundancy_rows)
            redundancy_penalty = min(1.0, redundancy_penalty)

        overall_score = (
            SCORE_FORMULA["predictive_weight"] * predictive_score
            + SCORE_FORMULA["stability_weight"] * stability_score
            + SCORE_FORMULA["regime_weight"] * regime_score
            + SCORE_FORMULA["sample_weight"] * sample_quality_score
            - SCORE_FORMULA["redundancy_penalty_weight"] * redundancy_penalty
        )
        overall_score = round(max(0.0, min(1.0, overall_score)), 6)

        warnings: list[str] = []
        if sample_count < 50:
            warnings.append("small samples")
        if stability.get("stability_classification") in {"unstable", "mixed", "insufficient_data"}:
            warnings.append("unstable windows")
        if regime_rows and any(row.get("status") == "insufficient_data" for row in regime_rows):
            warnings.append("regime dependency")
        if redundancy_penalty >= 0.85:
            warnings.append("high redundancy")
        if missing_count > sample_count:
            warnings.append("excessive missing values")

        evidence = [
            {"component": "predictive_rank_correlation", "value": round(predictive_score, 6)},
            {"component": "stability", "value": round(stability_score, 6)},
            {"component": "regime_consistency", "value": round(regime_score, 6)},
            {"component": "sample_quality", "value": round(sample_quality_score, 6)},
            {"component": "redundancy_penalty", "value": round(redundancy_penalty, 6)},
        ]
        strongest = sorted(evidence, key=lambda item: item["value"], reverse=True)[:2]
        weakest = sorted(evidence, key=lambda item: item["value"])[:2]

        scorecards.append(
            {
                "scorecard_id": short_hash([predictive["factor_id"], predictive["factor_version"], analysis_start_date, analysis_end_date], length=32),
                "factor_id": predictive["factor_id"],
                "factor_version": predictive["factor_version"],
                "predictive_score": round(predictive_score, 6),
                "stability_score": round(stability_score, 6),
                "regime_score": round(regime_score, 6),
                "sample_quality_score": round(sample_quality_score, 6),
                "redundancy_penalty": round(redundancy_penalty, 6),
                "overall_research_score": overall_score,
                "confidence_classification": _confidence_label(overall_score, warnings, sample_count),
                "strongest_evidence": strongest,
                "weakest_evidence": weakest,
                "warnings": warnings,
                "sample_count": sample_count,
                "analysis_start_date": analysis_start_date,
                "analysis_end_date": analysis_end_date,
                "formula": SCORE_FORMULA,
            }
        )

    return scorecards
