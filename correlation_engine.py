from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Any


INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


@dataclass(frozen=True)
class CorrelationPolicy:
    lookback_days: int = 90
    min_overlap_days: int = 40
    max_correlation: float = 0.80
    allocation_reduction_factor: float = 0.50


def _normalize_symbol(symbol: Any) -> str:
    return str(symbol or "").strip().upper()


def _extract_series(price_history_by_symbol: dict[str, Any], symbol: str, lookback_days: int) -> dict[str, float]:
    rows = price_history_by_symbol.get(symbol) if isinstance(price_history_by_symbol, dict) else None
    if rows is None:
        return {}

    result: dict[str, float] = {}

    if isinstance(rows, dict):
        for date_key, value in rows.items():
            price = _safe_float(value, 0.0)
            if price > 0:
                result[str(date_key)] = price
    elif isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                date_key = row.get("date") or row.get("timestamp") or row.get("t")
                price = _safe_float(row.get("close") or row.get("price") or row.get("c"), 0.0)
                if date_key is not None and price > 0:
                    result[str(date_key)] = price
            else:
                # Fallback for simple numeric lists with index-based keys.
                idx = str(len(result))
                price = _safe_float(row, 0.0)
                if price > 0:
                    result[idx] = price

    if not result:
        return {}

    ordered_dates = sorted(result.keys())[-max(int(lookback_days), 1) :]
    return {key: result[key] for key in ordered_dates}


def _returns(series: dict[str, float]) -> dict[str, float]:
    dates = sorted(series.keys())
    if len(dates) < 2:
        return {}
    out: dict[str, float] = {}
    prev = _safe_float(series[dates[0]], 0.0)
    for key in dates[1:]:
        cur = _safe_float(series[key], 0.0)
        if prev > 0 and cur > 0:
            out[key] = (cur / prev) - 1.0
        prev = cur
    return out


