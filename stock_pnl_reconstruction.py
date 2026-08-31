from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from stock_execution_cost_model import estimate_stock_round_trip_costs, settings_from_environment as cost_settings_from_environment
from strategy_execution_policy import evaluate_strategy_execution_policy, settings_from_environment as strategy_policy_from_environment
from strategy_profitability import build_strategy_leaderboard


_STOCK_ASSET_CLASSES = {"equity", "stock", "us_equity"}
_CRYPTO_ASSET_CLASSES = {"crypto"}
_OPTION_ASSET_CLASSES = {"option", "us_option"}
_OCC_OPTION_SYMBOL = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")
_CLOSE_INTENTS = {"buy_to_close", "sell_to_close", "close", "close_position"}
_QUANTITY_EPSILON = Decimal("0.0000000001")


def _decimal(value: Any) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")
    return parsed if parsed.is_finite() else Decimal("0")


def _float(value: Decimal, places: int = 6) -> float:
    quantum = Decimal("1").scaleb(-places)
    return float(value.quantize(quantum))


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


def _fill_timestamp(order: dict[str, Any]) -> datetime | None:
    for key in ("filled_at", "updated_at", "submitted_at", "created_at"):
        parsed = _parse_timestamp(order.get(key))
        if parsed is not None:
            return parsed
    return None


def _order_key(order: dict[str, Any], fallback_index: int) -> str:
    return str(order.get("order_id") or order.get("client_order_id") or f"history-row-{fallback_index}").strip()


def _is_bot_order(order: dict[str, Any]) -> bool:
    return str(order.get("client_order_id") or "").strip().lower().startswith("qtb-")


def _asset_group(order: dict[str, Any]) -> tuple[str, bool]:
    """Return ``(group, classification_is_explicit)`` for a broker order."""
    asset_class = str(order.get("asset_class") or "").strip().lower()
    client_order_id = str(order.get("client_order_id") or "").strip().lower()
    symbol = str(order.get("symbol") or "").strip().upper()

    if asset_class in _CRYPTO_ASSET_CLASSES or client_order_id.startswith("qtb-crypto-") or "/" in symbol:
        return "crypto", True
    if (
        asset_class in _OPTION_ASSET_CLASSES
        or client_order_id.startswith("qtb-option-")
        or bool(_OCC_OPTION_SYMBOL.fullmatch(symbol))
    ):
        return "options", True
    if asset_class in _STOCK_ASSET_CLASSES:
        return "stocks", True
    if not asset_class and symbol:
        # Older normalized Alpaca rows did not always carry asset_class. These
        # rows may still be useful, but the result must not be labeled exact.
        return "stocks", False
    return "unknown", False


def is_bot_stock_order(order: dict[str, Any]) -> bool:
    """Return whether a broker row belongs to the bot's stock-only scope."""
    group, _ = _asset_group(dict(order or {}))
    return _is_bot_order(dict(order or {})) and group == "stocks"


def _is_explicit_close(order: dict[str, Any]) -> bool:
    intent = str(order.get("position_intent") or "").strip().lower()
    client_order_id = str(order.get("client_order_id") or "").strip().lower()
    return intent in _CLOSE_INTENTS or client_order_id.startswith("qtb-exit-")


def _entry_strategy_id(
    order: dict[str, Any],
    strategy_by_order_id: dict[str, Any],
) -> str:
    order_id = str(order.get("order_id") or "").strip()
    client_order_id = str(order.get("client_order_id") or "").strip()
    metadata = order.get("metadata") if isinstance(order.get("metadata"), dict) else {}
    selected_strategy = order.get("selected_strategy") if isinstance(order.get("selected_strategy"), dict) else {}
    mapped = strategy_by_order_id.get(order_id) or strategy_by_order_id.get(client_order_id) or {}
    if not isinstance(mapped, dict):
        mapped = {"strategy_id": mapped}
    for value in (
        order.get("entry_strategy_id"),
        order.get("strategy_id"),
        metadata.get("entry_strategy_id"),
        metadata.get("strategy_id"),
        selected_strategy.get("strategy_id"),
        mapped.get("entry_strategy_id"),
        mapped.get("strategy_id"),
    ):
        normalized = str(value or "").strip()
        if normalized:
            return normalized
    return "unknown"


