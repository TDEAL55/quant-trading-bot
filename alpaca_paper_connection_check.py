from __future__ import annotations

import json
import sys
from typing import Any

from alpaca_paper_broker import ALPACA_PAPER_ENDPOINT
from paper_broker import create_paper_broker


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def main() -> int:
    try:
        broker = create_paper_broker(mode="PAPER", backend="ALPACA")
        account = broker.get_account()
        positions = broker.get_positions()
        open_orders = broker.get_open_orders()

        payload = {
            "api_reachable": True,
            "paper_endpoint_confirmed": str(account.get("paper_endpoint_confirmed") or False).lower() == "true" or bool(account.get("paper_endpoint_confirmed")),
            "required_endpoint": ALPACA_PAPER_ENDPOINT,
            "account_status": str(account.get("status") or "unknown"),
            "buying_power": float(account.get("buying_power") or 0.0),
            "cash": float(account.get("cash") or 0.0),
            "equity": float(account.get("equity") or 0.0),
            "position_count": _safe_int(len(positions or {})),
            "open_order_count": _safe_int(len(open_orders or [])),
            "result": "PAPER_CONNECTION_OK",
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        print("PAPER_CONNECTION_OK")
        return 0
    except Exception as exc:
        error_payload = {
            "api_reachable": False,
            "required_endpoint": ALPACA_PAPER_ENDPOINT,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "result": "PAPER_CONNECTION_FAILED",
        }
        print(json.dumps(error_payload, indent=2, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
