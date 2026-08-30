from __future__ import annotations

import math
from typing import Any, Iterable


TERMINAL_ORDER_STATUSES = {
    "filled",
    "canceled",
    "cancelled",
    "rejected",
    "expired",
    "done_for_day",
    "failed",
}


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _strict_float(value: Any) -> tuple[float, bool]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0, False
    return (parsed, True) if math.isfinite(parsed) else (0.0, False)


def _position_quantity(positions: Any, symbol: str) -> tuple[float, bool]:
    if isinstance(positions, dict):
        payload = next(
            (
                value
                for key, value in positions.items()
                if str(key or "").strip().upper() == symbol
            ),
            None,
        )
        if payload is None:
            return 0.0, True
        if not isinstance(payload, dict) or "quantity" not in payload:
            return 0.0, False
        return _strict_float(payload.get("quantity"))
    if isinstance(positions, list):
        for row in positions:
            if not isinstance(row, dict):
                return 0.0, False
            if str(row.get("symbol") or "").strip().upper() == symbol:
                if "quantity" not in row:
                    return 0.0, False
                return _strict_float(row.get("quantity"))
        return 0.0, True
    return 0.0, False


def _remaining_quantity(row: dict[str, Any], reference_price: float) -> tuple[float, bool]:
    requested = row.get("requested_quantity")
    if requested in {None, ""}:
        requested = row.get("quantity")
    if requested in {None, ""}:
        requested = row.get("qty")
    if requested not in {None, ""}:
        requested_quantity, requested_available = _strict_float(requested)
        filled_quantity, filled_available = _strict_float(
            row.get("filled_quantity") or row.get("filled_qty") or 0.0
        )
        if (
            not requested_available
            or not filled_available
            or requested_quantity <= 0.0
            or filled_quantity < 0.0
            or filled_quantity > requested_quantity + 1e-8
        ):
            return 0.0, False
        return max(requested_quantity - filled_quantity, 0.0), True

    notional = _as_float(row.get("notional"), 0.0)
    row_price = _as_float(
        row.get("reference_price")
        or row.get("limit_price")
        or row.get("stop_price")
        or reference_price,
        0.0,
    )
    if notional > 0.0 and row_price > 0.0:
        return notional / row_price, True
    return 0.0, False


def _entry_quantity_from_rows(
    rows: Iterable[dict[str, Any]],
    *,
    symbol: str,
    side: str,
    reference_price: float,
    active_only: bool,
    exclude_reservation_id: str | None = None,
) -> tuple[float, bool, int]:
    total = 0.0
    count = 0
    for raw in rows:
        if not isinstance(raw, dict):
            return 0.0, False, count
        row = dict(raw)
        if str(row.get("symbol") or "").strip().upper() != symbol:
            continue
        if str(row.get("side") or "").strip().upper() != side:
            continue
        if exclude_reservation_id and str(row.get("reservation_id") or "") == str(exclude_reservation_id):
            continue
        status = str(row.get("status") or "").strip().lower()
        if active_only and status in TERMINAL_ORDER_STATUSES | {"released", "finalized"}:
            continue
        quantity, available = _remaining_quantity(row, reference_price)
        if not available:
            return 0.0, False, count
        if quantity <= 0.0:
            continue
        total += quantity
        count += 1
    return total, True, count


def _same_symbol_order_count(
    rows: Iterable[dict[str, Any]],
    *,
    symbol: str,
    active_only: bool,
    exclude_reservation_id: str | None = None,
) -> tuple[int, bool]:
    count = 0
    for raw in rows:
        if not isinstance(raw, dict):
            return count, False
        row = dict(raw)
        if str(row.get("symbol") or "").strip().upper() != symbol:
            continue
        if exclude_reservation_id and str(row.get("reservation_id") or "") == str(exclude_reservation_id):
            continue
        status = str(row.get("status") or "").strip().lower()
        if active_only and status in TERMINAL_ORDER_STATUSES | {"released", "finalized"}:
            continue
        count += 1
    return count, True


