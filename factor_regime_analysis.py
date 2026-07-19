from __future__ import annotations

from collections import defaultdict
from typing import Any

from factor_intelligence_utils import as_float, mean, short_hash, spearman


DEFAULT_REGIMES = ["bull", "bear", "high_volatility", "low_volatility", "sideways", "unknown"]


def _normalize_regime(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "unknown"
    if "bull" in text:
        return "bull"
    if "bear" in text or "risk_off" in text:
        return "bear"
    if "high" in text and "vol" in text:
        return "high_volatility"
    if "low" in text and "vol" in text:
        return "low_volatility"
    if "side" in text:
        return "sideways"
    return text


def compute_regime_statistics(
    aligned_rows: list[dict[str, Any]],
    direction_map: dict[str, str],
    forward_horizon: int,
    minimum_sample_size: int,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    known_factors: set[tuple[str, str]] = set()
    for row in aligned_rows:
        regime = _normalize_regime(row.get("market_regime") or row.get("regime_label"))
        factor_id = str(row.get("factor_id") or "")
        factor_version = str(row.get("factor_version") or "")
        known_factors.add((factor_id, factor_version))
        grouped[(factor_id, factor_version, regime)].append(row)

    results: list[dict[str, Any]] = []
    for (factor_id, factor_version, regime), rows in sorted(grouped.items()):
        pairs = []
        returns = []
        excess = []
        for row in rows:
            value = as_float(row.get("factor_value"), None)
            ret = as_float(row.get(f"forward_{forward_horizon}d_return"), None)
            ex = as_float(row.get(f"forward_{forward_horizon}d_excess_return"), None)
            if value is None or ret is None:
                continue
            pairs.append((value, ret))
            returns.append(ret)
            if ex is not None:
                excess.append(ex)

        sample_count = len(pairs)
        status = "completed" if sample_count >= minimum_sample_size else "insufficient_data"
        warnings: list[str] = []
        if sample_count < minimum_sample_size:
            warnings.append("insufficient regime sample")

        spread = None
        if sample_count >= minimum_sample_size:
            ordered = sorted(pairs, key=lambda item: item[0])
            bucket_size = max(sample_count // 5, 1)
            bottom = [item[1] for item in ordered[:bucket_size]]
            top = [item[1] for item in ordered[-bucket_size:]]
            spread = round((sum(top) / len(top)) - (sum(bottom) / len(bottom)), 6)

        rank_corr = spearman([item[0] for item in pairs], [item[1] for item in pairs], minimum_sample_size)
        expected_rate = None
        if rank_corr is not None:
            direction = direction_map.get(factor_id, "higher_is_better")
            if direction == "higher_is_better":
                expected_rate = 1.0 if rank_corr >= 0 else 0.0
            elif direction == "lower_is_better":
                expected_rate = 1.0 if rank_corr <= 0 else 0.0

        results.append(
            {
                "regime_stat_id": short_hash([factor_id, factor_version, regime, forward_horizon], length=32),
                "factor_id": factor_id,
                "factor_version": factor_version,
                "regime_label": regime,
                "sample_count": sample_count,
                "spearman_correlation": rank_corr,
                "top_minus_bottom_spread": spread,
                "positive_return_rate": round(len([v for v in returns if v > 0]) / len(returns), 6) if returns else None,
                "average_return": mean(returns),
                "average_excess_return": mean(excess),
                "stability_score": abs(rank_corr) if rank_corr is not None else None,
                "expected_direction_success_rate": expected_rate,
                "status": status,
                "warnings": warnings,
            }
        )

    # Ensure unknown regime rows are present when absent in source.
    keys = {(row["factor_id"], row["factor_version"], row["regime_label"]) for row in results}
    for factor_id, factor_version in sorted(known_factors):
        key = (factor_id, factor_version, "unknown")
        if key in keys:
            continue
        results.append(
            {
                "regime_stat_id": short_hash([factor_id, factor_version, "unknown", forward_horizon], length=32),
                "factor_id": factor_id,
                "factor_version": factor_version,
                "regime_label": "unknown",
                "sample_count": 0,
                "spearman_correlation": None,
                "top_minus_bottom_spread": None,
                "positive_return_rate": None,
                "average_return": None,
                "average_excess_return": None,
                "stability_score": None,
                "expected_direction_success_rate": None,
                "status": "insufficient_data",
                "warnings": ["unknown regime retained"],
            }
        )

    return sorted(results, key=lambda item: (item["factor_id"], item["factor_version"], item["regime_label"]))
