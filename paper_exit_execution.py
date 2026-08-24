from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Callable

from order_lifecycle import track_order_lifecycle
from paper_execution_repository import PaperValidationRunPayload
from paper_reconciliation import reconcile_paper_positions
from sprint_10_2_execution_validation import _execution_fingerprint


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _position_weights(positions: dict[str, dict[str, Any]], equity: float) -> dict[str, dict[str, float]]:
    base = max(float(equity), 1.0)
    return {
        str(symbol).upper(): {
            "quantity": _as_float((payload or {}).get("quantity"), 0.0),
            "weight": round(
                _as_float((payload or {}).get("quantity"), 0.0)
                * _as_float((payload or {}).get("current_price") or (payload or {}).get("avg_price"), 0.0)
                / base,
                10,
            ),
        }
        for symbol, payload in dict(positions or {}).items()
        if abs(_as_float((payload or {}).get("quantity"), 0.0)) > 1e-8
    }


def execute_guard_exit(
    exit_candidate: dict[str, Any],
    *,
    broker: Any,
    broker_positions: dict[str, dict[str, Any]],
    broker_cash: float,
    broker_buying_power: float,
    broker_equity: float,
    execution_repo: Any,
    cycle_run_id: str,
    started_at: str,
    dry_run: bool,
    paper_execution_enabled: bool,
    allow_fractional: bool,
    reconciliation_tolerance: float,
    persist: bool,
    lifecycle_tracker: Callable[..., dict[str, Any]] = track_order_lifecycle,
) -> dict[str, Any]:
    symbol = str(exit_candidate.get("symbol") or "").strip().upper()
    reason = str(exit_candidate.get("exit_reason") or "position_guard_exit").strip()
    position = dict((broker_positions or {}).get(symbol) or {})
    signed_position_quantity = _as_float(position.get("quantity"), 0.0)
    position_side = "SHORT" if signed_position_quantity < 0 else "LONG"
    close_side = "BUY" if position_side == "SHORT" else "SELL"
    quantity = min(
        max(_as_float(exit_candidate.get("quantity"), 0.0), 0.0),
        abs(signed_position_quantity),
    )
    reference_price = _as_float(exit_candidate.get("current_market_price"), 0.0)
    if not symbol or quantity <= 0 or reference_price <= 0:
        return {
            "status": "risk_rejected",
            "confirmed_order_count": 0,
            "paper_order": {"symbol": symbol, "side": close_side, "quantity": quantity},
            "risk_result": {"approved": False, "checks": {"position_exists": quantity > 0, "current_price": reference_price > 0}},
            "reconciliation": {},
            "execution_counters": {
                "orders_recommended": 1,
                "orders_submission_requested": 0,
                "orders_submitted": 0,
                "orders_filled": 0,
                "orders_rejected": 0,
            },
        }

    trade_date = str(started_at or _utc_iso())[:10]
    execution_fingerprint = _execution_fingerprint(symbol, quantity, f"PAPER_EXIT:{close_side}:{reason}:{trade_date}")
    existing = execution_repo.fetch_latest_submitting_run_by_execution_fingerprint(execution_fingerprint)
    if existing is not None:
        return {
            "status": "duplicate_rejected",
            "confirmed_order_count": 0,
            "paper_order": {"symbol": symbol, "side": close_side, "quantity": quantity, "submission_status": "duplicate_rejected"},
            "risk_result": {"approved": False, "checks": {"duplicate_protection": False}, "duplicate_run": existing},
            "reconciliation": {},
            "execution_counters": {
                "orders_recommended": 1,
                "orders_submission_requested": 0,
                "orders_submitted": 0,
                "orders_filled": 0,
                "orders_rejected": 0,
            },
        }

    submission_requested = (not bool(dry_run)) and bool(paper_execution_enabled)
    if not submission_requested:
        return {
            "status": "exit_recommended",
            "confirmed_order_count": 0,
            "paper_order": {
                "symbol": symbol,
                "side": close_side,
                "quantity": quantity,
                "notional": round(quantity * reference_price, 6),
                "submission_status": "not_submitted",
                "exit_reason": reason,
            },
            "risk_result": {
                "approved": True,
                "checks": {"position_exists": True, "current_price": True, "duplicate_protection": True},
            },
            "reconciliation": {},
            "execution_counters": {
                "orders_recommended": 1,
                "orders_submission_requested": 0,
                "orders_submitted": 0,
                "orders_filled": 0,
                "orders_rejected": 0,
            },
        }

    client_order_id = f"qtb-exit-{execution_fingerprint[:32]}"
    response = dict(
        broker.submit_order(
            side=close_side.lower(),
            ticker=symbol,
            quantity=quantity,
            client_order_id=client_order_id,
            order_type="market",
            time_in_force="day",
            allow_fractional=bool(allow_fractional),
            wait_for_fill=False,
            reference_price=reference_price,
        )
        or {}
    )
    response_status = str(response.get("status") or "unknown").strip().lower()
    accepted_statuses = {"new", "accepted", "pending", "pending_new", "partially_filled", "filled"}
    accepted_by_broker = response_status in accepted_statuses
    if accepted_by_broker:
        lifecycle = lifecycle_tracker(broker=broker, initial_order=response, poll_seconds=1.0, max_wait_seconds=45.0)
    else:
        lifecycle = {
            "order": response,
            "final_status": response_status,
            "submission_time": _utc_iso(),
            "fill_time": "",
            "execution_latency_seconds": 0.0,
            "status_transitions": [],
        }

    final_order = dict(lifecycle.get("order") or response)
    final_status = str(lifecycle.get("final_status") or final_order.get("status") or response_status).strip().lower()
    requested_quantity = _as_float(final_order.get("requested_quantity") or quantity, quantity)
    filled_quantity = _as_float(final_order.get("filled_quantity"), 0.0)
    counted_quantity = filled_quantity if final_status in {"filled", "partially_filled"} else 0.0
    fill_price = _as_float(final_order.get("average_fill_price") or reference_price, reference_price)

    post_positions = dict(broker.get_positions() or {})
    try:
        post_account = dict(broker.get_account() or {})
        post_cash = _as_float(post_account.get("cash"), broker_cash)
        post_buying_power = _as_float(post_account.get("buying_power"), broker_buying_power)
    except Exception:
        post_cash = _as_float(getattr(broker, "get_cash", lambda: broker_cash)(), broker_cash)
        post_buying_power = _as_float(getattr(broker, "get_buying_power", lambda: broker_buying_power)(), broker_buying_power)

    expected_positions = {key: dict(value or {}) for key, value in dict(broker_positions or {}).items()}
    if counted_quantity > 0:
        signed_fill = counted_quantity if close_side == "BUY" else -counted_quantity
        expected_remaining = signed_position_quantity + signed_fill
        if abs(expected_remaining) > float(reconciliation_tolerance):
            expected_positions[symbol] = {**position, "quantity": expected_remaining}
        else:
            expected_positions.pop(symbol, None)

    signed_cash_flow = counted_quantity * fill_price * (1.0 if close_side == "BUY" else -1.0)
    expected_cash = round(float(broker_cash) - signed_cash_flow, 6)
    reconciliation = reconcile_paper_positions(
        planned_positions=_position_weights(expected_positions, broker_equity),
        actual_positions=_position_weights(post_positions, broker_equity),
        expected_cash=expected_cash,
        actual_cash=post_cash,
        expected_buying_power=None,
        actual_buying_power=post_buying_power,
        orders=[
            {
                "submission_status": final_status,
                "filled_quantity": counted_quantity,
                "quantity": requested_quantity,
                "average_fill_price": fill_price,
            }
        ],
        tolerance=float(reconciliation_tolerance),
    )
    reconciliation_status = str(reconciliation.get("reconciliation_status") or "unknown").lower()
    completed = final_status == "filled" and reconciliation_status == "matched"
    pending = reconciliation_status == "pending"
    execution_status = "completed" if completed else "pending" if pending else "failed"
    rejected_statuses = {"rejected", "failed", "expired", "canceled", "cancelled", "submission_blocked_by_config"}

    validation_run_id = f"{cycle_run_id}-exit-{symbol.lower()}"
    paper_order_id = str(final_order.get("order_id") or final_order.get("id") or f"{validation_run_id}-0001")
    if persist:
        entry_lookup = getattr(execution_repo, "fetch_latest_filled_entry", None)
        if callable(entry_lookup):
            entry_order = dict(entry_lookup(symbol, "SELL" if position_side == "SHORT" else "BUY") or {})
        else:
            legacy_lookup = getattr(execution_repo, "fetch_latest_filled_buy", None)
            entry_order = dict(legacy_lookup(symbol) or {}) if callable(legacy_lookup) and position_side == "LONG" else {}
        execution_repo.save_validation_run(
            PaperValidationRunPayload(
                run={
                    "run_id": validation_run_id,
                    "run_fingerprint": execution_fingerprint,
                    "execution_fingerprint": execution_fingerprint,
                    "approval_id": f"position-guard-{symbol}-{trade_date}",
                    "strategy_id": str(entry_order.get("strategy_id") or "position_guard"),
                    "strategy_version": str(entry_order.get("strategy_version") or "v1"),
                    "strategy_fingerprint": "position_guard:v1",
                    "research_run_id": cycle_run_id,
                    "scanner_timestamp": started_at,
                    "started_at": started_at,
                    "completed_at": _utc_iso(),
                    "mode": "PAPER",
                    "status": execution_status,
                    "dry_run": False,
                    "proposed_order_count": 1,
                    "approved_order_count": 1,
                    "rejected_order_count": 0,
                    "submitted_order_count": 1 if accepted_by_broker else 0,
                    "filled_order_count": 1 if final_status == "filled" else 0,
                    "failed_order_count": 1 if final_status in rejected_statuses else 0,
                    "configuration": {"source": "paper_position_guard", "exit_reason": reason},
                    "risk_snapshot": {
                        "checks": {"position_exists": True, "current_price": True, "duplicate_protection": True},
                        "position_review": exit_candidate,
                    },
                    "performance": {},
                    "warnings": list(reconciliation.get("warnings") or []),
                    "error_message": None if execution_status != "failed" else f"exit_status={final_status}; reconciliation={reconciliation_status}",
                    "created_at": started_at,
                    "updated_at": _utc_iso(),
                },
                orders=[
                    {
                        "paper_order_id": f"{validation_run_id}-0001",
                        "symbol": symbol,
                        "side": close_side,
                        "quantity": quantity,
                        "notional": round(quantity * reference_price, 6),
                        "target_weight": 0.0,
                        "current_weight": round(signed_position_quantity * reference_price / max(float(broker_equity), 1.0), 10),
                        "weight_delta": round(-(signed_position_quantity * reference_price / max(float(broker_equity), 1.0)), 10),
                        "reference_price": reference_price,
                        "proposed_at": started_at,
                        "risk_status": "approved",
                        "risk_reason": reason,
                        "submission_status": final_status if accepted_by_broker else "not_submitted",
                        "broker_order_id": paper_order_id,
                        "client_order_id": client_order_id,
                        "requested_quantity": requested_quantity,
                        "broker_backend": str(final_order.get("broker_backend") or getattr(broker, "backend", "ALPACA")),
                        "order_type": str(final_order.get("order_type") or "market"),
                        "time_in_force": str(final_order.get("time_in_force") or "day"),
                        "broker_updated_at": str(final_order.get("updated_at") or _utc_iso()),
                        "rejection_reason": str(final_order.get("rejection_reason") or ""),
                        "submitted_at": lifecycle.get("submission_time"),
                        "filled_quantity": counted_quantity,
                        "average_fill_price": fill_price if counted_quantity > 0 else 0.0,
                        "filled_at": lifecycle.get("fill_time") if counted_quantity > 0 else None,
                        "canceled_at": _utc_iso() if final_status in {"canceled", "cancelled"} else None,
                        "failed_at": _utc_iso() if final_status in rejected_statuses else None,
                        "error_message": None,
                        "order_payload": {
                            "source": "paper_position_guard",
                            "exit_reason": reason,
                            "status_transitions": lifecycle.get("status_transitions") or [],
                            "execution_latency_seconds": lifecycle.get("execution_latency_seconds"),
                            "dry_run": False,
                        },
                        "created_at": started_at,
                        "updated_at": _utc_iso(),
                    }
                ],
                position_snapshots=[
                    {
                        "snapshot_id": f"{validation_run_id}-post",
                        "captured_at": _utc_iso(),
                        "positions": post_positions,
                        "cash": post_cash,
                        "buying_power": post_buying_power,
                        "portfolio_value": round(
                            post_cash
                            + sum(
                                _as_float((payload or {}).get("quantity"), 0.0)
                                * _as_float((payload or {}).get("current_price") or (payload or {}).get("avg_price"), 0.0)
                                for payload in post_positions.values()
                            ),
                            6,
                        ),
                        "gross_exposure": 0.0,
                        "net_exposure": 0.0,
                        "concentration": {},
                        "reconciliation_status": reconciliation_status,
                        "warnings": reconciliation.get("warnings") or [],
                    }
                ],
            )
        )

        save_transitions = getattr(execution_repo, "save_order_status_transitions", None)
        if callable(save_transitions):
            save_transitions(
                run_id=validation_run_id,
                symbol=symbol,
                paper_order_id=f"{validation_run_id}-0001",
                transitions=list(lifecycle.get("status_transitions") or []),
            )

        post_quantity = _as_float((post_positions.get(symbol) or {}).get("quantity"), 0.0)
        if completed and abs(post_quantity) <= float(reconciliation_tolerance):
            entry_price = _as_float(entry_order.get("average_fill_price") or position.get("avg_price"), fill_price)
            closed_quantity = min(quantity, counted_quantity)
            gross_pnl = (
                (entry_price - fill_price) * closed_quantity
                if position_side == "SHORT"
                else (fill_price - entry_price) * closed_quantity
            )
            estimated_slippage = abs(
                float(os.getenv("ESTIMATED_SLIPPAGE_BPS", "5")) / 10000.0 * fill_price * closed_quantity
            )
            estimated_fees = abs(float(os.getenv("ESTIMATED_FEES_PER_TRADE", "0")))
            net_pnl = gross_pnl - estimated_slippage - estimated_fees
            percentage_return = net_pnl / (entry_price * closed_quantity) if entry_price > 0 and closed_quantity > 0 else 0.0
            execution_repo.save_closed_trade(
                {
                    "trade_id": f"{execution_fingerprint}-closed",
                    "strategy_id": str(entry_order.get("strategy_id") or "position_guard"),
                    "strategy_version": str(entry_order.get("strategy_version") or "v1"),
                    "symbol": symbol,
                    "entry_timestamp": entry_order.get("filled_at") or entry_order.get("submitted_at") or started_at,
                    "exit_timestamp": _utc_iso(),
                    "entry_price": entry_price,
                    "exit_price": fill_price,
                    "quantity": closed_quantity,
                    "realized_gross_pnl": round(gross_pnl, 6),
                    "estimated_fees": round(estimated_fees, 6),
                    "estimated_slippage": round(estimated_slippage, 6),
                    "net_pnl": round(net_pnl, 6),
                    "percentage_return": round(percentage_return, 6),
                    "holding_duration_hours": 0.0,
                    "max_adverse_excursion": min(float(exit_candidate.get("return_percent") or 0.0) / 100.0, 0.0),
                    "max_favorable_excursion": max(float(exit_candidate.get("return_percent") or 0.0) / 100.0, 0.0),
                    "exit_reason": reason,
                    "market_regime": "unknown",
                    "close_type": "risk_guard_exit",
                }
            )

    return {
        "status": execution_status,
        "confirmed_order_count": 1 if completed and accepted_by_broker else 0,
        "paper_order": {
            **final_order,
            "order_id": paper_order_id,
            "symbol": symbol,
            "side": close_side,
            "quantity": quantity,
            "notional": round(quantity * reference_price, 6),
            "submission_status": final_status,
            "exit_reason": reason,
        },
        "risk_result": {
            "approved": True,
            "checks": {"position_exists": True, "current_price": True, "duplicate_protection": True},
        },
        "reconciliation": reconciliation,
        "execution_counters": {
            "orders_recommended": 1,
            "orders_submission_requested": 1,
            "orders_submitted": 1 if accepted_by_broker else 0,
            "orders_filled": 1 if final_status == "filled" else 0,
            "orders_rejected": 1 if final_status in rejected_statuses else 0,
        },
    }