def evaluate_entry_exposure(
    *,
    symbol: str,
    side: str,
    planned_quantity: float,
    reference_price: float,
    portfolio_equity: float,
    allowed_position_percent: float,
    positions: Any,
    open_orders: list[dict[str, Any]] | None,
    reservations: list[dict[str, Any]] | None,
    same_cycle_orders: list[dict[str, Any]] | None,
    exclude_reservation_id: str | None = None,
) -> dict[str, Any]:
    """Fail-closed stock entry concentration and duplicate-order review.

    Opposite-side orders are deliberately not credited as exposure reductions:
    they may never fill. BUY orders cover an existing short before creating long
    exposure; SELL orders close an existing long before creating short exposure.
    """
    normalized_symbol = str(symbol or "").strip().upper()
    normalized_side = str(side or "").strip().upper()
    quantity = _as_float(planned_quantity, 0.0)
    price = _as_float(reference_price, 0.0)
    equity = _as_float(portfolio_equity, 0.0)
    allowed_percent = _as_float(allowed_position_percent, 0.0)

    inputs_valid = bool(
        normalized_symbol
        and normalized_side in {"BUY", "SELL"}
        and quantity > 0.0
        and price > 0.0
        and equity > 0.0
        and 0.0 < allowed_percent <= 25.0
        and open_orders is not None
        and reservations is not None
        and same_cycle_orders is not None
    )
    current_quantity, positions_available = _position_quantity(positions, normalized_symbol)
    if not inputs_valid or not positions_available:
        return {
            "approved": False,
            "reason": "exposure_data_unavailable",
            "symbol": normalized_symbol,
            "side": normalized_side,
            "allowed_position_percent": allowed_percent,
            "checks": {"exposure_data_available": False, "duplicate_entry": False, "concentration": False},
        }

    pending_quantity, pending_available, pending_count = _entry_quantity_from_rows(
        open_orders,
        symbol=normalized_symbol,
        side=normalized_side,
        reference_price=price,
        active_only=True,
    )
    reserved_quantity, reservations_available, reservation_count = _entry_quantity_from_rows(
        reservations,
        symbol=normalized_symbol,
        side=normalized_side,
        reference_price=price,
        active_only=True,
        exclude_reservation_id=exclude_reservation_id,
    )
    same_cycle_quantity, planned_available, same_cycle_count = _entry_quantity_from_rows(
        same_cycle_orders,
        symbol=normalized_symbol,
        side=normalized_side,
        reference_price=price,
        active_only=False,
    )
    pending_symbol_count, pending_symbols_available = _same_symbol_order_count(
        open_orders,
        symbol=normalized_symbol,
        active_only=True,
    )
    reservation_symbol_count, reservation_symbols_available = _same_symbol_order_count(
        reservations,
        symbol=normalized_symbol,
        active_only=True,
        exclude_reservation_id=exclude_reservation_id,
    )
    same_cycle_symbol_count, planned_symbols_available = _same_symbol_order_count(
        same_cycle_orders,
        symbol=normalized_symbol,
        active_only=False,
    )
    exposure_data_available = bool(
        pending_available
        and reservations_available
        and planned_available
        and pending_symbols_available
        and reservation_symbols_available
        and planned_symbols_available
    )
    if not exposure_data_available:
        return {
            "approved": False,
            "reason": "exposure_data_unavailable",
            "symbol": normalized_symbol,
            "side": normalized_side,
            "allowed_position_percent": allowed_percent,
            "checks": {"exposure_data_available": False, "duplicate_entry": False, "concentration": False},
        }

    # Serialize the whole symbol, not only a side. A pending opposite-side
    # close/reversal must not race a fresh entry for the same stock.
    duplicate_count = pending_symbol_count + reservation_symbol_count + same_cycle_symbol_count
    incoming_quantity = pending_quantity + reserved_quantity + same_cycle_quantity + quantity
    if normalized_side == "BUY":
        projected_signed_quantity = current_quantity + incoming_quantity
        projected_exposure_quantity = max(projected_signed_quantity, 0.0)
    else:
        projected_signed_quantity = current_quantity - incoming_quantity
        projected_exposure_quantity = abs(min(projected_signed_quantity, 0.0))

    current_notional = abs(current_quantity) * price
    pending_notional = pending_quantity * price
    reserved_notional = reserved_quantity * price
    same_cycle_notional = same_cycle_quantity * price
    planned_notional = quantity * price
    projected_notional = projected_exposure_quantity * price
    maximum_notional = equity * (allowed_percent / 100.0)
    concentration_ok = projected_notional <= maximum_notional + 1e-6
    duplicate_entry = duplicate_count > 0
    approved = bool(not duplicate_entry and concentration_ok)
    reason = (
        "approved"
        if approved
        else "duplicate_entry_pending"
        if duplicate_entry
        else "symbol_concentration_limit"
    )
    return {
        "approved": approved,
        "reason": reason,
        "symbol": normalized_symbol,
        "side": normalized_side,
        "allowed_position_percent": round(allowed_percent, 6),
        "maximum_notional": round(maximum_notional, 6),
        "current_quantity": round(current_quantity, 8),
        "current_notional": round(current_notional, 6),
        "pending_entry_quantity": round(pending_quantity, 8),
        "pending_entry_notional": round(pending_notional, 6),
        "reserved_entry_quantity": round(reserved_quantity, 8),
        "reserved_entry_notional": round(reserved_notional, 6),
        "same_cycle_entry_quantity": round(same_cycle_quantity, 8),
        "same_cycle_entry_notional": round(same_cycle_notional, 6),
        "planned_quantity": round(quantity, 8),
        "planned_notional": round(planned_notional, 6),
        "projected_quantity": round(projected_signed_quantity, 8),
        "projected_notional": round(projected_notional, 6),
        "remaining_capacity_notional": round(max(maximum_notional - projected_notional, 0.0), 6),
        "duplicate_entry_count": duplicate_count,
        "checks": {
            "exposure_data_available": True,
            "duplicate_entry": not duplicate_entry,
            "concentration": concentration_ok,
        },
    }
