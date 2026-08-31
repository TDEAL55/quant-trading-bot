from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Mapping


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


@dataclass(frozen=True)
class StockExecutionCostSettings:
    slippage_bps_per_side: float = 5.0
    sell_regulatory_fee_bps: float = 0.5
    short_borrow_annual_percent: float = 3.0

    def validate(self) -> None:
        if min(self.slippage_bps_per_side, self.sell_regulatory_fee_bps, self.short_borrow_annual_percent) < 0:
            raise ValueError("execution cost assumptions cannot be negative")


def settings_from_environment(environ: Mapping[str, str] | None = None) -> StockExecutionCostSettings:
    env = dict(environ or os.environ)
    settings = StockExecutionCostSettings(
        slippage_bps_per_side=_as_float(env.get("PAPER_ESTIMATED_SLIPPAGE_BPS_PER_SIDE"), 5.0),
        sell_regulatory_fee_bps=_as_float(env.get("PAPER_ESTIMATED_SELL_FEE_BPS"), 0.5),
        short_borrow_annual_percent=_as_float(env.get("PAPER_ESTIMATED_SHORT_BORROW_ANNUAL_PERCENT"), 3.0),
    )
    settings.validate()
    return settings


def estimate_stock_round_trip_costs(
    *,
    entry_notional: float,
    exit_notional: float,
    direction: str,
    holding_hours: float = 0.0,
    settings: StockExecutionCostSettings | None = None,
) -> dict[str, float | dict[str, float]]:
    """Estimate costs omitted by PAPER fills without changing actual-fill P/L."""
    policy = settings or StockExecutionCostSettings()
    policy.validate()
    entry_value = max(_as_float(entry_notional, 0.0), 0.0)
    exit_value = max(_as_float(exit_notional, 0.0), 0.0)
    slippage = (entry_value + exit_value) * policy.slippage_bps_per_side / 10_000.0
    sale_notional = entry_value if str(direction or "").strip().lower() == "short" else exit_value
    regulatory_fees = sale_notional * policy.sell_regulatory_fee_bps / 10_000.0
    borrow_cost = 0.0
    if str(direction or "").strip().lower() == "short":
        borrow_cost = entry_value * (policy.short_borrow_annual_percent / 100.0) * max(_as_float(holding_hours, 0.0), 0.0) / (24.0 * 365.0)
    fees = regulatory_fees + borrow_cost
    return {
        "estimated_slippage": round(slippage, 6),
        "estimated_regulatory_fees": round(regulatory_fees, 6),
        "estimated_borrow_cost": round(borrow_cost, 6),
        "estimated_fees": round(fees, 6),
        "estimated_total_cost": round(slippage + fees, 6),
        "assumptions": {
            "slippage_bps_per_side": policy.slippage_bps_per_side,
            "sell_regulatory_fee_bps": policy.sell_regulatory_fee_bps,
            "short_borrow_annual_percent": policy.short_borrow_annual_percent,
        },
    }