def _pearson(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n < 2:
        return 0.0
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    dx = [a - mean_x for a in x]
    dy = [b - mean_y for b in y]
    cov = sum(a * b for a, b in zip(dx, dy))
    var_x = sum(a * a for a in dx)
    var_y = sum(b * b for b in dy)
    if var_x <= 0 or var_y <= 0:
        return 0.0
    return max(-1.0, min(1.0, cov / sqrt(var_x * var_y)))


def calculate_pair_correlation(
    symbol_a: str,
    symbol_b: str,
    price_history_by_symbol: dict[str, Any],
    policy: CorrelationPolicy | None = None,
) -> dict[str, Any]:
    active = policy or CorrelationPolicy()
    a = _normalize_symbol(symbol_a)
    b = _normalize_symbol(symbol_b)
    if not a or not b:
        return {
            "symbol_a": a,
            "symbol_b": b,
            "status": INSUFFICIENT_DATA,
            "overlap_days": 0,
            "correlation": None,
            "reason": "missing_symbol",
        }

    series_a = _extract_series(price_history_by_symbol, a, active.lookback_days)
    series_b = _extract_series(price_history_by_symbol, b, active.lookback_days)
    returns_a = _returns(series_a)
    returns_b = _returns(series_b)

    overlap_keys = sorted(set(returns_a.keys()) & set(returns_b.keys()))
    if len(overlap_keys) < int(active.min_overlap_days):
        return {
            "symbol_a": a,
            "symbol_b": b,
            "status": INSUFFICIENT_DATA,
            "overlap_days": len(overlap_keys),
            "correlation": None,
            "reason": "insufficient_overlap",
        }

    x = [returns_a[key] for key in overlap_keys]
    y = [returns_b[key] for key in overlap_keys]
    correlation = _pearson(x, y)
    return {
        "symbol_a": a,
        "symbol_b": b,
        "status": "OK",
        "overlap_days": len(overlap_keys),
        "correlation": round(float(correlation), 6),
        "reason": "",
    }


def assess_symbol_correlation(
    symbol: str,
    peer_symbols: list[str],
    price_history_by_symbol: dict[str, Any],
    policy: CorrelationPolicy | None = None,
) -> dict[str, Any]:
    active = policy or CorrelationPolicy()
    normalized_symbol = _normalize_symbol(symbol)
    peers = sorted({_normalize_symbol(item) for item in list(peer_symbols or []) if _normalize_symbol(item) and _normalize_symbol(item) != normalized_symbol})

    pairs: list[dict[str, Any]] = []
    valid: list[float] = []
    insufficient_count = 0
    high_pairs: list[dict[str, Any]] = []

    for peer in peers:
        row = calculate_pair_correlation(normalized_symbol, peer, price_history_by_symbol, active)
        pairs.append(row)
        value = row.get("correlation")
        if value is None:
            insufficient_count += 1
            continue
        val_f = _safe_float(value, 0.0)
        valid.append(val_f)
        if val_f > float(active.max_correlation):
            high_pairs.append(
                {
                    "peer_symbol": peer,
                    "correlation": round(val_f, 6),
                    "overlap_days": int(row.get("overlap_days") or 0),
                }
            )

    average = round(sum(valid) / len(valid), 6) if valid else None
    maximum = round(max(valid), 6) if valid else None

    return {
        "symbol": normalized_symbol,
        "pair_count": len(peers),
        "insufficient_pair_count": insufficient_count,
        "average_correlation": average,
        "maximum_correlation": maximum,
        "high_correlation_pairs": high_pairs,
        "pair_details": pairs,
        "status": "OK" if valid else INSUFFICIENT_DATA,
    }


def summarize_portfolio_correlation(
    symbols: list[str],
    price_history_by_symbol: dict[str, Any],
    policy: CorrelationPolicy | None = None,
) -> dict[str, Any]:
    active = policy or CorrelationPolicy()
    ordered = sorted({_normalize_symbol(item) for item in list(symbols or []) if _normalize_symbol(item)})

    pairs: list[dict[str, Any]] = []
    valid: list[float] = []
    insufficient_count = 0
    for idx, sym_a in enumerate(ordered):
        for sym_b in ordered[idx + 1 :]:
            row = calculate_pair_correlation(sym_a, sym_b, price_history_by_symbol, active)
            pairs.append(row)
            value = row.get("correlation")
            if value is None:
                insufficient_count += 1
                continue
            valid.append(_safe_float(value, 0.0))

    return {
        "symbol_count": len(ordered),
        "pair_count": len(pairs),
        "insufficient_pair_count": insufficient_count,
        "average_correlation": round(sum(valid) / len(valid), 6) if valid else None,
        "maximum_correlation": round(max(valid), 6) if valid else None,
        "status": "OK" if valid else INSUFFICIENT_DATA,
        "pair_details": pairs,
    }


def apply_correlation_reduction(
    target_notional: float,
    correlation_assessment: dict[str, Any],
    policy: CorrelationPolicy | None = None,
) -> dict[str, Any]:
    active = policy or CorrelationPolicy()
    current = max(_safe_float(target_notional, 0.0), 0.0)
    reasons: list[str] = []

    max_corr = correlation_assessment.get("maximum_correlation")
    status = str(correlation_assessment.get("status") or "")
    if max_corr is None or status == INSUFFICIENT_DATA:
        reasons.append("correlation_status:INSUFFICIENT_DATA")
        return {
            "adjusted_notional": current,
            "reduced": False,
            "rejected": False,
            "reasons": reasons,
        }

    max_corr_f = _safe_float(max_corr, 0.0)
    if max_corr_f <= float(active.max_correlation):
        return {
            "adjusted_notional": current,
            "reduced": False,
            "rejected": False,
            "reasons": reasons,
        }

    reduced_value = current * max(min(float(active.allocation_reduction_factor), 1.0), 0.0)
    reasons.append(f"correlation_reduced:max={max_corr_f:.4f}")
    rejected = reduced_value <= 0
    if rejected:
        reasons.append("correlation_rejected_after_reduction")

    return {
        "adjusted_notional": max(reduced_value, 0.0),
        "reduced": True,
        "rejected": rejected,
        "reasons": reasons,
    }
