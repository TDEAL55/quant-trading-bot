from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from config import (
    BENCHMARK_SYMBOL,
    DAILY_LOSS_LIMIT,
    MAX_DAILY_LOSS,
    MAX_POSITION_SIZE,
    PAPER_VALIDATION_ALLOW_FRACTIONAL,
    PAPER_VALIDATION_CASH_BUFFER,
    PAPER_VALIDATION_DUPLICATE_RUN_PROTECTION,
    PAPER_VALIDATION_MAX_ORDER_NOTIONAL,
    PAPER_VALIDATION_MAX_ORDERS,
    PAPER_VALIDATION_MIN_ORDER_NOTIONAL,
    PAPER_VALIDATION_QUANTITY_PRECISION,
    PAPER_VALIDATION_REBALANCE_TOLERANCE,
    PAPER_VALIDATION_RECONCILIATION_TOLERANCE,
)
from deployment_config import load_deployment_config
from paper_broker import create_paper_broker
from paper_execution_repository import MonitoringPaperExecutionRepository, PaperValidationRunPayload
from paper_order_planner import OrderPlannerSettings, plan_paper_orders
from paper_reconciliation import reconcile_paper_positions
from risk_manager import RiskManager
from scanner_repository import save_scan_results
from scanner_runner import SAMPLE_SYMBOLS, _load_paper_positions, _symbol_records_from_list, run_scan, run_shortlist_only
from sprint_10_2_execution_validation import _execution_fingerprint
from stock_universe import load_stock_universe


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso() -> str:
    return _utc_now().isoformat()


def _coerce_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "items"):
        return dict(value)
    return {}


def _positions_list(raw_positions: Any) -> list[dict[str, Any]]:
    if isinstance(raw_positions, list):
        return [dict(item or {}) for item in raw_positions]
    if isinstance(raw_positions, dict):
        rows: list[dict[str, Any]] = []
        for symbol, payload in raw_positions.items():
            row = dict(payload or {}) if isinstance(payload, dict) else {}
            row.setdefault("symbol", symbol)
            rows.append(row)
        return rows
    return []


def _latest_price_for_symbol(scan_payload: dict[str, Any], symbol: str) -> float:
    for row in list(scan_payload.get("ranked_candidates") or []) + list(scan_payload.get("scan_results") or []):
        if str(row.get("symbol") or "").upper() != str(symbol).upper():
            continue
        try:
            price = float(row.get("latest_price") or 0.0)
            if price > 0:
                return price
        except (TypeError, ValueError):
            continue
    return 0.0


def _scan_run_payload(run_id: str, scan_payload: dict[str, Any], universe_count: int, completed_at: str) -> dict[str, Any]:
    summary = dict(scan_payload.get("summary") or {})
    return {
        "run_id": run_id,
        "started_at": summary.get("started_at") or completed_at,
        "completed_at": completed_at,
        "universe_name": summary.get("universe_name") or ("sample" if universe_count and universe_count <= len(SAMPLE_SYMBOLS) else "configured"),
        "symbol_count": int(summary.get("symbol_count") or universe_count),
        "success_count": int(summary.get("success_count") or 0),
        "rejection_count": int(summary.get("rejection_count") or 0),
        "error_count": int(summary.get("error_count") or 0),
        "eligible_count": int(summary.get("eligible_count") or len(scan_payload.get("ranked_candidates") or [])),
        "status": str(summary.get("status") or "completed"),
        "duration_seconds": float(summary.get("duration_seconds") or 0.0),
    }


