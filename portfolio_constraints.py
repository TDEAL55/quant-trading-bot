from __future__ import annotations

from typing import Any


def _as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _clean_weights(raw_weights: dict[str, Any]) -> tuple[dict[str, float], int]:
    cleaned: dict[str, float] = {}
    invalid = 0
    for symbol, value in raw_weights.items():
        weight = _as_float(value, None)
        if weight is None or weight <= 0:
            invalid += 1
            continue
        cleaned[str(symbol).upper()] = float(weight)
    return cleaned, invalid


def _normalize_to_target(weights: dict[str, float], target: float) -> dict[str, float]:
    total = sum(weights.values())
    if total <= 0:
        return {}
    scale = float(target) / total
    return {symbol: value * scale for symbol, value in weights.items()}


def _redistribute(
    weights: dict[str, float],
    capacity: dict[str, float],
    amount: float,
) -> float:
    if amount <= 0:
        return 0.0
    candidates = {symbol: room for symbol, room in capacity.items() if room > 1e-12 and symbol in weights}
    if not candidates:
        return amount
    denom = sum(candidates.values())
    if denom <= 0:
        return amount
    remaining = amount
    for symbol, room in candidates.items():
        add = min(room, amount * (room / denom))
        weights[symbol] += add
        remaining -= add
    return max(0.0, remaining)


def apply_portfolio_constraints(
    raw_weights: dict[str, Any],
    row_by_symbol: dict[str, dict[str, Any]],
    max_position_weight: float = 0.25,
    sector_cap: float = 0.4,
    min_holdings: int = 1,
    max_holdings: int | None = None,
    max_gross_exposure: float = 1.0,
    allow_cash: bool = True,
    normalization_tolerance: float = 1e-6,
    max_iterations: int = 25,
) -> dict[str, Any]:
    warnings: list[str] = []
    weights, invalid_count = _clean_weights(raw_weights)
    if invalid_count:
        warnings.append(f"removed {invalid_count} invalid or non-positive weights")
    if not weights:
        return {
            "status": "insufficient_data",
            "weights": {},
            "cash_weight": 1.0 if allow_cash else 0.0,
            "warnings": warnings + ["no valid weights after cleaning"],
        }

    gross_target = min(max(float(max_gross_exposure), 0.0), 1.0)
    weights = _normalize_to_target(weights, gross_target)

    # Optional holding-count trim by smallest preliminary weights.
    if max_holdings is not None and max_holdings > 0 and len(weights) > int(max_holdings):
        ranked = sorted(weights.items(), key=lambda item: item[1], reverse=True)
        kept = dict(ranked[: int(max_holdings)])
        removed = len(weights) - len(kept)
        weights = _normalize_to_target(kept, gross_target)
        warnings.append(f"trimmed {removed} holdings to respect max_holdings")

    cap = max(float(max_position_weight), 0.0)
    sector_limit = max(float(sector_cap), 0.0)

    for _ in range(max(int(max_iterations), 1)):
        changed = False

        # Position cap pass.
        capped_total = 0.0
        for symbol, weight in list(weights.items()):
            if weight > cap:
                capped_total += weight - cap
                weights[symbol] = cap
                changed = True
        if capped_total > normalization_tolerance:
            capacity = {symbol: max(cap - weight, 0.0) for symbol, weight in weights.items()}
            remainder = _redistribute(weights, capacity, capped_total)
            if remainder > normalization_tolerance:
                warnings.append("position-cap binding caused residual allocation")

        # Sector cap pass.
        sector_map = {symbol: str((row_by_symbol.get(symbol) or {}).get("sector") or "Unknown") for symbol in weights}
        sector_weights: dict[str, float] = {}
        for symbol, weight in weights.items():
            sector = sector_map[symbol]
            sector_weights[sector] = sector_weights.get(sector, 0.0) + weight

        freed = 0.0
        for sector, total in sorted(sector_weights.items()):
            if total <= sector_limit + normalization_tolerance:
                continue
            changed = True
            scale = sector_limit / total if total > 0 else 0.0
            for symbol in [name for name, sec in sector_map.items() if sec == sector]:
                original = weights[symbol]
                weights[symbol] = original * scale
                freed += original - weights[symbol]
        if freed > normalization_tolerance:
            sector_weights = {}
            for symbol, weight in weights.items():
                sector = sector_map[symbol]
                sector_weights[sector] = sector_weights.get(sector, 0.0) + weight
            capacity = {}
            for symbol, weight in weights.items():
                symbol_room = max(cap - weight, 0.0)
                sector_room = max(sector_limit - sector_weights.get(sector_map[symbol], 0.0), 0.0)
                capacity[symbol] = min(symbol_room, sector_room)
            remainder = _redistribute(weights, capacity, freed)
            if remainder > normalization_tolerance:
                warnings.append("sector-cap binding caused residual allocation")

        if not changed:
            break
    else:
        warnings.append("maximum redistribution iterations reached")

    weights = {symbol: weight for symbol, weight in weights.items() if weight > normalization_tolerance}

    invested = sum(weights.values())
    if invested <= normalization_tolerance:
        return {
            "status": "insufficient_data",
            "weights": {},
            "cash_weight": 1.0 if allow_cash else 0.0,
            "warnings": warnings + ["all weights removed by constraints"],
        }

    if invested > gross_target + normalization_tolerance:
        weights = _normalize_to_target(weights, gross_target)
        invested = sum(weights.values())

    if len(weights) < int(max(min_holdings, 0)):
        warnings.append("insufficient holdings after constraints")

    cash_weight = max(0.0, 1.0 - invested) if allow_cash else 0.0
    if not allow_cash and abs(invested - 1.0) > max(normalization_tolerance, 1e-6):
        weights = _normalize_to_target(weights, 1.0)
        invested = sum(weights.values())

    final_total = invested + cash_weight
    if abs(final_total - 1.0) > max(normalization_tolerance, 1e-6) and allow_cash:
        cash_weight = max(0.0, 1.0 - invested)

    return {
        "status": "ok" if len(weights) >= int(max(min_holdings, 0)) else "insufficient_holdings",
        "weights": dict(sorted(weights.items())),
        "invested_weight": round(invested, 10),
        "cash_weight": round(cash_weight, 10),
        "warnings": warnings,
    }
