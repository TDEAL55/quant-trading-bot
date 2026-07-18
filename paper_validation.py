from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from config import (
    BENCHMARK_SYMBOL,
    PAPER_VALIDATION_ALLOW_FRACTIONAL,
    PAPER_VALIDATION_CASH_BUFFER,
    PAPER_VALIDATION_DEFAULT_MODE,
    PAPER_VALIDATION_DUPLICATE_RUN_PROTECTION,
    PAPER_VALIDATION_ENABLED,
    PAPER_VALIDATION_MAX_ORDERS,
    PAPER_VALIDATION_MAX_ORDER_NOTIONAL,
    PAPER_VALIDATION_MAX_PRICE_AGE_MINUTES,
    PAPER_VALIDATION_MAX_RESEARCH_SNAPSHOT_AGE_MINUTES,
    PAPER_VALIDATION_MAX_SNAPSHOT_AGE_MINUTES,
    PAPER_VALIDATION_MAX_TURNOVER,
    PAPER_VALIDATION_MIN_ORDER_NOTIONAL,
    PAPER_VALIDATION_QUANTITY_PRECISION,
    PAPER_VALIDATION_REBALANCE_TOLERANCE,
    PAPER_VALIDATION_RECONCILIATION_TOLERANCE,
    PAPER_VALIDATION_REQUIRE_APPROVAL,
)
from evaluation_repository import MonitoringEvaluationRepository
from logger_setup import logger
from paper_approval import build_approval_record, validate_approval
from paper_broker import create_paper_broker
from paper_execution_repository import MonitoringPaperExecutionRepository, PaperValidationRunPayload
from paper_order_planner import OrderPlannerSettings, plan_paper_orders
from paper_reconciliation import reconcile_paper_positions
from portfolio_research_data import normalize_portfolio_research_rows, run_portfolio_method
from risk_manager import RiskManager
from strategy_definitions import built_in_strategy_definitions, definition_by_id
from strategy_lab_data import apply_strategy_filters


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso() -> str:
    return _utc_now().isoformat()


def _stable_json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True, separators=(",", ":"))


def _parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _age_minutes(value: Any, now_dt: datetime) -> float | None:
    text = str(value or "").strip()
    dt = None
    if text and len(text) == 10 and text.count("-") == 2:
        try:
            dt = datetime.fromisoformat(text + "T23:59:59+00:00")
        except Exception:
            dt = None
    if dt is None:
        dt = _parse_dt(value)
    if dt is None and text:
        try:
            dt = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        except Exception:
            dt = None
    if dt is None:
        return None
    return (now_dt - dt).total_seconds() / 60.0


def _run_fingerprint(
    approval_id: str,
    strategy_fingerprint: str,
    research_run_id: str,
    scanner_timestamp: str,
    target_portfolio_fingerprint: str,
    mode: str,
) -> str:
    payload = {
        "approval_id": approval_id,
        "strategy_fingerprint": strategy_fingerprint,
        "research_run_id": research_run_id,
        "scanner_timestamp": scanner_timestamp,
        "target_portfolio_fingerprint": target_portfolio_fingerprint,
        "mode": str(mode or "").upper(),
    }
    digest = hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()
    return digest[:24]


def _attempt_fingerprint(execution_fingerprint: str, dry_run: bool, execute: bool) -> str:
    payload = {
        "execution_fingerprint": str(execution_fingerprint or ""),
        "attempt_kind": "dry_run" if dry_run or not execute else "execute",
        "created_at": _utc_iso(),
    }
    digest = hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()
    return digest[:24]


def _build_run_id(dry_run: bool, execute: bool) -> str:
    suffix = "dry" if dry_run or not execute else "exec"
    return f"paper-validation-{_utc_now().strftime('%Y%m%d%H%M%S%f')}-{suffix}"


def _normalize_positions(raw: Any) -> dict[str, dict[str, float]]:
    if isinstance(raw, dict):
        result = {}
        for symbol, payload in raw.items():
            item = payload if isinstance(payload, dict) else {}
            result[str(symbol).upper()] = {
                "quantity": float(item.get("quantity") or 0.0),
                "avg_price": float(item.get("avg_price") or 0.0),
            }
        return result
    result: dict[str, dict[str, float]] = {}
    for payload in raw or []:
        symbol = str((payload or {}).get("symbol") or "").upper()
        if not symbol:
            continue
        result[symbol] = {
            "quantity": float((payload or {}).get("quantity") or 0.0),
            "avg_price": float((payload or {}).get("avg_price") or 0.0),
        }
    return result


