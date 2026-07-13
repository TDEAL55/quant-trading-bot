from __future__ import annotations

from io import StringIO
from typing import Any

import csv

from dashboard_sanitization import sanitize_identifier, sanitize_text


def _write_csv(rows: list[dict[str, Any]], fieldnames: list[str]) -> str:
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: sanitize_text(row.get(key, "")) for key in fieldnames})
    return buffer.getvalue()


def export_sanitized_orders(rows: list[dict[str, Any]]) -> str:
    fieldnames = ["timestamp", "symbol", "signal", "submitted", "status", "notional", "block_reason", "order_id"]
    cleaned = []
    for row in rows:
        cleaned.append(
            {
                "timestamp": row.get("Timestamp", row.get("timestamp", "")),
                "symbol": row.get("Symbol", row.get("symbol", "")),
                "signal": row.get("Signal", row.get("signal", "")),
                "submitted": row.get("Submitted", row.get("submitted", "")),
                "status": row.get("Order Status", row.get("status", "")),
                "notional": row.get("Notional", row.get("notional", "")),
                "block_reason": row.get("Stop Reason", row.get("block_reason", "")),
                "order_id": sanitize_identifier(row.get("order_id_masked", row.get("order_id", ""))),
            }
        )
    return _write_csv(cleaned, fieldnames)


def export_daily_activity(rows: list[dict[str, Any]]) -> str:
    fieldnames = ["timestamp", "event", "status", "category", "detail"]
    cleaned = []
    for row in rows:
        cleaned.append(
            {
                "timestamp": row.get("timestamp", ""),
                "event": row.get("event", ""),
                "status": row.get("status", ""),
                "category": row.get("category", ""),
                "detail": row.get("detail", ""),
            }
        )
    return _write_csv(cleaned, fieldnames)


def export_signal_history(rows: list[dict[str, Any]]) -> str:
    fieldnames = ["timestamp", "signal", "price", "short_ma", "long_ma", "reason"]
    cleaned = []
    for row in rows:
        cleaned.append(
            {
                "timestamp": row.get("timestamp", row.get("snapshot_timestamp", "")),
                "signal": row.get("signal", row.get("generated_signal", "")),
                "price": row.get("price", row.get("latest_price", "")),
                "short_ma": row.get("short_ma", row.get("short_moving_average", "")),
                "long_ma": row.get("long_ma", row.get("long_moving_average", "")),
                "reason": row.get("reason", row.get("trade_or_skip_reason", "")),
            }
        )
    return _write_csv(cleaned, fieldnames)


def export_system_health(rows: list[dict[str, Any]]) -> str:
    fieldnames = ["component", "status", "timestamp", "reason"]
    return _write_csv(rows, fieldnames)


def export_performance_summary(rows: list[dict[str, Any]]) -> str:
    fieldnames = ["metric", "value"]
    return _write_csv(rows, fieldnames)