@dataclass(frozen=True)
class ContinuousScanCycleResult:
    run_id: str
    started_at: str
    completed_at: str
    status: str
    execution_status: str
    confirmed_order_count: int
    scan: dict[str, Any] = field(default_factory=dict)
    selection: dict[str, Any] = field(default_factory=dict)
    execution: dict[str, Any] = field(default_factory=dict)
    persistence: dict[str, Any] = field(default_factory=dict)
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_continuous_scan_cycle(
    database_url: str | None = None,
    config_loader: Callable[[], Any] = load_deployment_config,
    now_provider: Callable[[], datetime] = _utc_now,
    broker_factory: Callable[..., Any] = create_paper_broker,
    scan_runner: Callable[[list[dict[str, Any]]], dict[str, Any]] = run_scan,
    shortlist_runner: Callable[..., dict[str, Any]] = run_shortlist_only,
    scan_persistor: Callable[..., dict[str, Any]] = save_scan_results,
    execution_repo_factory: Callable[..., MonitoringPaperExecutionRepository] = MonitoringPaperExecutionRepository,
    positions_loader: Callable[[], tuple[list[dict[str, Any]], float, float]] = _load_paper_positions,
    universe_loader: Callable[[], list[dict[str, Any]]] = load_stock_universe,
    symbol_records_builder: Callable[[list[str]], list[dict[str, Any]]] = _symbol_records_from_list,
    sample_symbols: list[str] | None = None,
    symbols: list[str] | None = None,
    persist: bool = True,
) -> ContinuousScanCycleResult:
    config = config_loader()
    if str(config.trading_mode).upper() != "PAPER":
        raise RuntimeError("continuous scan cycle requires TRADING_MODE=PAPER")

    started_dt = _coerce_utc(now_provider())
    started_at = started_dt.isoformat()
    run_stamp = started_dt.strftime("%Y%m%d%H%M%S%f")
    cycle_run_id = f"continuous-scan-{run_stamp}"

    if symbols:
        universe_records = symbol_records_builder([str(symbol).upper() for symbol in symbols if str(symbol).strip()])
    elif sample_symbols:
        universe_records = symbol_records_builder([str(symbol).upper() for symbol in sample_symbols if str(symbol).strip()])
    else:
        universe_records = list(universe_loader())

    scan_payload = dict(scan_runner(universe_records) or {})
    positions, current_cash, portfolio_value = positions_loader()
    shortlist_payload = dict(shortlist_runner(scan_payload, positions, current_cash, portfolio_value) or {})
    selected_candidates = list(shortlist_payload.get("selected") or [])
    selected_candidate = dict(selected_candidates[0]) if selected_candidates else {}
    completed_at = _utc_iso()
    scan_run_payload = _scan_run_payload(cycle_run_id, scan_payload, len(universe_records), completed_at)

    if persist:
        scan_persistor(
            run_payload=scan_run_payload,
            scan_results=list(scan_payload.get("scan_results") or []),
            candidates=selected_candidates,
            position_reviews=[],
            database_url=database_url or getattr(config, "database_url", None),
        )

    scan_result = {"scan_payload": scan_payload, "scan_run": scan_run_payload}
    if not selected_candidate:
        return ContinuousScanCycleResult(
            run_id=cycle_run_id,
            started_at=started_at,
            completed_at=completed_at,
            status="no_candidates",
            execution_status="no_candidates",
            confirmed_order_count=0,
            scan=scan_result,
            selection=shortlist_payload,
            execution={"selected": None, "paper_order": {}, "risk_result": {}, "reconciliation": {}},
            persistence={"scan": {"status": "saved" if persist else "skipped"}, "execution": {"status": "skipped"}},
            duration_seconds=max((_utc_now() - started_dt).total_seconds(), 0.0),
        )

    selected_symbol = str(selected_candidate.get("symbol") or "").upper()
    latest_price = _latest_price_for_symbol(scan_payload, selected_symbol)
    if latest_price <= 0:
        return ContinuousScanCycleResult(
            run_id=cycle_run_id,
            started_at=started_at,
            completed_at=completed_at,
            status="no_trade",
            execution_status="no_trade",
            confirmed_order_count=0,
            scan=scan_result,
            selection=shortlist_payload,
            execution={"selected": selected_candidate, "paper_order": {}, "risk_result": {}, "reconciliation": {}},
            persistence={"scan": {"status": "saved" if persist else "skipped"}, "execution": {"status": "skipped"}},
            duration_seconds=max((_utc_now() - started_dt).total_seconds(), 0.0),
        )

    target_notional = float(selected_candidate.get("suggested_paper_notional") or 0.0)
    if target_notional <= 0 and portfolio_value > 0:
        target_notional = min(float(current_cash), float(portfolio_value) * 0.1)
    if target_notional <= 0:
        return ContinuousScanCycleResult(
            run_id=cycle_run_id,
            started_at=started_at,
            completed_at=completed_at,
            status="no_trade",
            execution_status="no_trade",
            confirmed_order_count=0,
            scan=scan_result,
            selection=shortlist_payload,
            execution={"selected": selected_candidate, "paper_order": {}, "risk_result": {}, "reconciliation": {}},
            persistence={"scan": {"status": "saved" if persist else "skipped"}, "execution": {"status": "skipped"}},
            duration_seconds=max((_utc_now() - started_dt).total_seconds(), 0.0),
        )

    planner_settings = OrderPlannerSettings(
        minimum_order_notional=float(PAPER_VALIDATION_MIN_ORDER_NOTIONAL),
        maximum_order_notional=float(PAPER_VALIDATION_MAX_ORDER_NOTIONAL),
        allow_fractional=bool(PAPER_VALIDATION_ALLOW_FRACTIONAL),
        quantity_precision=int(PAPER_VALIDATION_QUANTITY_PRECISION),
        rebalance_tolerance=float(PAPER_VALIDATION_REBALANCE_TOLERANCE),
        maximum_orders=1,
        cash_buffer=float(PAPER_VALIDATION_CASH_BUFFER),
    )
    target_weight = min(target_notional / max(float(portfolio_value), 1.0), 1.0)
    planned_orders = plan_paper_orders(
        target_weights={selected_symbol: target_weight},
        current_positions={},
        reference_prices={selected_symbol: latest_price},
        portfolio_value=max(float(portfolio_value), 1.0),
        current_cash=float(current_cash),
        settings=planner_settings,
    )
    planned_order = dict((planned_orders.get("orders") or [{}])[0]) if planned_orders.get("orders") else {}
    if not planned_order:
        return ContinuousScanCycleResult(
            run_id=cycle_run_id,
            started_at=started_at,
            completed_at=completed_at,
            status="no_trade",
            execution_status="no_trade",
            confirmed_order_count=0,
            scan=scan_result,
            selection=shortlist_payload,
            execution={"selected": selected_candidate, "paper_order": {}, "risk_result": {}, "reconciliation": {}},
            persistence={"scan": {"status": "saved" if persist else "skipped"}, "execution": {"status": "skipped"}},
            duration_seconds=max((_utc_now() - started_dt).total_seconds(), 0.0),
        )

    risk = RiskManager(max_position_size=float(MAX_POSITION_SIZE), max_daily_loss=float(MAX_DAILY_LOSS), daily_loss_limit=float(DAILY_LOSS_LIMIT))
    trade_value = float(planned_order.get("notional") or 0.0)
    existing_position_symbols = {str(position.get("symbol") or "").upper() for position in positions if str(position.get("symbol") or "").strip()}
    risk_checks = {
        "position_size": bool(trade_value <= max(float(portfolio_value), 1.0) * float(MAX_POSITION_SIZE)),
        "cash": bool(float(current_cash) >= trade_value),
        "buying_power": bool(float(current_cash) >= trade_value),
        "daily_loss": bool(risk.daily_loss < float(DAILY_LOSS_LIMIT)),
        "existing_position": selected_symbol not in existing_position_symbols,
        "duplicate_protection": True,
    }

    if not risk.approve_trade(max(float(portfolio_value), 1.0), trade_value, current_loss=0.0) or not all(risk_checks.values()):
        return ContinuousScanCycleResult(
            run_id=cycle_run_id,
            started_at=started_at,
            completed_at=completed_at,
            status="risk_rejected",
            execution_status="risk_rejected",
            confirmed_order_count=0,
            scan=scan_result,
            selection=shortlist_payload,
            execution={
                "selected": selected_candidate,
                "paper_order": {"symbol": selected_symbol, "side": "BUY", "quantity": planned_order.get("quantity"), "notional": trade_value, "submission_status": "rejected"},
                "risk_result": {"approved": False, "checks": risk_checks},
                "reconciliation": {},
            },
            persistence={"scan": {"status": "saved" if persist else "skipped"}, "execution": {"status": "skipped"}},
            duration_seconds=max((_utc_now() - started_dt).total_seconds(), 0.0),
        )

    execution_repo = execution_repo_factory(database_url=database_url or getattr(config, "database_url", None))
    try:
        execution_fingerprint = _execution_fingerprint(selected_symbol, float(planned_order.get("quantity") or 0.0), "PAPER")
        if PAPER_VALIDATION_DUPLICATE_RUN_PROTECTION:
            existing = execution_repo.fetch_latest_submitting_run_by_execution_fingerprint(execution_fingerprint)
            if existing:
                return ContinuousScanCycleResult(
                    run_id=cycle_run_id,
                    started_at=started_at,
                    completed_at=completed_at,
                    status="duplicate_rejected",
                    execution_status="duplicate_rejected",
                    confirmed_order_count=0,
                    scan=scan_result,
                    selection=shortlist_payload,
                    execution={
                        "selected": selected_candidate,
                        "paper_order": {"symbol": selected_symbol, "side": "BUY", "quantity": planned_order.get("quantity"), "notional": trade_value, "submission_status": "duplicate_rejected"},
                        "risk_result": {"approved": False, "checks": {**risk_checks, "duplicate_protection": False}, "duplicate_run": existing},
                        "reconciliation": {},
                    },
                    persistence={"scan": {"status": "saved" if persist else "skipped"}, "execution": {"status": "skipped"}},
                    duration_seconds=max((_utc_now() - started_dt).total_seconds(), 0.0),
                )

        broker = broker_factory(mode="PAPER")
        broker_pre_positions = _as_dict(broker.get_positions())
        broker_pre_cash = float(broker.get_buying_power() or 0.0)
        order_response = _as_dict(broker.submit_order(side=str(planned_order.get("side") or "buy").lower(), ticker=selected_symbol, quantity=float(planned_order.get("quantity") or 0.0)))
        paper_order_id = str(order_response.get("order_id") or order_response.get("id") or f"{cycle_run_id}-1")
        filled_qty = float(order_response.get("filled_quantity") or planned_order.get("quantity") or 0.0)
        fill_price = float(order_response.get("average_fill_price") or latest_price or 0.0)
        broker_post_positions = _as_dict(broker.get_positions())
        broker_post_cash = float(broker.get_buying_power() or broker_pre_cash)

        expected_positions = dict(broker_pre_positions)
        expected_positions[selected_symbol] = {
            "quantity": float((expected_positions.get(selected_symbol) or {}).get("quantity") or 0.0) + filled_qty,
            "avg_price": fill_price,
        }
        planned_positions = {
            symbol: {"quantity": float((payload or {}).get("quantity") or 0.0), "weight": round(float((payload or {}).get("quantity") or 0.0) * float((payload or {}).get("avg_price") or 0.0) / max(float(portfolio_value), 1.0), 10)}
            for symbol, payload in expected_positions.items()
        }
        actual_positions = {
            symbol: {"quantity": float((payload or {}).get("quantity") or 0.0), "weight": round(float((payload or {}).get("quantity") or 0.0) * float((payload or {}).get("avg_price") or 0.0) / max(float(portfolio_value), 1.0), 10)}
            for symbol, payload in broker_post_positions.items()
        }
        reconciliation = reconcile_paper_positions(
            planned_positions=planned_positions,
            actual_positions=actual_positions,
            expected_cash=round(broker_pre_cash - (filled_qty * fill_price), 6),
            actual_cash=broker_post_cash,
            expected_buying_power=round(broker_pre_cash - (filled_qty * fill_price), 6),
            actual_buying_power=broker_post_cash,
            orders=[
                {
                    "submission_status": str(order_response.get("status") or "submitted"),
                    "filled_quantity": filled_qty,
                    "quantity": float(planned_order.get("quantity") or 0.0),
                    "average_fill_price": fill_price,
                }
            ],
            tolerance=float(PAPER_VALIDATION_RECONCILIATION_TOLERANCE),
        )

        validation_run_id = f"continuous-scan-{run_stamp}-exec"
        if persist:
            execution_repo.save_validation_run(
                PaperValidationRunPayload(
                    run={
                        "run_id": validation_run_id,
                        "run_fingerprint": execution_fingerprint,
                        "execution_fingerprint": execution_fingerprint,
                        "approval_id": f"continuous-scan-{selected_symbol}-{started_dt.date().isoformat()}",
                        "strategy_id": "continuous_scan_cycle",
                        "strategy_version": "v1",
                        "strategy_fingerprint": execution_fingerprint,
                        "research_run_id": cycle_run_id,
                        "scanner_timestamp": completed_at,
                        "started_at": started_at,
                        "completed_at": _utc_iso(),
                        "mode": "PAPER",
                        "status": "completed" if str(reconciliation.get("reconciliation_status") or "").lower() == "matched" else "failed",
                        "dry_run": False,
                        "proposed_order_count": 1,
                        "approved_order_count": 1,
                        "rejected_order_count": 0,
                        "submitted_order_count": 1,
                        "filled_order_count": 1 if filled_qty > 0 else 0,
                        "failed_order_count": 0,
                        "configuration": {
                            "portfolio": {"maximum_orders": 1},
                            "risk": {"max_position_size": float(MAX_POSITION_SIZE), "max_daily_loss": float(MAX_DAILY_LOSS), "daily_loss_limit": float(DAILY_LOSS_LIMIT)},
                        },
                        "risk_snapshot": {"max_position_size": float(MAX_POSITION_SIZE), "max_daily_loss": float(MAX_DAILY_LOSS), "daily_loss_limit": float(DAILY_LOSS_LIMIT), "rejection_reasons": {}},
                        "performance": {},
                        "warnings": list(planned_orders.get("holds") or []),
                        "error_message": None,
                        "created_at": started_at,
                        "updated_at": _utc_iso(),
                    },
                    orders=[
                        {
                            "paper_order_id": f"{validation_run_id}-0001",
                            "symbol": selected_symbol,
                            "side": "BUY",
                            "quantity": float(planned_order.get("quantity") or 0.0),
                            "notional": float(planned_order.get("notional") or 0.0),
                            "target_weight": float(target_weight),
                            "current_weight": 0.0,
                            "weight_delta": float(target_weight),
                            "reference_price": latest_price,
                            "proposed_at": started_at,
                            "risk_status": "approved",
                            "risk_reason": "approved",
                            "submission_status": str(order_response.get("status") or "submitted"),
                            "broker_order_id": paper_order_id,
                            "submitted_at": _utc_iso(),
                            "filled_quantity": filled_qty,
                            "average_fill_price": fill_price,
                            "filled_at": _utc_iso() if filled_qty > 0 else None,
                            "error_message": None,
                            "order_payload": {"source": "continuous_scan_cycle"},
                            "created_at": started_at,
                            "updated_at": _utc_iso(),
                        }
                    ],
                    position_snapshots=[
                        {
                            "snapshot_id": f"{validation_run_id}-post",
                            "captured_at": _utc_iso(),
                            "positions": broker_post_positions,
                            "cash": broker_post_cash,
                            "buying_power": broker_post_cash,
                            "portfolio_value": round(broker_post_cash + sum(float((payload or {}).get("quantity") or 0.0) * float((payload or {}).get("avg_price") or 0.0) for payload in broker_post_positions.values()), 6),
                            "gross_exposure": 1.0,
                            "net_exposure": 1.0,
                            "concentration": {},
                            "reconciliation_status": reconciliation.get("reconciliation_status"),
                            "warnings": reconciliation.get("warnings") or [],
                        }
                    ],
                )
            )

        confirmed = 1 if str(reconciliation.get("reconciliation_status") or "").lower() in {"matched", "matched_with_tolerance"} and int(reconciliation.get("position_mismatch_count") or 0) == 0 else 0
        return ContinuousScanCycleResult(
            run_id=cycle_run_id,
            started_at=started_at,
            completed_at=completed_at,
            status="completed",
            execution_status="completed",
            confirmed_order_count=confirmed,
            scan=scan_result,
            selection=shortlist_payload,
            execution={
                "selected": selected_candidate,
                "paper_order": {
                    "order_id": paper_order_id,
                    "symbol": selected_symbol,
                    "shares": filled_qty,
                    "fill_price": fill_price,
                    "timestamp": _utc_iso(),
                    "submission_status": str(order_response.get("status") or "submitted"),
                },
                "risk_result": {"approved": True, "checks": risk_checks},
                "reconciliation": reconciliation,
                "execution_fingerprint": execution_fingerprint,
                "validation_run_id": validation_run_id,
            },
            persistence={"scan": {"status": "saved" if persist else "skipped"}, "execution": {"status": "saved" if persist else "skipped", "run_id": validation_run_id if persist else None}},
            duration_seconds=max((_utc_now() - started_dt).total_seconds(), 0.0),
        )
    finally:
        execution_repo.close()