def _latest_snapshot_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str, str]:
    if not rows:
        return [], "", ""
    ordered = sorted(rows, key=lambda item: (str(item.get("_observation_date") or ""), str(item.get("_run_id") or ""), int(item.get("_rank") or 10**9)))
    latest = ordered[-1]
    latest_date = str(latest.get("_observation_date") or "")
    latest_run = str(latest.get("_run_id") or "")
    scoped = [item for item in ordered if str(item.get("_observation_date") or "") == latest_date and str(item.get("_run_id") or "") == latest_run]
    return scoped, latest_run, latest_date


def _build_target_from_snapshot(
    rows: list[dict[str, Any]],
    weighting_method: str,
    benchmark: str,
    top_n: int,
    max_position_weight: float,
    sector_cap: float,
    min_holdings: int,
) -> tuple[dict[str, float], dict[str, Any], list[dict[str, Any]]]:
    run = run_portfolio_method(
        rows,
        method=str(weighting_method),
        horizon=20,
        benchmark_symbol=benchmark,
        top_n=int(top_n),
        max_position_weight=float(max_position_weight),
        sector_cap=float(sector_cap),
        min_holdings=int(min_holdings),
    )
    snapshots = list(run.get("snapshots") or [])
    if not snapshots:
        return {}, {}, []
    snapshot = snapshots[-1]
    holdings = list(snapshot.get("holdings") or [])
    target_weights = {str(item.get("symbol") or "").upper(): float(item.get("weight") or 0.0) for item in holdings if str(item.get("symbol") or "").strip()}
    return target_weights, snapshot, holdings


def _build_reference_prices(latest_rows: list[dict[str, Any]], current_positions: dict[str, dict[str, float]]) -> dict[str, float]:
    prices = {}
    for row in latest_rows:
        symbol = str(row.get("_symbol") or row.get("symbol") or "").upper()
        if not symbol:
            continue
        value = row.get("candidate_latest_price")
        if value is None:
            value = row.get("observation_price")
        try:
            numeric = float(value)
            if numeric > 0:
                prices[symbol] = numeric
        except (TypeError, ValueError):
            continue
    for symbol, payload in current_positions.items():
        if symbol not in prices:
            avg_price = float(payload.get("avg_price") or 0.0)
            if avg_price > 0:
                prices[symbol] = avg_price
    return prices


def _position_weights(positions: dict[str, dict[str, float]], reference_prices: dict[str, float], cash: float) -> dict[str, dict[str, float]]:
    equity_value = 0.0
    for symbol, payload in positions.items():
        qty = float(payload.get("quantity") or 0.0)
        price = float(reference_prices.get(symbol) or payload.get("avg_price") or 0.0)
        equity_value += qty * price
    total_value = float(cash) + equity_value
    if total_value <= 0:
        total_value = 1.0

    result: dict[str, dict[str, float]] = {}
    for symbol, payload in positions.items():
        qty = float(payload.get("quantity") or 0.0)
        price = float(reference_prices.get(symbol) or payload.get("avg_price") or 0.0)
        result[symbol] = {
            "quantity": qty,
            "weight": (qty * price) / total_value if total_value > 0 else 0.0,
        }
    return result


@dataclass(frozen=True)
class RunnerContext:
    strategy_definition: Any
    approval: dict[str, Any]
    latest_rows: list[dict[str, Any]]
    latest_research_run_id: str
    latest_observation_date: str


def _build_runner_context(
    database_url: str | None,
    approval: dict[str, Any],
    strategy_id: str,
    horizon: int,
) -> RunnerContext:
    repo = MonitoringEvaluationRepository(database_url=database_url)
    try:
        repo.db.ensure_schema()
        rows = repo.fetch_evaluation_rows_for_dashboard(limit=20000)
    finally:
        repo.close()

    normalized = normalize_portfolio_research_rows(rows, horizon=horizon)
    definitions = built_in_strategy_definitions()
    strategy_definition = definition_by_id(definitions, strategy_id)
    filtered = apply_strategy_filters(normalized.get("rows") or [], strategy_definition)
    latest_rows, latest_run_id, latest_obs = _latest_snapshot_rows(filtered.get("rows") or [])
    if not latest_rows:
        raise RuntimeError("no eligible candidates")

    return RunnerContext(
        strategy_definition=strategy_definition,
        approval=approval,
        latest_rows=latest_rows,
        latest_research_run_id=latest_run_id,
        latest_observation_date=latest_obs,
    )