def _select_latest_order_snapshots(orders: list[tuple[int, dict[str, Any]]]) -> tuple[list[tuple[int, dict[str, Any]]], int]:
    """Collapse repeated snapshots of one broker order without double-counting fills."""
    selected: dict[str, tuple[int, dict[str, Any]]] = {}
    anonymous: list[tuple[int, dict[str, Any]]] = []
    duplicate_count = 0
    for index, order in orders:
        stable_id = str(order.get("order_id") or order.get("client_order_id") or "").strip()
        if not stable_id:
            anonymous.append((index, order))
            continue
        previous = selected.get(stable_id)
        if previous is None:
            selected[stable_id] = (index, order)
            continue
        duplicate_count += 1
        previous_index, previous_order = previous
        previous_quantity = abs(_decimal(previous_order.get("filled_quantity")))
        current_quantity = abs(_decimal(order.get("filled_quantity")))
        previous_time = _fill_timestamp(previous_order) or datetime.min.replace(tzinfo=timezone.utc)
        current_time = _fill_timestamp(order) or datetime.min.replace(tzinfo=timezone.utc)
        if (current_quantity, current_time, index) >= (previous_quantity, previous_time, previous_index):
            selected[stable_id] = (index, order)
    return [*selected.values(), *anonymous], duplicate_count


