from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from deployment_config import load_deployment_config
from paper_broker import create_paper_broker
from sprint_10_2_execution_validation import run_sprint_10_2_execution_validation


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_true(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def run_one_order_test(database_url: str | None = None) -> dict:
    cfg = load_deployment_config()
    if str(cfg.trading_mode).upper() != "PAPER":
        raise RuntimeError("TRADING_MODE must be PAPER")
    if str(cfg.paper_broker_backend).upper() != "ALPACA":
        raise RuntimeError("PAPER_BROKER_BACKEND must be ALPACA")
    if not _is_true(os.getenv("ALPACA_ORDER_SUBMISSION_ENABLED", "false")):
        raise RuntimeError("ALPACA_ORDER_SUBMISSION_ENABLED must be true for one-order paper trade test")

    symbols = [str(item).upper() for item in cfg.scan_symbols if str(item).strip()]
    if not symbols:
        symbols = ["JPM", "MSFT", "AAPL"]

    result = run_sprint_10_2_execution_validation(
        database_url=database_url or cfg.database_url,
        manual_approval="YES",
        symbols=symbols,
        persist=True,
    )

    paper_order = dict(result.get("paper_order") or {})
    order_id = str(paper_order.get("order_id") or "")
    client_order_id = str(paper_order.get("client_order_id") or "")
    symbol = str(paper_order.get("symbol") or "")

    broker = create_paper_broker(mode="PAPER", backend="ALPACA")
    order_details = {}
    if order_id:
        order_details = broker.get_order_by_id(order_id) or {}
    if not order_details and client_order_id:
        order_details = broker.get_order_by_client_order_id(client_order_id) or {}

    if not order_details:
        raise RuntimeError("Order verification failed: order not found by order_id/client_order_id")

    log_payload = {
        "logged_at": _utc_iso(),
        "run_status": str(result.get("status") or "unknown"),
        "execution_run_id": str(result.get("dashboard_payload", {}).get("latest_run", {}).get("run_id") or ""),
        "order_id": str(order_details.get("order_id") or order_id),
        "client_order_id": str(order_details.get("client_order_id") or client_order_id),
        "symbol": str(order_details.get("symbol") or symbol),
        "quantity": float(order_details.get("requested_quantity") or paper_order.get("shares") or 0.0),
        "filled_quantity": float(order_details.get("filled_quantity") or 0.0),
        "status": str(order_details.get("status") or paper_order.get("status") or "unknown"),
        "submitted_at": str(order_details.get("submitted_at") or ""),
        "updated_at": str(order_details.get("updated_at") or ""),
        "broker_backend": str(order_details.get("broker_backend") or "ALPACA"),
    }

    log_path = Path("first_alpaca_paper_trade_log.jsonl")
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(log_payload, sort_keys=True) + "\n")

    return {
        "status": "ok",
        "symbols": symbols,
        "result": result,
        "verified_order": order_details,
        "log_path": str(log_path),
    }


def main() -> int:
    try:
        payload = run_one_order_test()
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