def run_paper_validation(
    database_url: str | None,
    approval_id: str,
    mode: str = PAPER_VALIDATION_DEFAULT_MODE,
    dry_run: bool = True,
    execute: bool = False,
    confirm: bool = False,
    broker: Any | None = None,
    risk_manager: RiskManager | None = None,
    now_ts: str | None = None,
    persist: bool = True,
    allow_failed_retry: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    now_dt = _parse_dt(now_ts) or _utc_now()
    run_started_at = _utc_iso()

    selected_mode = str(mode or PAPER_VALIDATION_DEFAULT_MODE).strip().upper()
    if selected_mode == "LIVE":
        raise RuntimeError("LIVE mode is rejected")
    if selected_mode not in {"PAPER", "SIMULATION"}:
        raise RuntimeError("mode must be PAPER or SIMULATION")

    if execute and not confirm:
        raise RuntimeError("execute-paper requires explicit confirmation")

    if execute and not PAPER_VALIDATION_ENABLED:
        raise RuntimeError("paper validation is disabled")

    repo = MonitoringPaperExecutionRepository(database_url=database_url)
    try:
        approval = repo.fetch_approval(approval_id)
        if PAPER_VALIDATION_REQUIRE_APPROVAL and not approval:
            raise RuntimeError("approval missing")

        if approval is None:
            raise RuntimeError("approval missing")

        strategy_id = str(approval.get("strategy_id") or "")
        strategy_version = str(approval.get("strategy_version") or "")
        strategy_fingerprint = str(approval.get("strategy_fingerprint") or "")
        portfolio_config = dict(approval.get("portfolio_configuration") or {})
        risk_config = dict(approval.get("risk_configuration") or {})
        horizon = int(approval.get("horizon") or 20)

        validation_started = time.perf_counter()
        approval_validation = validate_approval(
            approval=approval,
            expected_strategy_id=strategy_id,
            expected_strategy_version=strategy_version,
            expected_strategy_fingerprint=strategy_fingerprint,
            expected_portfolio_configuration=portfolio_config,
            expected_risk_configuration=risk_config,
            mode=selected_mode,
            broker_type="paper" if selected_mode == "PAPER" else "simulation",
            now_ts=now_dt.isoformat(),
        )
        approval_validation_time = time.perf_counter() - validation_started
        if not approval_validation.valid:
            raise RuntimeError("; ".join(approval_validation.reasons))

        data_started = time.perf_counter()
        context = _build_runner_context(database_url, approval, strategy_id, horizon=horizon)
        candidate_load_time = time.perf_counter() - data_started

        snapshot_age = _age_minutes(context.latest_observation_date, now_dt)
        if snapshot_age is None:
            raise RuntimeError("stale scanner snapshot rejection")
        if snapshot_age > float(PAPER_VALIDATION_MAX_SNAPSHOT_AGE_MINUTES):
            raise RuntimeError("stale scanner snapshot rejection")
        if snapshot_age > float(PAPER_VALIDATION_MAX_RESEARCH_SNAPSHOT_AGE_MINUTES):
            raise RuntimeError("stale research snapshot rejection")

        broker_instance = broker or create_paper_broker(mode=selected_mode)
        broker_mode = str(getattr(broker_instance, "mode", selected_mode) or "").upper()
        if broker_mode == "LIVE":
            raise RuntimeError("LIVE broker is rejected")

        account_started = time.perf_counter()
        current_positions = _normalize_positions(broker_instance.get_positions())
        pre_cash = float(broker_instance.get_buying_power() or 0.0)
        pre_portfolio_value = float(pre_cash + sum((float(item.get("quantity") or 0.0) * float(item.get("avg_price") or 0.0)) for item in current_positions.values()))
        if pre_portfolio_value <= 0:
            pre_portfolio_value = max(pre_cash, 1.0)
        account_load_time = time.perf_counter() - account_started

        portfolio_started = time.perf_counter()
        target_weights, target_snapshot, target_holdings = _build_target_from_snapshot(
            context.latest_rows,
            weighting_method=str(portfolio_config.get("weighting_method") or "equal_weight"),
            benchmark=str(approval.get("benchmark") or BENCHMARK_SYMBOL),
            top_n=int(portfolio_config.get("top_n") or 5),
            max_position_weight=float(portfolio_config.get("max_position_weight") or 0.30),
            sector_cap=float(portfolio_config.get("sector_cap") or 0.50),
            min_holdings=int(portfolio_config.get("min_holdings") or 1),
        )
        portfolio_time = time.perf_counter() - portfolio_started
        if not target_weights:
            raise RuntimeError("no eligible candidates")

        reference_prices = _build_reference_prices(context.latest_rows, current_positions)
        stale_price_rejections = []
        for symbol, price in reference_prices.items():
            if float(price) <= 0:
                stale_price_rejections.append(symbol)
        if stale_price_rejections:
            raise RuntimeError("stale price rejection")

        planning_started = time.perf_counter()
        planner_settings = OrderPlannerSettings(
            minimum_order_notional=float(portfolio_config.get("minimum_order_notional") or PAPER_VALIDATION_MIN_ORDER_NOTIONAL),
            maximum_order_notional=float(portfolio_config.get("maximum_order_notional") or PAPER_VALIDATION_MAX_ORDER_NOTIONAL),
            allow_fractional=bool(portfolio_config.get("allow_fractional", PAPER_VALIDATION_ALLOW_FRACTIONAL)),
            quantity_precision=int(portfolio_config.get("quantity_precision") or PAPER_VALIDATION_QUANTITY_PRECISION),
            rebalance_tolerance=float(portfolio_config.get("rebalance_tolerance") or PAPER_VALIDATION_REBALANCE_TOLERANCE),
            maximum_orders=int(portfolio_config.get("maximum_orders") or PAPER_VALIDATION_MAX_ORDERS),
            cash_buffer=float(portfolio_config.get("cash_buffer") or PAPER_VALIDATION_CASH_BUFFER),
        )
        plan_result = plan_paper_orders(
            target_weights=target_weights,
            current_positions=current_positions,
            reference_prices=reference_prices,
            portfolio_value=pre_portfolio_value,
            current_cash=pre_cash,
            settings=planner_settings,
        )
        order_planning_time = time.perf_counter() - planning_started

        if float(plan_result.get("summary", {}).get("estimated_turnover") or 0.0) > float(PAPER_VALIDATION_MAX_TURNOVER):
            raise RuntimeError("excessive turnover rejection")

        proposed_orders = list(plan_result.get("orders") or [])
        if len(proposed_orders) > int(PAPER_VALIDATION_MAX_ORDERS):
            raise RuntimeError("too many proposed orders")

        target_payload = {
            "weights": target_weights,
            "holdings": sorted(target_holdings, key=lambda row: str(row.get("symbol") or "")),
            "research_run_id": context.latest_research_run_id,
            "scanner_timestamp": context.latest_observation_date,
        }
        target_fingerprint = hashlib.sha256(_stable_json(target_payload).encode("utf-8")).hexdigest()[:24]
        execution_fingerprint = _run_fingerprint(
            approval_id=str(approval_id),
            strategy_fingerprint=strategy_fingerprint,
            research_run_id=context.latest_research_run_id,
            scanner_timestamp=context.latest_observation_date,
            target_portfolio_fingerprint=target_fingerprint,
            mode=selected_mode,
        )
        run_fingerprint = _attempt_fingerprint(execution_fingerprint=execution_fingerprint, dry_run=dry_run, execute=execute)
        run_id = _build_run_id(dry_run=dry_run, execute=execute)

        if PAPER_VALIDATION_DUPLICATE_RUN_PROTECTION and execute and not dry_run:
            existing = repo.fetch_latest_submitting_run_by_execution_fingerprint(execution_fingerprint)
            if existing:
                status = str(existing.get("status") or "")
                existing_run_id = str(existing.get("run_id") or "")
                if status in {"completed", "running"}:
                    raise RuntimeError(f"duplicate-run protection: prior_run_id={existing_run_id} status={status}")
                if status == "failed" and not allow_failed_retry:
                    raise RuntimeError(f"failed-run retry requires explicit override: prior_run_id={existing_run_id}")

        risk_started = time.perf_counter()
        risk = risk_manager or RiskManager(
            max_position_size=float(risk_config.get("max_position_size") or 0.25),
            max_daily_loss=float(risk_config.get("max_daily_loss") or 500),
            daily_loss_limit=float(risk_config.get("daily_loss_limit") or 500),
        )
        approved_orders = []
        rejected_orders = list(plan_result.get("rejections") or [])
        rejection_reasons: dict[str, int] = {}

        for order in proposed_orders:
            reference_price = float(order.get("reference_price") or 0.0)
            if reference_price <= 0:
                reason = "stale price rejection"
            else:
                trade_value = float(order.get("notional") or 0.0)
                approved = risk.approve_trade(pre_portfolio_value, trade_value, current_loss=0.0)
                reason = "approved" if approved else "risk_manager_rejection"
            if reason != "approved":
                rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
                order = dict(order)
                order["risk_status"] = "rejected"
                order["risk_reason"] = reason
                order["submission_status"] = "rejected"
                rejected_orders.append(order)
                continue
            order = dict(order)
            order["risk_status"] = "approved"
            order["risk_reason"] = "approved"
            order["submission_status"] = "planned"
            approved_orders.append(order)
        risk_time = time.perf_counter() - risk_started

        submission_started = time.perf_counter()
        submitted = 0
        filled = 0
        failed = 0
        finalized_orders = []

        for index, order in enumerate(approved_orders, start=1):
            item = dict(order)
            item["paper_order_id"] = f"{run_id}-{index:04d}"
            item["proposed_at"] = now_dt.isoformat()

            if dry_run or not execute:
                item["submission_status"] = "not_submitted_dry_run"
                finalized_orders.append(item)
                continue

            try:
                response = broker_instance.submit_order(
                    side=str(item.get("side") or "").lower(),
                    ticker=str(item.get("symbol") or ""),
                    quantity=float(item.get("quantity") or 0.0),
                )
                response = response or {}
                item["broker_order_id"] = str(response.get("order_id") or response.get("id") or "")
                item["submitted_at"] = _utc_iso()
                item["submission_status"] = str(response.get("status") or "submitted")
                item["filled_quantity"] = float(response.get("filled_quantity") or item.get("quantity") or 0.0)
                item["average_fill_price"] = float(response.get("average_fill_price") or item.get("reference_price") or 0.0)
                if item["filled_quantity"] > 0:
                    item["filled_at"] = _utc_iso()
                    filled += 1
                submitted += 1
            except Exception as exc:
                item["submission_status"] = "failed"
                item["failed_at"] = _utc_iso()
                item["error_message"] = f"{type(exc).__name__}: {exc}"
                failed += 1
            finalized_orders.append(item)

        for rejected in rejected_orders:
            item = dict(rejected)
            item.setdefault("paper_order_id", f"{run_id}-rej-{len(finalized_orders)+1:04d}")
            item.setdefault("proposed_at", now_dt.isoformat())
            item.setdefault("risk_status", "rejected")
            item.setdefault("risk_reason", item.get("reason") or "rejected")
            item.setdefault("submission_status", "rejected")
            finalized_orders.append(item)

        submission_time = time.perf_counter() - submission_started

        post_cash = float(broker_instance.get_buying_power() or pre_cash)
        post_positions = _normalize_positions(broker_instance.get_positions())
        expected_cash = pre_cash
        expected_positions = {symbol: {"quantity": float(item.get("quantity") or 0.0), "avg_price": float(item.get("avg_price") or 0.0)} for symbol, item in current_positions.items()}
        for item in finalized_orders:
            side = str(item.get("side") or "").upper()
            status = str(item.get("submission_status") or "")
            if status not in {"submitted", "filled", "partially_filled", "pending"}:
                continue
            filled_qty = float(item.get("filled_quantity") or 0.0)
            if filled_qty <= 0:
                continue
            fill_price = float(item.get("average_fill_price") or item.get("reference_price") or 0.0)
            if fill_price <= 0:
                continue
            symbol = str(item.get("symbol") or "").upper()
            position = expected_positions.setdefault(symbol, {"quantity": 0.0, "avg_price": fill_price})
            if side == "BUY":
                position["quantity"] = float(position.get("quantity") or 0.0) + filled_qty
                expected_cash -= filled_qty * fill_price
            elif side == "SELL":
                position["quantity"] = max(float(position.get("quantity") or 0.0) - filled_qty, 0.0)
                expected_cash += filled_qty * fill_price
            if float(position.get("quantity") or 0.0) <= 0:
                expected_positions.pop(symbol, None)

        expected_buying_power = expected_cash
        planned_positions = _position_weights(expected_positions, reference_prices, expected_cash)
        actual_position_payload = _position_weights(post_positions, reference_prices, post_cash)

        reconcile_started = time.perf_counter()
        reconciliation = reconcile_paper_positions(
            planned_positions=planned_positions,
            actual_positions=actual_position_payload,
            expected_cash=expected_cash,
            actual_cash=post_cash,
            expected_buying_power=expected_buying_power,
            actual_buying_power=post_cash,
            orders=finalized_orders,
            tolerance=float(PAPER_VALIDATION_RECONCILIATION_TOLERANCE),
        )
        reconciliation_time = time.perf_counter() - reconcile_started

        actual_turnover = 0.0
        for item in finalized_orders:
            status = str(item.get("submission_status") or "")
            if status not in {"submitted", "filled", "partially_filled", "pending"}:
                continue
            filled_qty = float(item.get("filled_quantity") or 0.0)
            fill_price = float(item.get("average_fill_price") or item.get("reference_price") or 0.0)
            if filled_qty <= 0 or fill_price <= 0:
                continue
            actual_turnover += abs(filled_qty * fill_price)
        actual_turnover = round(actual_turnover / max(pre_portfolio_value, 1.0), 6)

        component_time_map = {
            "candidate_load_time": candidate_load_time,
            "approval_validation_time": approval_validation_time,
            "portfolio_construction_time": portfolio_time,
            "order_planning_time": order_planning_time,
            "risk_check_time": risk_time,
            "paper_submission_time": submission_time,
            "reconciliation_time": reconciliation_time,
            "persistence_time": 0.0,
            "dashboard_payload_time": 0.0,
        }
        performance: dict[str, Any] = {}

        warnings = list(plan_result.get("holds") or [])
        status = "completed" if failed == 0 else "failed"

        run_payload = {
            "run_id": run_id,
            "run_fingerprint": run_fingerprint,
            "execution_fingerprint": execution_fingerprint,
            "approval_id": approval_id,
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "strategy_fingerprint": strategy_fingerprint,
            "research_run_id": context.latest_research_run_id,
            "scanner_timestamp": context.latest_observation_date,
            "started_at": run_started_at,
            "completed_at": _utc_iso(),
            "mode": selected_mode,
            "status": status,
            "dry_run": bool(dry_run),
            "proposed_order_count": len(finalized_orders),
            "approved_order_count": len([item for item in finalized_orders if str(item.get("risk_status") or "") == "approved"]),
            "rejected_order_count": len([item for item in finalized_orders if str(item.get("risk_status") or "") == "rejected"]),
            "submitted_order_count": submitted,
            "filled_order_count": filled,
            "failed_order_count": failed,
            "configuration": {
                "portfolio": portfolio_config,
                "risk": risk_config,
                "planner": planner_settings.__dict__,
                "target_portfolio_fingerprint": target_fingerprint,
                "expected_positions": expected_positions,
                "expected_cash": round(expected_cash, 6),
                "expected_buying_power": round(expected_buying_power, 6),
            },
            "risk_snapshot": {
                "max_position_size": risk.max_position_size,
                "max_daily_loss": risk.max_daily_loss,
                "daily_loss_limit": risk.daily_loss_limit,
                "rejection_reasons": rejection_reasons,
            },
            "performance": performance,
            "warnings": warnings,
            "error_message": None,
            "created_at": run_started_at,
            "updated_at": _utc_iso(),
        }

        snapshot_payload = {
            "snapshot_id": f"{run_id}-post",
            "captured_at": _utc_iso(),
            "positions": post_positions,
            "cash": post_cash,
            "buying_power": post_cash,
            "portfolio_value": pre_portfolio_value,
            "gross_exposure": round(sum(abs(float(weight)) for weight in target_weights.values()), 6),
            "net_exposure": round(sum(float(weight) for weight in target_weights.values()), 6),
            "concentration": {},
            "reconciliation_status": reconciliation.get("reconciliation_status"),
            "warnings": reconciliation.get("warnings") or [],
        }

        if persist:
            persist_started = time.perf_counter()
            repo.save_validation_run(PaperValidationRunPayload(run=run_payload, orders=finalized_orders, position_snapshots=[snapshot_payload]))
            component_time_map["persistence_time"] = time.perf_counter() - persist_started

        total_wall_clock_duration = time.perf_counter() - started
        total_measured_component_time = sum(float(value or 0.0) for value in component_time_map.values())
        unmeasured_overhead = max(total_wall_clock_duration - total_measured_component_time, 0.0)
        performance.update(
            {
                "candidate_load_time": round(component_time_map["candidate_load_time"], 6),
                "approval_validation_time": round(component_time_map["approval_validation_time"], 6),
                "account_load_time": round(account_load_time, 6),
                "portfolio_construction_time": round(component_time_map["portfolio_construction_time"], 6),
                "order_planning_time": round(component_time_map["order_planning_time"], 6),
                "risk_check_time": round(component_time_map["risk_check_time"], 6),
                "paper_submission_time": round(component_time_map["paper_submission_time"], 6),
                "reconciliation_time": round(component_time_map["reconciliation_time"], 6),
                "dashboard_payload_time": round(component_time_map["dashboard_payload_time"], 6),
                "persistence_time": round(component_time_map["persistence_time"], 6),
                "total_measured_component_time": round(total_measured_component_time, 6),
                "total_wall_clock_duration": round(total_wall_clock_duration, 6),
                "unmeasured_overhead": round(unmeasured_overhead, 6),
                "total_duration": round(total_wall_clock_duration, 6),
                "average_time_per_order": round(total_wall_clock_duration / max(len(finalized_orders), 1), 6),
                "average_time_per_proposed_order": round(total_wall_clock_duration / max(len(finalized_orders), 1), 6),
                "average_time_per_submitted_order": round(total_wall_clock_duration / max(submitted, 1), 6),
            }
        )

        result = {
            "run_id": run_id,
            "execution_fingerprint": execution_fingerprint,
            "approval_id": approval_id,
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "strategy_fingerprint": strategy_fingerprint,
            "mode": selected_mode,
            "dry_run": bool(dry_run),
            "status": status,
            "target_weights": target_weights,
            "target_holdings": target_holdings,
            "proposed_orders": finalized_orders,
            "holds": plan_result.get("holds") or [],
            "reconciliation": reconciliation,
            "metrics": {
                "candidate_count": len(context.latest_rows),
                "target_holding_count": len([value for value in target_weights.values() if float(value) > 0]),
                "proposed_orders": len(finalized_orders),
                "approved_orders": run_payload["approved_order_count"],
                "rejected_orders": run_payload["rejected_order_count"],
                "submitted_orders": submitted,
                "filled_orders": filled,
                "failed_orders": failed,
                "estimated_turnover": plan_result.get("summary", {}).get("estimated_turnover", 0.0),
                "actual_turnover": actual_turnover,
                "pre_trade_cash": pre_cash,
                "post_trade_cash": post_cash,
                "pre_trade_portfolio_value": pre_portfolio_value,
                "post_trade_portfolio_value": pre_portfolio_value,
                "expected_post_trade_cash": round(expected_cash, 6),
                "cash_difference": round(post_cash - expected_cash, 6),
                "expected_post_trade_buying_power": round(expected_buying_power, 6),
                "actual_post_trade_buying_power": round(post_cash, 6),
                "buying_power_difference": round(post_cash - expected_buying_power, 6),
                "maximum_target_weight": max(target_weights.values()) if target_weights else 0.0,
                "largest_sector_exposure": target_snapshot.get("largest_sector_weight"),
                "reconciliation_status": reconciliation.get("reconciliation_status"),
                "total_duration": performance.get("total_wall_clock_duration"),
            },
            "performance": performance,
        }
        logger.info("PAPER_VALIDATION_COMPLETED run_id=%s mode=%s dry_run=%s status=%s", run_id, selected_mode, dry_run, status)
        return result
    finally:
        repo.close()


def create_approval(
    database_url: str | None,
    approval_id: str,
    strategy_id: str,
    strategy_version: str,
    strategy_fingerprint: str,
    portfolio_configuration: dict[str, Any],
    risk_configuration: dict[str, Any],
    benchmark: str,
    horizon: int,
    approved_by: str,
    approved_at: str,
    expires_at: str,
    enabled: bool,
    notes: str,
) -> dict[str, Any]:
    approval = build_approval_record(
        approval_id=approval_id,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        strategy_fingerprint=strategy_fingerprint,
        portfolio_configuration=portfolio_configuration,
        risk_configuration=risk_configuration,
        benchmark=benchmark,
        horizon=horizon,
        approved_by=approved_by,
        approved_at=approved_at,
        expires_at=expires_at,
        enabled=enabled,
        notes=notes,
    )
    repo = MonitoringPaperExecutionRepository(database_url=database_url)
    try:
        saved = repo.create_approval(approval)
        return {"approval": approval, "saved": saved}
    finally:
        repo.close()


def list_approvals(database_url: str | None, enabled_only: bool = False) -> list[dict[str, Any]]:
    repo = MonitoringPaperExecutionRepository(database_url=database_url)
    try:
        return repo.list_approvals(enabled_only=enabled_only)
    finally:
        repo.close()


def disable_approval(database_url: str | None, approval_id: str) -> dict[str, Any]:
    repo = MonitoringPaperExecutionRepository(database_url=database_url)
    try:
        return repo.disable_approval(approval_id)
    finally:
        repo.close()


def validate_approval_cli(database_url: str | None, approval_id: str, mode: str) -> dict[str, Any]:
    repo = MonitoringPaperExecutionRepository(database_url=database_url)
    try:
        approval = repo.fetch_approval(approval_id)
        if not approval:
            return {"valid": False, "reasons": ["approval missing"], "fingerprint_status": "missing"}
        result = validate_approval(
            approval=approval,
            expected_strategy_id=str(approval.get("strategy_id") or ""),
            expected_strategy_version=str(approval.get("strategy_version") or ""),
            expected_strategy_fingerprint=str(approval.get("strategy_fingerprint") or ""),
            expected_portfolio_configuration=dict(approval.get("portfolio_configuration") or {}),
            expected_risk_configuration=dict(approval.get("risk_configuration") or {}),
            mode=mode,
            broker_type="paper" if str(mode).upper() == "PAPER" else "simulation",
        )
        return {"valid": result.valid, "reasons": result.reasons, "fingerprint_status": result.fingerprint_status}
    finally:
        repo.close()


def show_run(database_url: str | None, run_id: str) -> dict[str, Any]:
    repo = MonitoringPaperExecutionRepository(database_url=database_url)
    try:
        run = repo.fetch_run(run_id) or {}
        orders = repo.fetch_orders_for_run(run_id)
        snapshots = repo.fetch_position_snapshots_for_run(run_id)
        return {"run": run, "orders": orders, "snapshots": snapshots}
    finally:
        repo.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Paper validation runner (manual approval required)")
    parser.add_argument("--database-url", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    create_cmd = sub.add_parser("create-approval")
    create_cmd.add_argument("--approval-id", required=True)
    create_cmd.add_argument("--strategy-id", required=True)
    create_cmd.add_argument("--strategy-version", required=True)
    create_cmd.add_argument("--strategy-fingerprint", required=True)
    create_cmd.add_argument("--portfolio-config-json", required=True)
    create_cmd.add_argument("--risk-config-json", required=True)
    create_cmd.add_argument("--benchmark", default=BENCHMARK_SYMBOL)
    create_cmd.add_argument("--horizon", type=int, default=20)
    create_cmd.add_argument("--approved-by", required=True)
    create_cmd.add_argument("--approved-at", required=True)
    create_cmd.add_argument("--expires-at", required=True)
    create_cmd.add_argument("--enabled", action="store_true")
    create_cmd.add_argument("--notes", default="")

    list_cmd = sub.add_parser("list-approvals")
    list_cmd.add_argument("--enabled-only", action="store_true")

    disable_cmd = sub.add_parser("disable-approval")
    disable_cmd.add_argument("--approval-id", required=True)

    validate_cmd = sub.add_parser("validate-approval")
    validate_cmd.add_argument("--approval-id", required=True)
    validate_cmd.add_argument("--mode", default=PAPER_VALIDATION_DEFAULT_MODE)

    plan_cmd = sub.add_parser("plan")
    plan_cmd.add_argument("--approval-id", required=True)
    plan_cmd.add_argument("--mode", default=PAPER_VALIDATION_DEFAULT_MODE)

    dry_cmd = sub.add_parser("dry-run")
    dry_cmd.add_argument("--approval-id", required=True)
    dry_cmd.add_argument("--mode", default=PAPER_VALIDATION_DEFAULT_MODE)

    execute_cmd = sub.add_parser("execute-paper")
    execute_cmd.add_argument("--approval-id", required=True)
    execute_cmd.add_argument("--mode", default=PAPER_VALIDATION_DEFAULT_MODE)
    execute_cmd.add_argument("--confirm", action="store_true")

    reconcile_cmd = sub.add_parser("reconcile")
    reconcile_cmd.add_argument("--run-id", required=True)

    show_cmd = sub.add_parser("show-run")
    show_cmd.add_argument("--run-id", required=True)

    args = parser.parse_args()

    if args.command == "create-approval":
        result = create_approval(
            database_url=args.database_url,
            approval_id=args.approval_id,
            strategy_id=args.strategy_id,
            strategy_version=args.strategy_version,
            strategy_fingerprint=args.strategy_fingerprint,
            portfolio_configuration=json.loads(args.portfolio_config_json),
            risk_configuration=json.loads(args.risk_config_json),
            benchmark=args.benchmark,
            horizon=args.horizon,
            approved_by=args.approved_by,
            approved_at=args.approved_at,
            expires_at=args.expires_at,
            enabled=bool(args.enabled),
            notes=args.notes,
        )
    elif args.command == "list-approvals":
        result = list_approvals(args.database_url, enabled_only=bool(args.enabled_only))
    elif args.command == "disable-approval":
        result = disable_approval(args.database_url, args.approval_id)
    elif args.command == "validate-approval":
        result = validate_approval_cli(args.database_url, args.approval_id, args.mode)
    elif args.command == "plan":
        result = run_paper_validation(database_url=args.database_url, approval_id=args.approval_id, mode=args.mode, dry_run=True, execute=False, confirm=False)
    elif args.command == "dry-run":
        result = run_paper_validation(database_url=args.database_url, approval_id=args.approval_id, mode=args.mode, dry_run=True, execute=False, confirm=False)
    elif args.command == "execute-paper":
        result = run_paper_validation(database_url=args.database_url, approval_id=args.approval_id, mode=args.mode, dry_run=False, execute=True, confirm=bool(args.confirm))
    elif args.command == "reconcile":
        details = show_run(args.database_url, args.run_id)
        run = details.get("run") or {}
        snapshots = details.get("snapshots") or []
        result = {
            "run_id": run.get("run_id"),
            "status": run.get("status"),
            "reconciliation_status": (snapshots[-1] if snapshots else {}).get("reconciliation_status"),
        }
    else:
        result = show_run(args.database_url, args.run_id)

    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