def reconstruct_stock_realized_pnl(
    orders: list[dict[str, Any]],
    *,
    bot_orders_only: bool = True,
    history_limit: int | None = None,
    history_limit_reached: bool | None = None,
    strategy_by_order_id: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reconstruct realized stock P/L from broker fill history without writing state.

    Filled stock orders are matched FIFO. A sell first closes known long lots and
    may then open a short; a buy first covers known short lots and may then open
    a long. Explicit close orders with no known entry are reported as unresolved
    instead of being misclassified as a new position.

    The result is deliberately diagnostic. ``is_exact`` is false whenever the
    available history cannot prove the full cost basis, order sequence, or stock
    classification. No database or broker mutation occurs.
    """
    raw_orders = [dict(row or {}) for row in list(orders or [])]
    strategy_metadata = dict(strategy_by_order_id or {})
    if history_limit_reached is None:
        history_limit_reached = bool(history_limit and len(raw_orders) >= int(history_limit))

    ignored = {
        "non_bot": 0,
        "crypto": 0,
        "options": 0,
        "unknown_asset": 0,
        "unfilled": 0,
    }
    inferred_stock_classification = 0
    manual_stock_fill_count = 0
    manual_stock_fills_by_symbol: dict[str, int] = defaultdict(int)
    bot_stock_symbols: set[str] = set()
    stock_candidates: list[tuple[int, dict[str, Any]]] = []
    for index, order in enumerate(raw_orders):
        quantity = abs(_decimal(order.get("filled_quantity")))
        group, explicit_group = _asset_group(order)
        if bot_orders_only and not _is_bot_order(order):
            if quantity > 0:
                ignored["non_bot"] += 1
                if group == "stocks":
                    manual_stock_fill_count += 1
                    manual_symbol = str(order.get("symbol") or "").strip().upper()
                    if manual_symbol:
                        manual_stock_fills_by_symbol[manual_symbol] += 1
            continue
        if group != "stocks":
            if quantity > 0:
                ignored[group if group in ignored else "unknown_asset"] += 1
            continue
        if not explicit_group:
            inferred_stock_classification += 1
        if quantity <= 0:
            ignored["unfilled"] += 1
            continue
        bot_symbol = str(order.get("symbol") or "").strip().upper()
        if bot_symbol:
            bot_stock_symbols.add(bot_symbol)
        stock_candidates.append((index, order))

    interfering_manual_stock_symbols = sorted(bot_stock_symbols.intersection(manual_stock_fills_by_symbol))
    interfering_manual_stock_fill_count = sum(
        manual_stock_fills_by_symbol[symbol] for symbol in interfering_manual_stock_symbols
    )
    unrelated_manual_stock_fill_count = manual_stock_fill_count - interfering_manual_stock_fill_count

    stock_candidates, duplicate_snapshot_count = _select_latest_order_snapshots(stock_candidates)
    valid_fills: list[dict[str, Any]] = []
    invalid_fill_count = 0
    missing_timestamp_count = 0
    for original_index, order in stock_candidates:
        symbol = str(order.get("symbol") or "").strip().upper()
        side = str(order.get("side") or "").strip().lower()
        quantity = abs(_decimal(order.get("filled_quantity")))
        price = _decimal(order.get("average_fill_price"))
        timestamp = _fill_timestamp(order)
        if not symbol or side not in {"buy", "sell"} or quantity <= 0 or price <= 0:
            invalid_fill_count += 1
            continue
        if timestamp is None:
            missing_timestamp_count += 1
            continue
        valid_fills.append(
            {
                "order": order,
                "original_index": original_index,
                "order_key": _order_key(order, original_index),
                "symbol": symbol,
                "side": side,
                "direction": Decimal("1") if side == "buy" else Decimal("-1"),
                "quantity": quantity,
                "price": price,
                "timestamp": timestamp,
            }
        )

    valid_fills.sort(key=lambda row: (row["timestamp"], row["original_index"], row["order_key"]))
    ambiguous_timestamp_groups = 0
    timestamp_sides: dict[tuple[str, datetime], set[str]] = defaultdict(set)
    for fill in valid_fills:
        timestamp_sides[(fill["symbol"], fill["timestamp"])].add(fill["side"])
    ambiguous_timestamp_groups = sum(1 for sides in timestamp_sides.values() if len(sides) > 1)

    inventory: dict[str, list[dict[str, Any]]] = defaultdict(list)
    event_by_order: dict[str, dict[str, Any]] = {}
    unmatched_close_events: list[dict[str, Any]] = []
    per_symbol: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "realized_pnl": Decimal("0"),
            "long_realized_pnl": Decimal("0"),
            "short_realized_pnl": Decimal("0"),
            "matched_quantity": Decimal("0"),
            "closing_order_ids": set(),
            "matched_lot_count": 0,
        }
    )
    per_strategy: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "realized_pnl": Decimal("0"),
            "matched_quantity": Decimal("0"),
            "closing_order_ids": set(),
            "matched_lot_count": 0,
        }
    )
    matched_lot_count = 0
    total_matched_quantity = Decimal("0")
    total_unmatched_close_quantity = Decimal("0")

    for fill in valid_fills:
        order = fill["order"]
        symbol = fill["symbol"]
        direction = fill["direction"]
        remaining = fill["quantity"]
        lots = inventory[symbol]
        event: dict[str, Any] | None = None

        while remaining > _QUANTITY_EPSILON and lots and lots[0]["direction"] != direction:
            lot = lots[0]
            matched_quantity = min(remaining, lot["quantity"])
            entry_strategy_id = str(lot.get("strategy_id") or "unknown")
            if lot["direction"] > 0:
                pnl = (fill["price"] - lot["price"]) * matched_quantity
                closed_direction = "long"
            else:
                pnl = (lot["price"] - fill["price"]) * matched_quantity
                closed_direction = "short"

            if event is None:
                event = {
                    "exit_order_id": fill["order_key"],
                    "client_order_id": str(order.get("client_order_id") or ""),
                    "symbol": symbol,
                    "side": fill["side"],
                    "exit_timestamp": fill["timestamp"].isoformat(),
                    "average_exit_price": _float(fill["price"], 8),
                    "quantity_closed": Decimal("0"),
                    "matched_entry_notional": Decimal("0"),
                    "realized_pnl": Decimal("0"),
                    "unmatched_quantity": Decimal("0"),
                    "matched_lots": [],
                    "strategy_breakdown": {},
                }
                event_by_order[fill["order_key"]] = event

            entry_notional = lot["price"] * matched_quantity
            event["quantity_closed"] += matched_quantity
            event["matched_entry_notional"] += entry_notional
            event["realized_pnl"] += pnl
            event["matched_lots"].append(
                {
                    "entry_order_id": lot["order_key"],
                    "entry_timestamp": lot["timestamp"].isoformat(),
                    "direction": closed_direction,
                    "quantity": _float(matched_quantity, 10),
                    "entry_price": _float(lot["price"], 8),
                    "exit_price": _float(fill["price"], 8),
                    "realized_pnl": _float(pnl),
                    "entry_strategy_id": entry_strategy_id,
                }
            )
            event_strategy = event["strategy_breakdown"].setdefault(
                entry_strategy_id,
                {
                    "realized_pnl": Decimal("0"),
                    "quantity": Decimal("0"),
                    "entry_notional": Decimal("0"),
                    "matched_lot_count": 0,
                },
            )
            event_strategy["realized_pnl"] += pnl
            event_strategy["quantity"] += matched_quantity
            event_strategy["entry_notional"] += entry_notional
            event_strategy["matched_lot_count"] += 1

            symbol_summary = per_symbol[symbol]
            symbol_summary["realized_pnl"] += pnl
            symbol_summary[f"{closed_direction}_realized_pnl"] += pnl
            symbol_summary["matched_quantity"] += matched_quantity
            symbol_summary["closing_order_ids"].add(fill["order_key"])
            symbol_summary["matched_lot_count"] += 1
            strategy_summary = per_strategy[entry_strategy_id]
            strategy_summary["realized_pnl"] += pnl
            strategy_summary["matched_quantity"] += matched_quantity
            strategy_summary["closing_order_ids"].add(fill["order_key"])
            strategy_summary["matched_lot_count"] += 1
            matched_lot_count += 1
            total_matched_quantity += matched_quantity

            remaining -= matched_quantity
            lot["quantity"] -= matched_quantity
            if lot["quantity"] <= _QUANTITY_EPSILON:
                lots.pop(0)

        if remaining <= _QUANTITY_EPSILON:
            continue
        if _is_explicit_close(order):
            total_unmatched_close_quantity += remaining
            unmatched = {
                "order_id": fill["order_key"],
                "client_order_id": str(order.get("client_order_id") or ""),
                "symbol": symbol,
                "side": fill["side"],
                "timestamp": fill["timestamp"].isoformat(),
                "unmatched_quantity": _float(remaining, 10),
                "reason": "explicit_close_has_no_known_entry_fill",
            }
            unmatched_close_events.append(unmatched)
            if event is not None:
                event["unmatched_quantity"] += remaining
            continue

        lots.append(
            {
                "direction": direction,
                "quantity": remaining,
                "price": fill["price"],
                "timestamp": fill["timestamp"],
                "order_key": fill["order_key"],
                "strategy_id": _entry_strategy_id(order, strategy_metadata),
            }
        )

    realized_events: list[dict[str, Any]] = []
    execution_cost_settings = cost_settings_from_environment()
    for event in event_by_order.values():
        entry_notional = event.pop("matched_entry_notional")
        pnl = event["realized_pnl"]
        event["quantity_closed"] = _float(event["quantity_closed"], 10)
        event["realized_pnl"] = _float(pnl)
        event["percentage_return"] = _float(pnl / entry_notional, 8) if entry_notional > 0 else 0.0
        event["unmatched_quantity"] = _float(event["unmatched_quantity"], 10)
        event["is_exact"] = event["unmatched_quantity"] <= 0
        exit_timestamp = _parse_timestamp(event.get("exit_timestamp"))
        weighted_holding_hours = Decimal("0")
        holding_quantity = Decimal("0")
        matched_directions = {str(item.get("direction") or "long") for item in event.get("matched_lots") or []}
        for matched_lot in event.get("matched_lots") or []:
            lot_quantity = _decimal(matched_lot.get("quantity"))
            entry_timestamp = _parse_timestamp(matched_lot.get("entry_timestamp"))
            if exit_timestamp is not None and entry_timestamp is not None and lot_quantity > 0:
                hours = Decimal(str(max((exit_timestamp - entry_timestamp).total_seconds() / 3600.0, 0.0)))
                weighted_holding_hours += hours * lot_quantity
                holding_quantity += lot_quantity
        holding_hours = weighted_holding_hours / holding_quantity if holding_quantity > 0 else Decimal("0")
        direction = "short" if matched_directions == {"short"} else "long"
        costs = estimate_stock_round_trip_costs(
            entry_notional=float(entry_notional),
            exit_notional=float(_decimal(event.get("average_exit_price")) * _decimal(event.get("quantity_closed"))),
            direction=direction,
            holding_hours=float(holding_hours),
            settings=execution_cost_settings,
        )
        event["entry_notional"] = _float(entry_notional)
        event["holding_duration_hours"] = _float(holding_hours)
        event["estimated_slippage"] = float(costs["estimated_slippage"])
        event["estimated_regulatory_fees"] = float(costs["estimated_regulatory_fees"])
        event["estimated_borrow_cost"] = float(costs["estimated_borrow_cost"])
        event["estimated_execution_costs"] = float(costs["estimated_total_cost"])
        event["estimated_live_net_pnl"] = round(float(pnl) - float(costs["estimated_total_cost"]), 6)
        event["estimated_live_percentage_return"] = (
            round(event["estimated_live_net_pnl"] / float(entry_notional), 8)
            if entry_notional > 0
            else 0.0
        )
        strategy_breakdown = event.pop("strategy_breakdown")
        event["strategy_breakdown"] = []
        for strategy_id, values in sorted(strategy_breakdown.items()):
            strategy_entry_notional = values["entry_notional"]
            cost_share = (
                Decimal(str(event["estimated_execution_costs"])) * strategy_entry_notional / entry_notional
                if entry_notional > 0
                else Decimal("0")
            )
            strategy_realized_pnl = values["realized_pnl"]
            event["strategy_breakdown"].append({
                "strategy_id": strategy_id,
                "realized_pnl": _float(strategy_realized_pnl),
                "quantity": _float(values["quantity"], 10),
                "entry_notional": _float(strategy_entry_notional),
                "estimated_execution_costs": _float(cost_share),
                "estimated_live_net_pnl": _float(strategy_realized_pnl - cost_share),
                "matched_lot_count": int(values["matched_lot_count"]),
            })
        event["entry_strategy_id"] = (
            event["strategy_breakdown"][0]["strategy_id"]
            if len(event["strategy_breakdown"]) == 1
            else "multiple"
        )
        realized_events.append(event)
    realized_events.sort(key=lambda row: (row["exit_timestamp"], row["exit_order_id"]))

    open_inventory: list[dict[str, Any]] = []
    for symbol, lots in sorted(inventory.items()):
        if not lots:
            continue
        direction = lots[0]["direction"]
        quantity = sum((lot["quantity"] for lot in lots), Decimal("0"))
        cost = sum((lot["quantity"] * lot["price"] for lot in lots), Decimal("0"))
        open_inventory.append(
            {
                "symbol": symbol,
                "direction": "long" if direction > 0 else "short",
                "quantity": _float(quantity, 10),
                "average_entry_price": _float(cost / quantity, 8) if quantity > 0 else 0.0,
                "open_lot_count": len(lots),
            }
        )

    symbol_rows: list[dict[str, Any]] = []
    for symbol, summary in sorted(per_symbol.items()):
        symbol_rows.append(
            {
                "symbol": symbol,
                "realized_pnl": _float(summary["realized_pnl"]),
                "long_realized_pnl": _float(summary["long_realized_pnl"]),
                "short_realized_pnl": _float(summary["short_realized_pnl"]),
                "matched_quantity": _float(summary["matched_quantity"], 10),
                "closing_order_count": len(summary["closing_order_ids"]),
                "matched_lot_count": int(summary["matched_lot_count"]),
            }
        )

    strategy_rows: list[dict[str, Any]] = []
    for strategy_id, summary in sorted(per_strategy.items()):
        strategy_rows.append(
            {
                "strategy_id": strategy_id,
                "realized_pnl": _float(summary["realized_pnl"]),
                "matched_quantity": _float(summary["matched_quantity"], 10),
                "closing_order_count": len(summary["closing_order_ids"]),
                "matched_lot_count": int(summary["matched_lot_count"]),
            }
        )

    realized_pnl = sum((_decimal(event["realized_pnl"]) for event in realized_events), Decimal("0"))
    estimated_execution_costs = sum((_decimal(event.get("estimated_execution_costs")) for event in realized_events), Decimal("0"))
    estimated_live_net_pnl = sum((_decimal(event.get("estimated_live_net_pnl")) for event in realized_events), Decimal("0"))
    long_pnl = sum((_decimal(row["long_realized_pnl"]) for row in symbol_rows), Decimal("0"))
    short_pnl = sum((_decimal(row["short_realized_pnl"]) for row in symbol_rows), Decimal("0"))
    winning_events = sum(1 for event in realized_events if event["realized_pnl"] > 0)
    losing_events = sum(1 for event in realized_events if event["realized_pnl"] < 0)
    unknown_strategy_match_count = int(per_strategy.get("unknown", {}).get("matched_lot_count") or 0)

    confidence_reasons: list[str] = []
    if history_limit_reached:
        confidence_reasons.append("broker_order_limit_reached_older_fills_may_be_missing")
    if inferred_stock_classification:
        confidence_reasons.append("one_or_more_stock_asset_classes_were_inferred")
    if interfering_manual_stock_fill_count:
        confidence_reasons.append("manual_stock_fills_were_excluded_so_inventory_interactions_are_not_provable")
    if invalid_fill_count:
        confidence_reasons.append("one_or_more_stock_fill_rows_were_invalid")
    if missing_timestamp_count:
        confidence_reasons.append("one_or_more_stock_fills_had_no_usable_timestamp")
    if ambiguous_timestamp_groups:
        confidence_reasons.append("opposing_fills_shared_a_timestamp_so_ordering_is_ambiguous")
    if unmatched_close_events:
        confidence_reasons.append("one_or_more_close_orders_have_no_known_entry_cost_basis")

    is_exact = not confidence_reasons
    if is_exact:
        confidence = "exact"
        confidence_score = 100
    elif realized_events:
        confidence = "partial"
        closing_coverage = (
            total_matched_quantity / (total_matched_quantity + total_unmatched_close_quantity)
            if total_matched_quantity + total_unmatched_close_quantity > 0
            else Decimal("1")
        )
        confidence_score = max(1, min(95, int(closing_coverage * Decimal("85"))))
    else:
        confidence = "insufficient"
        confidence_score = 0

    for event in realized_events:
        event["is_exact"] = bool(is_exact and event.get("unmatched_quantity", 0.0) <= 0)
        event["confidence"] = "exact" if event["is_exact"] else confidence

    strategy_trade_rows: list[dict[str, Any]] = []
    for event in realized_events:
        for breakdown in list(event.get("strategy_breakdown") or []):
            strategy_trade_rows.append(
                {
                    "strategy_id": breakdown.get("strategy_id"),
                    "strategy_version": "unknown",
                    "entry_timestamp": (event.get("matched_lots") or [{}])[0].get("entry_timestamp"),
                    "exit_timestamp": event.get("exit_timestamp"),
                    "entry_price": (
                        float(breakdown.get("entry_notional") or 0.0) / float(breakdown.get("quantity") or 1.0)
                        if float(breakdown.get("quantity") or 0.0) > 0
                        else 0.0
                    ),
                    "exit_price": event.get("average_exit_price"),
                    "quantity": breakdown.get("quantity"),
                    "realized_gross_pnl": breakdown.get("realized_pnl"),
                    "estimated_fees": 0.0,
                    "estimated_slippage": breakdown.get("estimated_execution_costs"),
                    "net_pnl": breakdown.get("realized_pnl"),
                    "percentage_return": (
                        float(breakdown.get("realized_pnl") or 0.0) / float(breakdown.get("entry_notional") or 1.0)
                        if float(breakdown.get("entry_notional") or 0.0) > 0
                        else 0.0
                    ),
                    "holding_duration_hours": event.get("holding_duration_hours"),
                    "market_regime": "unknown",
                }
            )
    strategy_scoreboard = build_strategy_leaderboard(strategy_trade_rows)
    strategy_policy_settings = strategy_policy_from_environment()
    for row in strategy_scoreboard:
        row["execution_policy"] = evaluate_strategy_execution_policy(
            str(row.get("strategy_id") or ""),
            strategy_scoreboard,
            settings=strategy_policy_settings,
        )

    timestamps = [fill["timestamp"] for fill in valid_fills]
    return {
        "source": "alpaca_actual_filled_stock_orders",
        "scope": "bot_stock_orders_only" if bot_orders_only else "all_stock_orders",
        "calculation_method": "fifo_actual_filled_quantity_and_average_fill_price",
        "read_only": True,
        "database_mutated": False,
        "is_exact": is_exact,
        "history_complete": is_exact,
        "confidence": confidence,
        "confidence_score": confidence_score,
        "confidence_reasons": confidence_reasons,
        "realized_stock_pnl": _float(realized_pnl),
        "net_pnl": _float(realized_pnl),
        "estimated_execution_costs": _float(estimated_execution_costs),
        "estimated_live_net_pnl": _float(estimated_live_net_pnl),
        "execution_cost_assumptions": dict(estimate_stock_round_trip_costs(
            entry_notional=0.0,
            exit_notional=0.0,
            direction="long",
            settings=execution_cost_settings,
        ).get("assumptions") or {}),
        "long_realized_pnl": _float(long_pnl),
        "short_realized_pnl": _float(short_pnl),
        "closed_trade_count": len(realized_events),
        "closing_order_count": len(realized_events),
        "matched_lot_count": matched_lot_count,
        "winning_closes": winning_events,
        "losing_closes": losing_events,
        "valid_stock_fill_count": len(valid_fills),
        "raw_order_record_count": len(raw_orders),
        "duplicate_snapshot_count": duplicate_snapshot_count,
        "invalid_fill_count": invalid_fill_count,
        "missing_timestamp_count": missing_timestamp_count,
        "inferred_stock_classification_count": inferred_stock_classification,
        "manual_stock_fill_count": manual_stock_fill_count,
        "interfering_manual_stock_fill_count": interfering_manual_stock_fill_count,
        "unrelated_manual_stock_fill_count": unrelated_manual_stock_fill_count,
        "interfering_manual_stock_symbols": interfering_manual_stock_symbols,
        "ambiguous_timestamp_group_count": ambiguous_timestamp_groups,
        "unmatched_close_count": len(unmatched_close_events),
        "unmatched_close_quantity": _float(total_unmatched_close_quantity, 10),
        "strategy_attribution_complete": unknown_strategy_match_count == 0,
        "unknown_strategy_match_count": unknown_strategy_match_count,
        "history_limit": int(history_limit) if history_limit is not None else None,
        "history_limit_reached": bool(history_limit_reached),
        "history_start": min(timestamps).isoformat() if timestamps else "",
        "history_end": max(timestamps).isoformat() if timestamps else "",
        "ignored_order_counts": ignored,
        "realized_events": realized_events,
        "per_symbol": symbol_rows,
        "per_strategy": strategy_rows,
        "strategy_scoreboard": strategy_scoreboard,
        "open_inventory": open_inventory,
        "unmatched_close_events": unmatched_close_events,
        "notes": [
            "Only actual broker filled_quantity and average_fill_price values are used.",
            "Crypto and option orders are excluded.",
            "This diagnostic never inserts, updates, or deletes closed-trade database rows.",
            "A partial or insufficient result must not replace the durable dashboard P/L ledger.",
        ],
    }


def realized_events_by_exit_order_id(reconstruction: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index reconstructed close events for read-only dashboard order rendering."""
    return {
        str(event.get("exit_order_id") or ""): dict(event)
        for event in list((reconstruction or {}).get("realized_events") or [])
        if str(event.get("exit_order_id") or "").strip()
    }
