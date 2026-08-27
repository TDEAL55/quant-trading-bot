from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from alpaca_paper_broker import AlpacaPaperBroker
from paper_execution_repository import MonitoringPaperExecutionRepository


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _parse_timestamp(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _strategy_for_order(order: dict[str, Any]) -> tuple[str, str]:
    client_order_id = str(order.get("client_order_id") or "").lower()
    asset_class = str(order.get("asset_class") or "").lower()
    if client_order_id.startswith("qtb-crypto-") or asset_class == "crypto":
        return "crypto_momentum", "v1"
    if client_order_id.startswith("qtb-option-") or asset_class in {"option", "us_option"}:
        return "options_directional", "v1"
    if client_order_id.startswith("qtb-exit-"):
        return "position_guard", "v1"
    return "equity_scanner", "v1"


def build_closed_trade_records(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build idempotent closed-trade records from chronological Alpaca bot fills."""
    inventory: dict[str, dict[str, Any]] = {}
    records: list[dict[str, Any]] = []
    seen_order_ids: set[str] = set()
    chronological = sorted(
        list(orders or []),
        key=lambda row: str((row or {}).get("updated_at") or (row or {}).get("submitted_at") or ""),
    )
    for raw_order in chronological:
        order = dict(raw_order or {})
        order_id = str(order.get("order_id") or "").strip()
        deduplication_id = order_id or str(order.get("client_order_id") or "").strip()
        if deduplication_id and deduplication_id in seen_order_ids:
            continue
        if deduplication_id:
            seen_order_ids.add(deduplication_id)
        client_order_id = str(order.get("client_order_id") or "").strip().lower()
        if not client_order_id.startswith("qtb-"):
            continue
        if str(order.get("status") or "").strip().lower() != "filled":
            continue
        symbol = str(order.get("symbol") or "").strip().upper()
        side = str(order.get("side") or "").strip().lower()
        quantity = abs(_as_float(order.get("filled_quantity"), 0.0))
        fill_price = _as_float(order.get("average_fill_price"), 0.0)
        if not symbol or side not in {"buy", "sell"} or quantity <= 0 or fill_price <= 0:
            continue

        fill_time = _parse_timestamp(order.get("updated_at") or order.get("submitted_at"))
        signed_fill = quantity if side == "buy" else -quantity
        lot = inventory.setdefault(
            symbol,
            {"quantity": 0.0, "average_price": 0.0, "entry_timestamp": fill_time},
        )
        existing_quantity = _as_float(lot.get("quantity"), 0.0)
        existing_price = _as_float(lot.get("average_price"), 0.0)
        if existing_quantity == 0 or existing_quantity * signed_fill > 0:
            combined_quantity = abs(existing_quantity) + quantity
            lot["average_price"] = (
                ((abs(existing_quantity) * existing_price) + (quantity * fill_price)) / combined_quantity
            )
            lot["quantity"] = existing_quantity + signed_fill
            if existing_quantity == 0:
                lot["entry_timestamp"] = fill_time
            continue

        closing_quantity = min(abs(existing_quantity), quantity)
        asset_class = str(order.get("asset_class") or "").strip().lower()
        multiplier = 100.0 if asset_class in {"option", "us_option"} else 1.0
        gross_pnl = (
            (fill_price - existing_price) * closing_quantity * multiplier
            if existing_quantity > 0
            else (existing_price - fill_price) * closing_quantity * multiplier
        )
        entry_time = lot.get("entry_timestamp")
        holding_hours = 0.0
        if isinstance(entry_time, datetime) and isinstance(fill_time, datetime):
            holding_hours = max((fill_time - entry_time).total_seconds() / 3600.0, 0.0)
        percentage_return = (
            gross_pnl / (existing_price * closing_quantity * multiplier)
            if existing_price > 0 and closing_quantity > 0
            else 0.0
        )
        strategy_id, strategy_version = _strategy_for_order(order)
        records.append(
            {
                "trade_id": f"alpaca-{deduplication_id or len(records)}-closed",
                "strategy_id": strategy_id,
                "strategy_version": strategy_version,
                "symbol": symbol,
                "entry_timestamp": entry_time.isoformat() if isinstance(entry_time, datetime) else "",
                "exit_timestamp": fill_time.isoformat() if isinstance(fill_time, datetime) else "",
                "entry_price": round(existing_price, 8),
                "exit_price": round(fill_price, 8),
                "quantity": round(closing_quantity, 10),
                "realized_gross_pnl": round(gross_pnl, 6),
                "estimated_fees": 0.0,
                "estimated_slippage": 0.0,
                "net_pnl": round(gross_pnl, 6),
                "percentage_return": round(percentage_return, 8),
                "holding_duration_hours": round(holding_hours, 6),
                "max_adverse_excursion": 0.0,
                "max_favorable_excursion": 0.0,
                "exit_reason": str(order.get("position_intent") or "broker_filled_exit"),
                "market_regime": "unknown",
                "close_type": f"broker_filled_{asset_class or 'equity'}_exit",
            }
        )

        remaining_quantity = existing_quantity + signed_fill
        if abs(remaining_quantity) <= 1e-10:
            lot.update({"quantity": 0.0, "average_price": 0.0, "entry_timestamp": None})
        elif existing_quantity * remaining_quantity > 0:
            lot["quantity"] = remaining_quantity
        else:
            lot.update({"quantity": remaining_quantity, "average_price": fill_price, "entry_timestamp": fill_time})
    return records


def summarize_closed_trade_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    rows = list(records or [])
    return {
        "closed_trade_count": len(rows),
        "realized_paper_pl": round(sum(_as_float(row.get("net_pnl"), 0.0) for row in rows), 6),
        "source": "alpaca_filled_bot_orders",
    }


def _same_closed_trade(candidate: dict[str, Any], existing: dict[str, Any]) -> bool:
    if str(candidate.get("trade_id") or "") == str(existing.get("trade_id") or ""):
        return True
    if str(candidate.get("symbol") or "").upper() != str(existing.get("symbol") or "").upper():
        return False
    candidate_time = _parse_timestamp(candidate.get("exit_timestamp"))
    existing_time = _parse_timestamp(existing.get("exit_timestamp"))
    if candidate_time is None or existing_time is None:
        return False
    if abs((candidate_time - existing_time).total_seconds()) > 300:
        return False
    candidate_quantity = abs(_as_float(candidate.get("quantity"), 0.0))
    existing_quantity = abs(_as_float(existing.get("quantity"), 0.0))
    quantity_tolerance = max(1e-6, max(candidate_quantity, existing_quantity) * 0.001)
    if abs(candidate_quantity - existing_quantity) > quantity_tolerance:
        return False
    candidate_exit = _as_float(candidate.get("exit_price"), 0.0)
    existing_exit = _as_float(existing.get("exit_price"), 0.0)
    price_tolerance = max(0.01, max(candidate_exit, existing_exit) * 0.001)
    return abs(candidate_exit - existing_exit) <= price_tolerance


def sync_closed_trade_ledger(
    *,
    database_url: str | None = None,
    broker_factory: Callable[..., AlpacaPaperBroker] = AlpacaPaperBroker,
    repository_factory: Callable[..., MonitoringPaperExecutionRepository] = MonitoringPaperExecutionRepository,
    limit: int = 500,
) -> dict[str, Any]:
    broker = broker_factory(mode="PAPER")
    orders = list(broker.get_order_history(limit=max(1, min(int(limit), 500))) or [])
    records = build_closed_trade_records(orders)
    repository = repository_factory(database_url=database_url)
    existing_records = list(
        getattr(repository, "list_closed_trades", lambda limit=5000: [])(limit=5000) or []
    )
    new_records = 0
    records_synced = 0
    for record in records:
        if any(_same_closed_trade(record, existing) for existing in existing_records):
            continue
        new_records += 1
        records_synced += 1
        repository.save_closed_trade(record)
        existing_records.append(dict(record))
    database = getattr(repository, "db", None)
    if database is not None:
        database.close()
    return {
        **summarize_closed_trade_records(records),
        "broker_order_count": len(orders),
        "records_synced": records_synced,
        "new_records": new_records,
    }
