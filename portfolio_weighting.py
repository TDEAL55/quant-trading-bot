from __future__ import annotations

import math
from typing import Any


WEIGHTING_METHODS = {
    "equal_weight",
    "score_proportional",
    "confidence_proportional",
    "rank_based",
    "inverse_volatility",
    "volatility_targeted",
    "risk_parity_like",
}


def _as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_positive(values: dict[str, float]) -> dict[str, float]:
    total = sum(max(v, 0.0) for v in values.values())
    if total <= 0:
        return {}
    return {k: max(v, 0.0) / total for k, v in values.items() if max(v, 0.0) > 0}


def _proxy_volatility_measure(row: dict[str, Any]) -> float | None:
    direct = _as_float(row.get("volatility_measure"), None)
    if direct is not None and direct > 0:
        return direct
    # If realized volatility is not stored directly, use a transparent proxy from
    # scanner volatility_score where higher score implies lower risk.
    score = _as_float(row.get("volatility_score"), None)
    if score is None:
        return None
    proxy = 101.0 - score
    if proxy <= 0:
        return None
    return proxy


def _estimate_portfolio_volatility(weights: dict[str, float], row_by_symbol: dict[str, dict[str, Any]]) -> float | None:
    variances = []
    for symbol, weight in weights.items():
        if weight <= 0:
            continue
        measure = _proxy_volatility_measure(row_by_symbol.get(symbol, {}))
        if measure is None or measure <= 0:
            return None
        variance = (measure / 100.0) ** 2
        variances.append((weight**2) * variance)
    if not variances:
        return None
    return math.sqrt(sum(variances))


def build_raw_weights(
    selected_rows: list[dict[str, Any]],
    method: str,
    target_volatility: float | None = None,
    max_gross_exposure: float = 1.0,
    allow_leverage: bool = False,
) -> dict[str, Any]:
    chosen_method = str(method or "equal_weight").strip().lower()
    if chosen_method not in WEIGHTING_METHODS:
        return {"status": "unavailable", "weights": {}, "warnings": [f"unknown method: {chosen_method}"]}

    rows = [row for row in selected_rows if str(row.get("symbol") or "").strip()]
    if not rows:
        return {"status": "insufficient_data", "weights": {}, "warnings": ["no eligible holdings"]}

    symbols = [str(row.get("symbol") or "").upper() for row in rows]
    row_by_symbol = {str(row.get("symbol") or "").upper(): row for row in rows}
    warnings: list[str] = []

    if chosen_method == "equal_weight":
        equal = 1.0 / len(symbols)
        return {"status": "ok", "weights": {symbol: equal for symbol in symbols}, "warnings": warnings, "normalization": "equal"}

    if chosen_method == "score_proportional":
        values = {symbol: max(_as_float(row_by_symbol[symbol].get("overall_score"), -1.0) or -1.0, 0.0) for symbol in symbols}
        normalized = _normalize_positive(values)
        if not normalized:
            return {"status": "insufficient_data", "weights": {}, "warnings": ["all eligible scores are null, invalid, or sum to zero"]}
        return {"status": "ok", "weights": normalized, "warnings": warnings, "normalization": "score/sum(score)"}

    if chosen_method == "confidence_proportional":
        values = {symbol: max(_as_float(row_by_symbol[symbol].get("confidence"), -1.0) or -1.0, 0.0) for symbol in symbols}
        normalized = _normalize_positive(values)
        if not normalized:
            return {"status": "insufficient_data", "weights": {}, "warnings": ["all eligible confidence values are null, invalid, or sum to zero"]}
        return {"status": "ok", "weights": normalized, "warnings": warnings, "normalization": "confidence/sum(confidence)"}

    if chosen_method == "rank_based":
        rank_values: dict[str, float] = {}
        for symbol in symbols:
            rank = _as_float(row_by_symbol[symbol].get("rank"), None)
            if rank is None or rank <= 0:
                continue
            rank_values[symbol] = 1.0 / rank
        normalized = _normalize_positive(rank_values)
        if not normalized:
            return {"status": "insufficient_data", "weights": {}, "warnings": ["no valid positive ranks for rank-based weighting"]}
        return {"status": "ok", "weights": normalized, "warnings": warnings, "normalization": "(1/rank)/sum(1/rank)"}

    if chosen_method in {"inverse_volatility", "risk_parity_like", "volatility_targeted"}:
        risk_inv: dict[str, float] = {}
        missing = 0
        for symbol in symbols:
            vol = _proxy_volatility_measure(row_by_symbol[symbol])
            if vol is None or vol <= 0:
                missing += 1
                continue
            risk_inv[symbol] = 1.0 / vol
        if not risk_inv:
            return {
                "status": "unavailable",
                "weights": {},
                "warnings": ["missing or invalid positive volatility values"],
                "volatility_source": "volatility_measure or proxy(101-volatility_score)",
            }
        normalized = _normalize_positive(risk_inv)
        if missing:
            warnings.append(f"excluded {missing} holdings with missing/invalid volatility")
        response: dict[str, Any] = {
            "status": "ok",
            "weights": normalized,
            "warnings": warnings,
            "volatility_source": "volatility_measure or proxy(101-volatility_score)",
            "normalization": "(1/vol)/sum(1/vol)",
        }

        if chosen_method == "risk_parity_like":
            response["method_detail"] = "simplified inverse-volatility approximation"
            return response

        if chosen_method == "inverse_volatility":
            return response

        # volatility_targeted
        if target_volatility is None or target_volatility <= 0:
            warnings.append("target volatility missing; using normalized inverse-volatility weights")
            return response

        estimate = _estimate_portfolio_volatility(normalized, row_by_symbol)
        if estimate is None or estimate <= 0:
            warnings.append("portfolio volatility estimate unavailable; no volatility scaling applied")
            return response

        scale = float(target_volatility) / estimate
        max_gross = float(max_gross_exposure)
        if not allow_leverage:
            scale = min(scale, 1.0)
        scale = max(0.0, min(scale, max_gross))
        scaled = {symbol: weight * scale for symbol, weight in normalized.items()}
        response["weights"] = scaled
        response["estimated_portfolio_volatility"] = estimate
        response["volatility_scale"] = scale
        response["normalization"] = "target-vol scaling over inverse-vol base"
        return response

    return {"status": "unavailable", "weights": {}, "warnings": [f"method not implemented: {chosen_method}"]}
