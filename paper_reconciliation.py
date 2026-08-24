from __future__ import annotations

from typing import Any


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _normalize_positions(raw: Any) -> dict[str, dict[str, float]]:
    if isinstance(raw, dict):
        result = {}
        for symbol, payload in raw.items():
            item = payload if isinstance(payload, dict) else {}
            result[str(symbol).upper()] = {
                "quantity": _as_float(item.get("quantity"), 0.0),
                "weight": _as_float(item.get("weight"), 0.0),
            }
        return result
    result: dict[str, dict[str, float]] = {}
    for payload in raw or []:
        symbol = str((payload or {}).get("symbol") or "").upper()
        if not symbol:
            continue
        result[symbol] = {
            "quantity": _as_float((payload or {}).get("quantity"), 0.0),
            "weight": _as_float((payload or {}).get("weight"), 0.0),
        }
    return result


def reconcile_paper_positions(
    planned_positions: dict[str, dict[str, float]] | list[dict[str, Any]],
    actual_positions: dict[str, dict[str, float]] | list[dict[str, Any]],
    expected_cash: float,
    actual_cash: float,
    expected_buying_power: float | None,
    actual_buying_power: float,
    orders: list[dict[str, Any]],
    tolerance: float,
) -> dict[str, Any]:
    planned = _normalize_positions(planned_positions)
    actual = _normalize_positions(actual_positions)
    symbols = sorted(set(planned.keys()) | set(actual.keys()))

    mismatches = []
    position_rows = []
    mismatch_count = 0

    for symbol in symbols:
        p = planned.get(symbol, {"quantity": 0.0, "weight": 0.0})
        a = actual.get(symbol, {"quantity": 0.0, "weight": 0.0})
        quantity_diff = _as_float(a.get("quantity"), 0.0) - _as_float(p.get("quantity"), 0.0)
        weight_diff = _as_float(a.get("weight"), 0.0) - _as_float(p.get("weight"), 0.0)
        status = "matched" if abs(quantity_diff) <= tolerance and abs(weight_diff) <= tolerance else "mismatch"
        if status == "mismatch":
            mismatch_count += 1
            mismatches.append({"symbol": symbol, "quantity_difference": round(quantity_diff, 6), "weight_difference": round(weight_diff, 6)})
        position_rows.append(
            {
                "symbol": symbol,
                "planned_quantity": round(_as_float(p.get("quantity"), 0.0), 6),
                "actual_quantity": round(_as_float(a.get("quantity"), 0.0), 6),
                "quantity_difference": round(quantity_diff, 6),
                "planned_weight": round(_as_float(p.get("weight"), 0.0), 6),
                "actual_weight": round(_as_float(a.get("weight"), 0.0), 6),
                "weight_difference": round(weight_diff, 6),
                "reconciliation_status": status,
            }
        )

    cash_diff = round(_as_float(actual_cash, 0.0) - _as_float(expected_cash, 0.0), 6)
    buying_power_checked = expected_buying_power is not None
    buying_power_diff = (
        round(_as_float(actual_buying_power, 0.0) - _as_float(expected_buying_power, 0.0), 6)
        if buying_power_checked
        else None
    )

    pending_statuses = {"new", "accepted", "submitted", "pending", "pending_new", "partially_filled"}
    failed_statuses = {"failed", "rejected", "expired", "canceled", "cancelled", "submission_blocked_by_config"}
    unfilled_count = len(
        [
            item
            for item in orders
            if str(item.get("submission_status") or "").strip().lower() in pending_statuses
            and _as_float(item.get("filled_quantity"), 0.0) < _as_float(item.get("quantity"), 0.0)
        ]
    )
    failed_count = len(
        [
            item
            for item in orders
            if str(item.get("submission_status") or "").strip().lower() in failed_statuses
            and str(item.get("risk_status") or "").strip().lower() != "rejected"
        ]
    )

    status = "matched"
    warnings: list[str] = []
    if failed_count > 0:
        status = "failed"
        warnings.append("failed_orders_detected")
    elif unfilled_count > 0:
        status = "pending"
        warnings.append("pending_or_unfilled_orders")
    elif mismatch_count > 0:
        status = "mismatch"
        warnings.append("position_mismatch_detected")
    elif abs(cash_diff) > tolerance or (buying_power_checked and abs(float(buying_power_diff or 0.0)) > tolerance):
        status = "mismatch"
        warnings.append("cash_or_buying_power_mismatch_detected")

    return {
        "position_rows": position_rows,
        "position_mismatch_count": mismatch_count,
        "cash_difference": cash_diff,
        "buying_power_difference": buying_power_diff,
        "buying_power_checked": buying_power_checked,
        "unfilled_order_count": unfilled_count,
        "failed_order_count": failed_count,
        "reconciliation_status": status,
        "mismatches": mismatches,
        "warnings": warnings,
    }
