from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


@dataclass(frozen=True)
class SectorPolicy:
    max_sector_percent: float = 30.0
    unknown_sector_max_percent: float = 10.0


_CANONICAL = {
    "communication services": "Communication Services",
    "consumer discretionary": "Consumer Discretionary",
    "consumer staples": "Consumer Staples",
    "energy": "Energy",
    "financials": "Financials",
    "healthcare": "Healthcare",
    "industrials": "Industrials",
    "information technology": "Information Technology",
    "materials": "Materials",
    "real estate": "Real Estate",
    "utilities": "Utilities",
    "unknown": "Unknown",
}

_ALIASES = {
    "tech": "Information Technology",
    "technology": "Information Technology",
    "it": "Information Technology",
    "comm services": "Communication Services",
    "communication": "Communication Services",
    "consumer cyclical": "Consumer Discretionary",
    "consumer defensive": "Consumer Staples",
    "health care": "Healthcare",
    "financial": "Financials",
}


def normalize_sector(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "Unknown"
    if text in _CANONICAL:
        return _CANONICAL[text]
    if text in _ALIASES:
        return _ALIASES[text]
    return "Unknown"


def build_sector_exposure(
    positions: list[dict[str, Any]],
    account_equity: float,
) -> dict[str, float]:
    equity = max(_safe_float(account_equity, 0.0), 0.0)
    if equity <= 0:
        return {}

    totals: dict[str, float] = {}
    for row in list(positions or []):
        sector = normalize_sector(row.get("sector"))
        quantity = _safe_float(row.get("quantity"), 0.0)
        price = _safe_float(row.get("latest_price") or row.get("market_price") or row.get("avg_price") or row.get("entry_price"), 0.0)
        notional = max(quantity * price, _safe_float(row.get("notional"), 0.0))
        if notional <= 0:
            continue
        totals[sector] = totals.get(sector, 0.0) + notional

    return {sector: round((value / equity) * 100.0, 6) for sector, value in sorted(totals.items())}


def apply_sector_constraint(
    sector: str,
    target_notional: float,
    account_equity: float,
    current_sector_notional: dict[str, float],
    policy: SectorPolicy | None = None,
) -> dict[str, Any]:
    active = policy or SectorPolicy()
    equity = max(_safe_float(account_equity, 0.0), 0.0)
    proposed = max(_safe_float(target_notional, 0.0), 0.0)
    normalized = normalize_sector(sector)

    if equity <= 0:
        return {
            "sector": normalized,
            "adjusted_notional": 0.0,
            "reduced": False,
            "rejected": True,
            "reasons": ["non_positive_equity"],
            "warnings": [],
        }

    cap_pct = float(active.unknown_sector_max_percent if normalized == "Unknown" else active.max_sector_percent)
    cap_notional = equity * (cap_pct / 100.0)
    current_notional = max(_safe_float((current_sector_notional or {}).get(normalized), 0.0), 0.0)
    remaining_cap = max(cap_notional - current_notional, 0.0)

    reasons: list[str] = []
    warnings: list[str] = []
    adjusted = min(proposed, remaining_cap)
    reduced = adjusted + 1e-9 < proposed

    if reduced:
        reasons.append(f"sector_reduced:{normalized}")
    if remaining_cap <= 0:
        reasons.append(f"sector_cap_reached:{normalized}")
    if normalized == "Unknown" and reduced:
        warnings.append("unknown_sector_limit_applied")

    rejected = adjusted <= 0
    if rejected:
        reasons.append(f"sector_rejected:{normalized}")

    return {
        "sector": normalized,
        "adjusted_notional": round(max(adjusted, 0.0), 6),
        "reduced": reduced,
        "rejected": rejected,
        "reasons": reasons,
        "warnings": warnings,
        "cap_percent": cap_pct,
    }


def summarize_sector_snapshot(
    current_sector_notional: dict[str, float],
    proposed_sector_notional: dict[str, float],
    account_equity: float,
    policy: SectorPolicy | None = None,
) -> list[dict[str, Any]]:
    active = policy or SectorPolicy()
    equity = max(_safe_float(account_equity, 0.0), 0.0)
    keys = sorted(set((current_sector_notional or {}).keys()) | set((proposed_sector_notional or {}).keys()))

    result: list[dict[str, Any]] = []
    for key in keys:
        sector = normalize_sector(key)
        cap = float(active.unknown_sector_max_percent if sector == "Unknown" else active.max_sector_percent)
        cur_notional = max(_safe_float((current_sector_notional or {}).get(key), 0.0), 0.0)
        prop_notional = max(_safe_float((proposed_sector_notional or {}).get(key), 0.0), 0.0)
        cur_pct = (cur_notional / equity * 100.0) if equity > 0 else 0.0
        prop_pct = (prop_notional / equity * 100.0) if equity > 0 else 0.0
        result.append(
            {
                "sector": sector,
                "current_exposure_pct": round(cur_pct, 6),
                "proposed_exposure_pct": round(prop_pct, 6),
                "maximum_allowed_pct": round(cap, 6),
                "policy_passed": bool(prop_pct <= cap + 1e-9),
            }
        )

    return sorted(result, key=lambda item: (-float(item["proposed_exposure_pct"]), item["sector"]))
