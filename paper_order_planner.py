from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _round_quantity(quantity: float, precision: int, allow_fractional: bool) -> float:
    if allow_fractional:
        return round(max(quantity, 0.0), int(precision))
    return float(max(int(quantity), 0))


@dataclass(frozen=True)
class OrderPlannerSettings:
    minimum_order_notional: float
    maximum_order_notional: float
    allow_fractional: bool
    quantity_precision: int
    rebalance_tolerance: float
    maximum_orders: int
    cash_buffer: float


def _normalize_positions(positions: Any) -> dict[str, dict[str, float]]:
    if isinstance(positions, dict):
        normalized = {}
        for symbol, item in positions.items():
            payload = item if isinstance(item, dict) else {}
            normalized[str(symbol).upper()] = {
                "quantity": _as_float(payload.get("quantity"), 0.0),
                "avg_price": _as_float(payload.get("avg_price"), 0.0),
            }
        return normalized
    result: dict[str, dict[str, float]] = {}
    for item in positions or []:
        symbol = str((item or {}).get("symbol") or "").upper()
        if not symbol:
            continue
        result[symbol] = {
            "quantity": _as_float((item or {}).get("quantity"), 0.0),
            "avg_price": _as_float((item or {}).get("avg_price"), 0.0),
        }
    return result


def plan_paper_orders(
    target_weights: dict[str, float],
    current_positions: dict[str, dict[str, float]] | list[dict[str, Any]],
    reference_prices: dict[str, float],
    portfolio_value: float,
    current_cash: float,
    settings: OrderPlannerSettings,
) -> dict[str, Any]:
    portfolio_value = max(_as_float(portfolio_value, 0.0), 0.0)
    current_cash = _as_float(current_cash, 0.0)
    tol_notional = abs(_as_float(settings.rebalance_tolerance, 0.0) * portfolio_value)

    positions = _normalize_positions(current_positions)
    symbols = sorted(set([str(symbol).upper() for symbol in target_weights.keys()]) | set(positions.keys()))

    planned: list[dict[str, Any]] = []
    holds: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []

    for symbol in symbols:
        target_weight = max(_as_float(target_weights.get(symbol), 0.0), 0.0)
        position = positions.get(symbol) or {"quantity": 0.0, "avg_price": 0.0}
        quantity_now = _as_float(position.get("quantity"), 0.0)

        reference_price = _as_float(reference_prices.get(symbol), _as_float(position.get("avg_price"), 0.0))
        if reference_price <= 0.0:
            rejections.append({"symbol": symbol, "reason": "missing_reference_price", "side": "HOLD"})
            continue

        target_notional = target_weight * portfolio_value
        current_notional = quantity_now * reference_price
        order_notional = target_notional - current_notional
        weight_delta = (order_notional / portfolio_value) if portfolio_value > 0 else 0.0

        if abs(order_notional) <= tol_notional:
            holds.append({"symbol": symbol, "reason": "within_rebalance_tolerance", "delta_notional": round(order_notional, 6)})
            continue

        if abs(order_notional) < abs(_as_float(settings.minimum_order_notional, 0.0)):
            holds.append({"symbol": symbol, "reason": "below_minimum_order_notional", "delta_notional": round(order_notional, 6)})
            continue

        side = "BUY" if order_notional > 0 else "SELL"
        notional = abs(order_notional)
        if notional > abs(_as_float(settings.maximum_order_notional, 0.0)) > 0:
            rejections.append({"symbol": symbol, "side": side, "reason": "above_maximum_order_notional", "notional": round(notional, 6)})
            continue

        raw_quantity = notional / reference_price
        quantity = _round_quantity(raw_quantity, precision=int(settings.quantity_precision), allow_fractional=bool(settings.allow_fractional))
        if quantity <= 0:
            holds.append({"symbol": symbol, "reason": "rounded_to_zero", "delta_notional": round(order_notional, 6)})
            continue

        planned.append(
            {
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "notional": round(quantity * reference_price, 6),
                "target_weight": round(target_weight, 10),
                "current_weight": round(current_notional / portfolio_value, 10) if portfolio_value > 0 else 0.0,
                "weight_delta": round(weight_delta, 10),
                "reference_price": round(reference_price, 10),
                "action": "increase" if side == "BUY" and current_notional > 0 else "buy" if side == "BUY" else "close" if target_weight <= 0 else "reduce",
            }
        )

    sells = sorted([item for item in planned if item["side"] == "SELL"], key=lambda item: item["symbol"])
    buys = sorted([item for item in planned if item["side"] == "BUY"], key=lambda item: item["symbol"])

    approved_orders: list[dict[str, Any]] = []
    rejected_orders = list(rejections)
    estimated_cash = current_cash + sum(item["notional"] for item in sells)
    min_cash_required = max(portfolio_value * _as_float(settings.cash_buffer, 0.0), 0.0)

    for order in sells + buys:
        if len(approved_orders) >= max(int(settings.maximum_orders), 0):
            rejected_orders.append({"symbol": order["symbol"], "side": order["side"], "reason": "maximum_orders_exceeded"})
            continue

        if order["side"] == "BUY":
            remaining_cash_after = estimated_cash - order["notional"]
            if remaining_cash_after < min_cash_required:
                rejected_orders.append({"symbol": order["symbol"], "side": order["side"], "reason": "insufficient_cash_buffer"})
                continue
            estimated_cash = remaining_cash_after
        else:
            estimated_cash += order["notional"]

        approved_orders.append(order)

    ordered = [item for item in approved_orders if item["side"] == "SELL"] + [item for item in approved_orders if item["side"] == "BUY"]
    estimated_turnover = round(sum(item["notional"] for item in ordered) / portfolio_value, 6) if portfolio_value > 0 else 0.0

    return {
        "orders": ordered,
        "holds": sorted(holds, key=lambda item: item.get("symbol") or ""),
        "rejections": sorted(rejected_orders, key=lambda item: (str(item.get("side") or ""), str(item.get("symbol") or ""))),
        "summary": {
            "target_holding_count": len([symbol for symbol, weight in target_weights.items() if _as_float(weight, 0.0) > 0]),
            "proposed_order_count": len(ordered) + len(rejected_orders),
            "approved_order_count": len(ordered),
            "rejected_order_count": len(rejected_orders),
            "estimated_turnover": estimated_turnover,
            "estimated_post_trade_cash": round(estimated_cash, 6),
        },
    }
