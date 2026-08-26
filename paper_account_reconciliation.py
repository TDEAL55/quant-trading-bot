from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from alpaca_paper_broker import AlpacaPaperBroker
from paper_trade_ledger import sync_closed_trade_ledger


_TERMINAL_STATUSES = {"filled", "canceled", "cancelled", "rejected", "expired"}


def _timestamp(value: Any) -> datetime | None:
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


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def run_paper_account_reconciliation(
    *,
    database_url: str | None = None,
    broker_factory: Callable[..., AlpacaPaperBroker] = AlpacaPaperBroker,
    ledger_sync: Callable[..., dict[str, Any]] = sync_closed_trade_ledger,
    now: datetime | None = None,
    status_path: str | Path | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    if str(os.getenv("TRADING_MODE", "PAPER")).strip().upper() != "PAPER":
        raise RuntimeError("account reconciliation requires TRADING_MODE=PAPER")

    broker = broker_factory(mode="PAPER")
    account = dict(broker.get_account() or {})
    positions = dict(broker.get_positions() or {})
    open_orders = list(broker.get_open_orders() or [])
    ledger = dict(
        ledger_sync(
            database_url=database_url,
            broker_factory=broker_factory,
        )
        or {}
    )
    warnings: list[str] = []
    account_status = str(account.get("status") or "").strip().upper()
    if account_status not in {"ACTIVE", "ACCOUNT_UPDATED", ""}:
        warnings.append(f"paper_account_status_{account_status.lower() or 'unknown'}")

    active_bot_orders: list[dict[str, Any]] = []
    order_keys: set[tuple[str, str]] = set()
    for raw in open_orders:
        order = dict(raw or {})
        client_order_id = str(order.get("client_order_id") or "")
        status = str(order.get("status") or "").lower()
        if not client_order_id.startswith("qtb-") or status in _TERMINAL_STATUSES:
            continue
        active_bot_orders.append(order)
        key = (str(order.get("symbol") or "").upper(), str(order.get("side") or "").lower())
        if key in order_keys:
            warnings.append(f"duplicate_open_order:{key[0]}:{key[1]}")
        order_keys.add(key)
        submitted = _timestamp(order.get("updated_at") or order.get("submitted_at"))
        if submitted is not None and (current - submitted).total_seconds() > 86400:
            warnings.append(f"stale_open_order:{key[0]}:{key[1]}")

    for symbol, raw in positions.items():
        position = dict(raw or {})
        quantity = float(position.get("quantity") or 0.0)
        if not math.isfinite(quantity):
            warnings.append(f"invalid_position_quantity:{str(symbol).upper()}")
            continue
        asset_class = str(position.get("asset_class") or "").lower()
        if quantity < 0 and asset_class in {"crypto", "option", "us_option"}:
            warnings.append(f"unexpected_short_{asset_class}:{str(symbol).upper()}")

    result = {
        "updated_at": current.isoformat(),
        "status": "matched" if not warnings else "mismatch",
        "account_status": account_status or "UNKNOWN",
        "position_count": len(positions),
        "open_order_count": len(open_orders),
        "active_bot_order_count": len(active_bot_orders),
        "closed_trade_count": int(ledger.get("closed_trade_count") or 0),
        "new_closed_trades_recorded": int(ledger.get("new_records") or 0),
        "warnings": sorted(set(warnings)),
        "paper_only": True,
    }
    resolved_path = Path(
        status_path
        or os.getenv("PAPER_RECONCILIATION_STATUS_PATH", "/var/lib/quant-bot/reconciliation-status.json")
    )
    _write_json_atomic(resolved_path, result)
    return result
