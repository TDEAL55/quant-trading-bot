"""Export a read-only, historical paper performance snapshot for the live dashboard.

Run as quantbot with the paper environment sourced. This never submits orders.
The output is diagnostic and deliberately marked as not like-for-like with live.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from stock_pnl_reconstruction import is_bot_stock_order, reconstruct_stock_realized_pnl
from trade_review import build_trade_review, index_submission_records


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def build_paper_performance_artifact(
    orders: list[dict[str, Any]],
    submissions: Iterable[Mapping[str, Any]],
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Keep only confirmed bot exits with a matched entry strategy."""
    records = [dict(row) for row in submissions]
    indexed = index_submission_records(records)
    reconstruction = reconstruct_stock_realized_pnl(
        orders,
        bot_orders_only=True,
        history_limit=500,
        strategy_by_order_id=indexed,
    )
    if reconstruction.get("history_limit_reached"):
        raise ValueError("Paper order history reached the broker query limit")
    if reconstruction.get("unmatched_close_count") or reconstruction.get("unknown_strategy_match_count"):
        raise ValueError("Paper exits or strategy attribution are unresolved")

    review = build_trade_review(reconstruction, submission_records=records)
    trades = list(review.get("trades") or [])
    unattributed_bot_entries = sum(
        1 for order in orders
        if order.get("status") == "filled"
        and order.get("side") == "buy"
        and is_bot_stock_order(order)
        and not (indexed.get(str(order.get("order_id") or "")) or indexed.get(str(order.get("client_order_id") or "")))
    )
    entry_times = [str(row.get("entry_timestamp")) for row in trades if row.get("entry_timestamp")]
    exit_times = [str(row.get("exit_timestamp")) for row in trades if row.get("exit_timestamp")]
    as_of = (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    return _json_safe({
        "metrics": dict(review.get("metrics") or {}),
        "breakdowns": dict(review.get("breakdowns") or {}),
        "data_quality": dict(review.get("data_quality") or {}),
        "provenance": {
            "environment": "paper",
            "scope": "historical_attributed_bot_stock_round_trips",
            "source": "alpaca_paper_broker_filled_orders_and_paper_micro_state",
            "generated_at": as_of,
            "period_start": min(entry_times)[:10] if entry_times else None,
            "period_end": max(exit_times)[:10] if exit_times else None,
            "broker_order_count": len(orders),
            "manual_stock_fill_count": int(reconstruction.get("manual_stock_fill_count") or 0),
            "unattributed_bot_entry_count": unattributed_bot_entries,
            "comparable_to_live": False,
            "comparison_limit": "Historical paper settings and manual exits are not proven like-for-like with live.",
        },
    })


def main() -> None:
    from alpaca_micro_paper_broker import AlpacaMicroPaperBroker

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", default="/var/lib/quant-bot/paper-micro-state.json")
    parser.add_argument("--output", default="/var/lib/quant-bot/paper-performance.json")
    args = parser.parse_args()

    state = json.loads(Path(args.state).read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise ValueError("Paper state must be a JSON object")
    broker = AlpacaMicroPaperBroker(read_only=True)
    orders = broker.get_order_history(limit=500)
    artifact = build_paper_performance_artifact(orders, state.get("submissions") or [])

    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=destination.parent, delete=False) as handle:
            temp_path = Path(handle.name)
            json.dump(artifact, handle, sort_keys=True, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, destination)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
    print(json.dumps({
        "output": str(destination),
        "trade_count": artifact["metrics"].get("number_of_trades"),
        "total_pnl": artifact["metrics"].get("total_pnl"),
        "quality": artifact["data_quality"].get("reconstruction_confidence"),
        "comparable_to_live": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()

