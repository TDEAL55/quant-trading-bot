from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any


FINAL_ORDER_STATUSES = {"filled", "canceled", "cancelled", "rejected", "expired", "done_for_day"}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _normalize_status(value: Any) -> str:
    return str(value or "unknown").strip().lower()


def _event_row(order: dict[str, Any], status: str, previous_status: str | None) -> dict[str, Any]:
    now_iso = _utc_iso()
    return {
        "event_time": now_iso,
        "status": status,
        "previous_status": previous_status or "",
        "broker_order_id": str(order.get("order_id") or ""),
        "client_order_id": str(order.get("client_order_id") or ""),
        "requested_quantity": _as_float(order.get("requested_quantity"), 0.0),
        "filled_quantity": _as_float(order.get("filled_quantity"), 0.0),
        "average_fill_price": _as_float(order.get("average_fill_price"), 0.0),
        "rejection_reason": str(order.get("rejection_reason") or ""),
        "broker_updated_at": str(order.get("updated_at") or now_iso),
    }


def track_order_lifecycle(
    broker: Any,
    initial_order: dict[str, Any],
    poll_seconds: float = 1.0,
    max_wait_seconds: float = 45.0,
) -> dict[str, Any]:
    """Track status transitions from initial submission through a final broker state."""
    order = dict(initial_order or {})
    order_id = str(order.get("order_id") or "").strip()
    client_order_id = str(order.get("client_order_id") or "").strip()

    transitions: list[dict[str, Any]] = []
    seen_statuses: list[str] = []
    start = time.monotonic()
    first_event_at = _utc_iso()

    def _append_transition(row: dict[str, Any]) -> None:
        status = _normalize_status(row.get("status"))
        previous = seen_statuses[-1] if seen_statuses else None
        if seen_statuses and status == seen_statuses[-1]:
            return
        seen_statuses.append(status)
        transitions.append(_event_row(row, status=status, previous_status=previous))

    _append_transition(order)

    while True:
        current_status = seen_statuses[-1] if seen_statuses else "unknown"
        if current_status in FINAL_ORDER_STATUSES:
            break
        if (time.monotonic() - start) >= max(float(max_wait_seconds), 0.1):
            break

        latest = None
        try:
            if order_id and hasattr(broker, "get_order_by_id"):
                latest = broker.get_order_by_id(order_id)
            if not latest and client_order_id and hasattr(broker, "get_order_by_client_order_id"):
                latest = broker.get_order_by_client_order_id(client_order_id)
        except Exception:
            latest = None

        if isinstance(latest, dict) and latest:
            order = dict(latest)
            _append_transition(order)

        time.sleep(max(float(poll_seconds), 0.1))

    final_status = seen_statuses[-1] if seen_statuses else _normalize_status(order.get("status"))
    final_filled_qty = _as_float(order.get("filled_quantity"), 0.0)
    is_filled = final_status == "filled" and final_filled_qty > 0
    fill_time = ""
    for row in transitions:
        if str(row.get("status") or "") == "filled":
            fill_time = str(row.get("event_time") or "")
            break

    return {
        "order": order,
        "status_transitions": transitions,
        "final_status": final_status,
        "submission_time": first_event_at,
        "fill_time": fill_time,
        "execution_latency_seconds": round(max(time.monotonic() - start, 0.0), 6),
        "is_filled": bool(is_filled),
    }
