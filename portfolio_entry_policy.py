from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Mapping


_STOCK_ASSET_CLASSES = {"", "equity", "stock", "us_equity"}


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _is_stock(symbol: str, payload: Mapping[str, Any]) -> bool:
    asset_class = str(payload.get("asset_class") or "").strip().lower()
    return "/" not in symbol and "crypto" not in asset_class and "option" not in asset_class and asset_class in _STOCK_ASSET_CLASSES


@dataclass(frozen=True)
class PortfolioEntryPolicySettings:
    maximum_gross_exposure_percent: float = 125.0
    maximum_open_stock_positions: int = 15
    minimum_cash: float = 0.0
    normalization_position_percent: float = 10.0

    def validate(self) -> None:
        if self.maximum_gross_exposure_percent <= 0:
            raise ValueError("maximum_gross_exposure_percent must be positive")
        if self.maximum_open_stock_positions <= 0:
            raise ValueError("maximum_open_stock_positions must be positive")
        if not 0 < self.normalization_position_percent <= 100:
            raise ValueError("normalization_position_percent must be in (0, 100]")


def settings_from_environment(environ: Mapping[str, str] | None = None) -> PortfolioEntryPolicySettings:
    env = dict(environ or os.environ)
    settings = PortfolioEntryPolicySettings(
        maximum_gross_exposure_percent=_as_float(env.get("PAPER_MAX_GROSS_EXPOSURE_PERCENT"), 125.0),
        maximum_open_stock_positions=_as_int(env.get("PAPER_MAX_OPEN_STOCK_POSITIONS"), 15),
        minimum_cash=_as_float(env.get("PAPER_MINIMUM_ENTRY_CASH"), 0.0),
        normalization_position_percent=_as_float(env.get("PAPER_NORMALIZATION_POSITION_PERCENT"), 10.0),
    )
    settings.validate()
    return settings


def evaluate_portfolio_entry_policy(
    account: Mapping[str, Any] | None,
    positions: Mapping[str, Mapping[str, Any]] | None,
    *,
    settings: PortfolioEntryPolicySettings | None = None,
) -> dict[str, Any]:
    """Block only new entries when the PAPER portfolio is overextended.

    This function never creates an order and never asks callers to liquidate.
    Existing exit policies remain independent and can continue reducing risk.
    """
    policy = settings or PortfolioEntryPolicySettings()
    policy.validate()
    snapshot = dict(account or {})
    equity = _as_float(snapshot.get("equity") or snapshot.get("portfolio_value"), 0.0)
    cash = _as_float(snapshot.get("cash"), 0.0)

    stock_rows: list[dict[str, Any]] = []
    gross_exposure = 0.0
    net_exposure = 0.0
    for raw_symbol, raw_payload in dict(positions or {}).items():
        symbol = str(raw_symbol or "").strip().upper()
        payload = dict(raw_payload or {})
        quantity = _as_float(payload.get("quantity"), 0.0)
        if not symbol or abs(quantity) <= 1e-8 or not _is_stock(symbol, payload):
            continue
        price = _as_float(payload.get("current_price") or payload.get("market_price") or payload.get("avg_price"), 0.0)
        signed_market_value = _as_float(payload.get("market_value"), quantity * price)
        if signed_market_value == 0.0 and price > 0:
            signed_market_value = quantity * price
        gross_exposure += abs(signed_market_value)
        net_exposure += signed_market_value
        stock_rows.append(
            {
                "symbol": symbol,
                "quantity": quantity,
                "market_value": signed_market_value,
            }
        )

    gross_percent = (gross_exposure / equity * 100.0) if equity > 0 else None
    net_percent = (net_exposure / equity * 100.0) if equity > 0 else None
    reasons: list[str] = []
    if equity <= 0:
        reasons.append("portfolio_equity_unavailable")
    if cash < policy.minimum_cash:
        reasons.append("cash_below_entry_floor")
    if gross_percent is not None and gross_percent > policy.maximum_gross_exposure_percent:
        reasons.append("gross_exposure_above_limit")
    if len(stock_rows) > policy.maximum_open_stock_positions:
        reasons.append("open_stock_positions_above_limit")

    normalization_rows: list[dict[str, Any]] = []
    if equity > 0:
        target_notional = equity * policy.normalization_position_percent / 100.0
        for row in stock_rows:
            exposure = abs(_as_float(row.get("market_value"), 0.0))
            allocation_percent = exposure / equity * 100.0
            if allocation_percent <= policy.normalization_position_percent + 1e-9:
                continue
            normalization_rows.append(
                {
                    "symbol": row["symbol"],
                    "direction": "SHORT" if _as_float(row.get("quantity"), 0.0) < 0 else "LONG",
                    "allocation_percent": round(allocation_percent, 6),
                    "target_percent": policy.normalization_position_percent,
                    "excess_notional": round(max(exposure - target_notional, 0.0), 2),
                    "action": "review_for_gradual_reduction_no_automatic_order",
                }
            )
    normalization_rows.sort(key=lambda row: (-float(row["allocation_percent"]), str(row["symbol"])))

    exit_only = bool(reasons)
    return {
        "new_entries_allowed": not exit_only,
        "exit_only": exit_only,
        "status": "EXIT_ONLY" if exit_only else "ENTRIES_ALLOWED",
        "reasons": reasons,
        "equity": round(equity, 2),
        "cash": round(cash, 2),
        "gross_exposure": round(gross_exposure, 2),
        "gross_exposure_percent": round(gross_percent, 6) if gross_percent is not None else None,
        "net_exposure_percent": round(net_percent, 6) if net_percent is not None else None,
        "open_stock_positions": len(stock_rows),
        "limits": {
            "maximum_gross_exposure_percent": policy.maximum_gross_exposure_percent,
            "maximum_open_stock_positions": policy.maximum_open_stock_positions,
            "minimum_cash": policy.minimum_cash,
            "normalization_position_percent": policy.normalization_position_percent,
        },
        "normalization_candidates": normalization_rows,
        "automatic_liquidation_enabled": False,
    }
