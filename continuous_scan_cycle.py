from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import inspect
import os
from typing import Any, Callable

from config import (
    BENCHMARK_SYMBOL,
    DAILY_LOSS_LIMIT,
    MAX_DAILY_ORDERS,
    MAX_OPEN_POSITIONS,
    MAX_POSITION_EQUITY_PERCENT,
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
from order_lifecycle import track_order_lifecycle
from paper_broker import create_paper_broker
from paper_execution_repository import MonitoringPaperExecutionRepository, PaperValidationRunPayload
from paper_order_planner import OrderPlannerSettings, plan_paper_orders
from paper_reconciliation import reconcile_paper_positions
from risk_manager import RiskManager
from scanner_repository import save_scan_results
from scanner_runner import SAMPLE_SYMBOLS, _load_paper_positions, _symbol_records_from_list, run_scan, run_shortlist_only
from sprint_10_2_execution_validation import _execution_fingerprint
from stock_universe import load_stock_universe
from strategy_profitability import allocate_equal_risk, build_strategy_leaderboard, paused_strategies_from_drawdown
from strategies import evaluate_all_strategies


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


def _emit_telemetry(telemetry_callback: Callable[[str, dict[str, Any]], None] | None, event: str, **fields: Any) -> None:
    if telemetry_callback is None:
        return
    try:
        telemetry_callback(event, dict(fields))
    except Exception:
        return


def _invoke_scan_runner(
    scan_runner: Callable[..., dict[str, Any]],
    universe_records: list[dict[str, Any]],
    **kwargs: Any,
) -> dict[str, Any]:
    signature = inspect.signature(scan_runner)
    params = signature.parameters
    accepts_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values())
    if accepts_kwargs:
        return dict(scan_runner(universe_records, **kwargs) or {})

    accepted: dict[str, Any] = {}
    for key, value in kwargs.items():
        if key in params:
            accepted[key] = value
    return dict(scan_runner(universe_records, **accepted) or {})


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


def _position_count(positions: dict[str, dict[str, float]]) -> int:
    count = 0
    for payload in (positions or {}).values():
        try:
            qty = float((payload or {}).get("quantity") or 0.0)
        except (TypeError, ValueError):
            qty = 0.0
        if qty > 0:
            count += 1
    return count


def _has_open_entry_order(open_orders: list[dict[str, Any]], symbol: str) -> bool:
    for row in open_orders or []:
        if str(row.get("symbol") or "").upper() != str(symbol).upper():
            continue
        side = str(row.get("side") or "").lower()
        status = str(row.get("status") or "").lower()
        if side == "buy" and status not in {"filled", "canceled", "cancelled", "rejected", "expired", "done_for_day"}:
            return True
    return False


def _effective_max_open_positions(config: Any) -> int:
    configured = int(getattr(config, "max_open_positions", 0) or 0)
    if configured > 0:
        return configured
    fallback = int(MAX_OPEN_POSITIONS or 0)
    return fallback if fallback > 0 else 10


def _effective_max_position_equity_percent(config: Any) -> float:
    configured = float(getattr(config, "max_position_equity_percent", 0.0) or 0.0)
    if configured > 0:
        return configured
    fallback = float(MAX_POSITION_EQUITY_PERCENT or 0.0)
    return fallback if fallback > 0 else 10.0


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
    dry_run: bool = False,
    diagnostic_symbol_limit: int | None = None,
    telemetry_callback: Callable[[str, dict[str, Any]], None] | None = None,
) -> ContinuousScanCycleResult:
    config = config_loader()
    if str(config.trading_mode).upper() != "PAPER":
        raise RuntimeError("continuous scan cycle requires TRADING_MODE=PAPER")

    started_dt = _coerce_utc(now_provider())
    started_at = started_dt.isoformat()
    run_stamp = started_dt.strftime("%Y%m%d%H%M%S%f")
    cycle_run_id = f"continuous-scan-{run_stamp}"

    def _result(
        *,
        status: str,
        execution_status: str,
        confirmed_order_count: int,
        scan: dict[str, Any],
        selection: dict[str, Any],
        execution: dict[str, Any],
        persistence_payload: dict[str, Any],
    ) -> ContinuousScanCycleResult:
        result_payload = ContinuousScanCycleResult(
            run_id=cycle_run_id,
            started_at=started_at,
            completed_at=_utc_iso(),
            status=status,
            execution_status=execution_status,
            confirmed_order_count=confirmed_order_count,
            scan=scan,
            selection=selection,
            execution=execution,
            persistence=persistence_payload,
            duration_seconds=max((_utc_now() - started_dt).total_seconds(), 0.0),
        )
        _emit_telemetry(
            telemetry_callback,
            "scan_cycle_complete",
            run_id=cycle_run_id,
            status=status,
            execution_status=execution_status,
            confirmed_order_count=confirmed_order_count,
            failed_symbol_count=int(((scan or {}).get("scan_payload") or {}).get("summary", {}).get("failed_symbol_count") or 0),
            elapsed_seconds=round(float(result_payload.duration_seconds), 4),
        )
        return result_payload

    _emit_telemetry(
        telemetry_callback,
        "paper_connection_check_start",
        run_id=cycle_run_id,
        dry_run=bool(dry_run),
    )

    if symbols:
        universe_records = symbol_records_builder([str(symbol).upper() for symbol in symbols if str(symbol).strip()])
        full_universe_count = len(universe_records)
    elif sample_symbols:
        universe_records = symbol_records_builder([str(symbol).upper() for symbol in sample_symbols if str(symbol).strip()])
        full_universe_count = len(universe_records)
    else:
        _emit_telemetry(
            telemetry_callback,
            "universe_fetch_start",
            run_id=cycle_run_id,
            source="alpaca_assets_api",
        )
        full_universe_records = list(universe_loader())
        full_universe_count = len(full_universe_records)
        if diagnostic_symbol_limit is not None and int(diagnostic_symbol_limit) > 0:
            ordered = sorted(full_universe_records, key=lambda item: str(item.get("symbol") or ""))
            universe_records = ordered[: int(diagnostic_symbol_limit)]
        else:
            universe_records = full_universe_records
        _emit_telemetry(
            telemetry_callback,
            "universe_fetch_complete",
            run_id=cycle_run_id,
            source="alpaca_assets_api",
            total_assets_retrieved=int(full_universe_count),
            selected_for_scan=int(len(universe_records)),
            diagnostic_symbol_limit=(int(diagnostic_symbol_limit) if diagnostic_symbol_limit is not None else None),
        )

    broker = broker_factory(mode="PAPER")
    scan_payload: dict[str, Any] = {}

    broker_positions = {}
    broker_open_orders: list[dict[str, Any]] = []
    broker_cash = 0.0
    broker_buying_power = 0.0
    broker_equity = 0.0
    account: dict[str, Any] = {}
    try:
        broker_positions = _as_dict(broker.get_positions())
    except Exception:
        broker_positions = {}

    try:
        account = _as_dict(getattr(broker, "get_account", lambda: {})())
        broker_cash = float(account.get("cash") or 0.0)
        broker_buying_power = float(account.get("buying_power") or 0.0)
        broker_equity = float(account.get("equity") or account.get("portfolio_value") or 0.0)
        if broker_buying_power <= 0:
            broker_buying_power = float(getattr(broker, "get_buying_power", lambda: 0.0)() or 0.0)
        if broker_cash <= 0:
            broker_cash = broker_buying_power
        if broker_equity <= 0:
            broker_equity = float(getattr(broker, "get_equity", lambda: broker_buying_power)() or broker_buying_power)
    except Exception:
        broker_cash = float(getattr(broker, "get_cash", lambda: 0.0)() or 0.0)
        broker_buying_power = float(getattr(broker, "get_buying_power", lambda: broker_cash)() or broker_cash)
        broker_equity = float(getattr(broker, "get_equity", lambda: broker_buying_power)() or broker_buying_power)

    _emit_telemetry(
        telemetry_callback,
        "paper_connection_check_complete",
        run_id=cycle_run_id,
        account_status=str(account.get("status") or "UNKNOWN") if isinstance(account, dict) else "UNKNOWN",
        account_blocked=bool(account.get("account_blocked", False)) if isinstance(account, dict) else False,
        paper_endpoint=str(getattr(config, "alpaca_paper_base_url", "")),
    )

    try:
        broker_open_orders = list(getattr(broker, "get_open_orders", lambda: [])() or [])
    except Exception:
        broker_open_orders = []

    if not broker_positions and broker_equity <= 0:
        # Preserve offline testability fallback when broker stubs expose only loader fixtures.
        loaded_positions, loaded_cash, loaded_equity = positions_loader()
        broker_positions = {str(item.get("symbol") or "").upper(): {"quantity": float(item.get("quantity") or 0.0), "avg_price": float(item.get("entry_price") or item.get("avg_price") or 0.0)} for item in loaded_positions}
        broker_cash = float(loaded_cash)
        broker_buying_power = float(loaded_cash)
        broker_equity = float(loaded_equity)

    def _scan_progress_telemetry(payload: dict[str, Any]) -> None:
        event_name = str(payload.get("event") or "scan_progress")
        base = {
            "run_id": cycle_run_id,
            "dry_run": bool(dry_run),
            "trading_mode": str(config.trading_mode),
        }
        base.update(payload)
        _emit_telemetry(telemetry_callback, event_name, **base)

    scan_payload = _invoke_scan_runner(
        scan_runner,
        universe_records,
        progress_callback=_scan_progress_telemetry,
    )
    summary = dict(scan_payload.get("summary") or {})
    summary["full_universe_count"] = int(full_universe_count)
    summary["diagnostic_symbol_limit"] = int(diagnostic_symbol_limit) if diagnostic_symbol_limit is not None else None
    summary["diagnostic_mode"] = bool(diagnostic_symbol_limit is not None and int(diagnostic_symbol_limit) > 0)
    scan_payload["summary"] = summary

    shortlist_positions = _positions_list(broker_positions)
    shortlist_payload = dict(shortlist_runner(scan_payload, shortlist_positions, broker_cash, broker_equity) or {})
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
        if dry_run:
            _emit_telemetry(
                telemetry_callback,
                "dry_run_execution_skipped",
                run_id=cycle_run_id,
                reason="no_candidates",
                orders_attempted=0,
                orders_submitted=0,
            )
        return _result(
            status="no_candidates",
            execution_status="no_candidates",
            confirmed_order_count=0,
            scan=scan_result,
            selection=shortlist_payload,
            execution={"selected": None, "paper_order": {}, "risk_result": {}, "reconciliation": {}},
            persistence_payload={"scan": {"status": "saved" if persist else "skipped"}, "execution": {"status": "skipped"}},
        )

    selected_symbol = str(selected_candidate.get("symbol") or "").upper()
    latest_price = _latest_price_for_symbol(scan_payload, selected_symbol)
    if latest_price <= 0:
        if dry_run:
            _emit_telemetry(
                telemetry_callback,
                "dry_run_execution_skipped",
                run_id=cycle_run_id,
                reason="no_latest_price",
                orders_attempted=0,
                orders_submitted=0,
            )
        return _result(
            status="no_trade",
            execution_status="no_trade",
            confirmed_order_count=0,
            scan=scan_result,
            selection=shortlist_payload,
            execution={"selected": selected_candidate, "paper_order": {}, "risk_result": {}, "reconciliation": {}},
            persistence_payload={"scan": {"status": "saved" if persist else "skipped"}, "execution": {"status": "skipped"}},
        )

    strategy_signals = evaluate_all_strategies({**selected_candidate, "latest_price": latest_price})
    if not strategy_signals:
        if dry_run:
            _emit_telemetry(
                telemetry_callback,
                "dry_run_execution_skipped",
                run_id=cycle_run_id,
                reason="no_strategy_signals",
                orders_attempted=0,
                orders_submitted=0,
            )
        return _result(
            status="no_trade",
            execution_status="no_trade",
            confirmed_order_count=0,
            scan=scan_result,
            selection=shortlist_payload,
            execution={"selected": selected_candidate, "paper_order": {}, "risk_result": {"approved": False, "reason": "no_strategy_signals"}, "reconciliation": {}},
            persistence_payload={"scan": {"status": "saved" if persist else "skipped"}, "execution": {"status": "skipped"}},
        )

    execution_repo = execution_repo_factory(database_url=database_url or getattr(config, "database_url", None))
    try:
        fetch_leaderboard = getattr(execution_repo, "fetch_latest_strategy_leaderboard", None)
        leaderboard = fetch_leaderboard() if persist and callable(fetch_leaderboard) else []
        paused = set(paused_strategies_from_drawdown(leaderboard, max_drawdown_threshold=float(os.getenv("STRATEGY_MAX_DRAWDOWN", "0.20"))))

        active_strategy_ids = [str(item.get("strategy_id") or "") for item in strategy_signals if str(item.get("strategy_id") or "") not in paused]
        allocations = allocate_equal_risk(active_strategy_ids)
        for row in strategy_signals:
            sid = str(row.get("strategy_id") or "")
            row["requested_risk_allocation"] = float(allocations.get(sid, row.get("requested_risk_allocation") or 0.0))
            row["paused"] = sid in paused

        tradable_signals = [row for row in strategy_signals if str(row.get("signal") or "").upper() == "BUY" and not bool(row.get("paused"))]
        if not tradable_signals:
            if dry_run:
                _emit_telemetry(
                    telemetry_callback,
                    "dry_run_execution_skipped",
                    run_id=cycle_run_id,
                    reason="no_active_buy_signals",
                    orders_attempted=0,
                    orders_submitted=0,
                )
            return _result(
                status="no_trade",
                execution_status="no_trade",
                confirmed_order_count=0,
                scan=scan_result,
                selection=shortlist_payload,
                execution={
                    "selected": selected_candidate,
                    "strategy_signals": strategy_signals,
                    "paper_order": {},
                    "risk_result": {"approved": False, "reason": "no_active_buy_signals", "paused_strategies": sorted(paused)},
                    "reconciliation": {},
                },
                persistence_payload={"scan": {"status": "saved" if persist else "skipped"}, "execution": {"status": "skipped"}},
            )

        selected_strategy = sorted(
            tradable_signals,
            key=lambda item: (
                -float(item.get("strategy_score") or 0.0),
                -float(item.get("confidence") or 0.0),
                str(item.get("strategy_id") or ""),
            ),
        )[0]

        max_position_equity_percent = _effective_max_position_equity_percent(config)
        max_open_positions = _effective_max_open_positions(config)
        per_strategy_allocation = float(selected_strategy.get("requested_risk_allocation") or 0.25)
        notional_cap_by_equity = max(float(broker_equity), 0.0) * (max_position_equity_percent / 100.0)
        notional_cap_by_allocation = notional_cap_by_equity * max(min(per_strategy_allocation, 1.0), 0.0)
        suggested_notional = float(selected_candidate.get("suggested_paper_notional") or notional_cap_by_allocation)
        target_notional = min(max(suggested_notional, 0.0), notional_cap_by_equity, notional_cap_by_allocation if notional_cap_by_allocation > 0 else notional_cap_by_equity)

        if target_notional <= 0:
            if dry_run:
                _emit_telemetry(
                    telemetry_callback,
                    "dry_run_execution_skipped",
                    run_id=cycle_run_id,
                    reason="zero_target_notional",
                    orders_attempted=0,
                    orders_submitted=0,
                )
            return _result(
                status="no_trade",
                execution_status="no_trade",
                confirmed_order_count=0,
                scan=scan_result,
                selection=shortlist_payload,
                execution={"selected": selected_candidate, "strategy_signals": strategy_signals, "paper_order": {}, "risk_result": {"approved": False, "reason": "zero_target_notional"}, "reconciliation": {}},
                persistence_payload={"scan": {"status": "saved" if persist else "skipped"}, "execution": {"status": "skipped"}},
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
        target_weight = min(target_notional / max(float(broker_equity), 1.0), 1.0)
        planned_orders = plan_paper_orders(
            target_weights={selected_symbol: target_weight},
            current_positions=broker_positions,
            reference_prices={selected_symbol: latest_price},
            portfolio_value=max(float(broker_equity), 1.0),
            current_cash=float(broker_cash),
            settings=planner_settings,
        )
        planned_order = dict((planned_orders.get("orders") or [{}])[0]) if planned_orders.get("orders") else {}
        if not planned_order:
            if dry_run:
                _emit_telemetry(
                    telemetry_callback,
                    "dry_run_execution_skipped",
                    run_id=cycle_run_id,
                    reason="planner_rejected",
                    orders_attempted=0,
                    orders_submitted=0,
                )
            return _result(
                status="no_trade",
                execution_status="no_trade",
                confirmed_order_count=0,
                scan=scan_result,
                selection=shortlist_payload,
                execution={"selected": selected_candidate, "strategy_signals": strategy_signals, "paper_order": {}, "risk_result": {"approved": False, "reason": "planner_rejected"}, "reconciliation": {}},
                persistence_payload={"scan": {"status": "saved" if persist else "skipped"}, "execution": {"status": "skipped"}},
            )

        risk = RiskManager(max_position_size=float(MAX_POSITION_SIZE), max_daily_loss=float(MAX_DAILY_LOSS), daily_loss_limit=float(DAILY_LOSS_LIMIT))
        trade_value = float(planned_order.get("notional") or 0.0)
        supports_scaling = bool(selected_strategy.get("supports_scaling", False))
        existing_qty = float((broker_positions.get(selected_symbol) or {}).get("quantity") or 0.0)
        selected_quantum = dict(selected_candidate.get("quantum_score") or {})
        quantum_rejections = {str(item) for item in list(selected_quantum.get("rejection_reasons") or [])}
        stale_data_ok = "stale_data" not in quantum_rejections
        liquidity_ok = "average_dollar_volume_below_minimum" not in quantum_rejections and "minimum_price_check_failed" not in quantum_rejections
        reward_risk_ok = "invalid_reward_risk_structure" not in quantum_rejections and "reward_risk_below_minimum" not in quantum_rejections
        risk_checks = {
            "position_size": bool(trade_value <= notional_cap_by_equity),
            "cash": bool(float(broker_cash) >= trade_value),
            "buying_power": bool(float(broker_buying_power) >= trade_value),
            "daily_loss": bool(risk.daily_loss < float(DAILY_LOSS_LIMIT)),
            "existing_position": bool(existing_qty <= 0 or supports_scaling),
            "open_entry_order": not _has_open_entry_order(broker_open_orders, selected_symbol),
            "max_open_positions": bool(_position_count(broker_positions) < int(max_open_positions) or (existing_qty > 0 and supports_scaling)),
            "stale_data": bool(stale_data_ok),
            "liquidity": bool(liquidity_ok),
            "reward_risk": bool(reward_risk_ok),
            "duplicate_protection": True,
        }

        if PAPER_VALIDATION_DUPLICATE_RUN_PROTECTION:
            strategy_fp = f"{selected_strategy.get('strategy_id')}:{selected_strategy.get('strategy_version')}"
            execution_fingerprint = _execution_fingerprint(selected_symbol, float(planned_order.get("quantity") or 0.0), f"PAPER:{strategy_fp}")
            existing = execution_repo.fetch_latest_submitting_run_by_execution_fingerprint(execution_fingerprint)
            risk_checks["duplicate_protection"] = existing is None
        else:
            execution_fingerprint = _execution_fingerprint(selected_symbol, float(planned_order.get("quantity") or 0.0), "PAPER")
            existing = None

        if not risk.approve_trade(max(float(broker_equity), 1.0), trade_value, current_loss=0.0) or not all(risk_checks.values()):
            rejected_status = "duplicate_rejected" if existing is not None and risk_checks.get("duplicate_protection") is False else "risk_rejected"
            if dry_run:
                _emit_telemetry(
                    telemetry_callback,
                    "dry_run_execution_skipped",
                    run_id=cycle_run_id,
                    reason=rejected_status,
                    orders_attempted=0,
                    orders_submitted=0,
                )
            return _result(
                status=rejected_status,
                execution_status=rejected_status,
                confirmed_order_count=0,
                scan=scan_result,
                selection=shortlist_payload,
                execution={
                    "selected": selected_candidate,
                    "strategy_signals": strategy_signals,
                    "selected_strategy": selected_strategy,
                    "paper_order": {"symbol": selected_symbol, "side": "BUY", "quantity": planned_order.get("quantity"), "notional": trade_value, "submission_status": "rejected"},
                    "risk_result": {"approved": False, "checks": risk_checks, "duplicate_run": existing},
                    "reconciliation": {},
                },
                persistence_payload={"scan": {"status": "saved" if persist else "skipped"}, "execution": {"status": "skipped"}},
            )

        client_order_id = str(_execution_fingerprint(
            selected_symbol,
            float(planned_order.get("quantity") or 0.0),
            f"{selected_strategy.get('strategy_id')}:{started_dt.date().isoformat()}"
        ))
        client_order_id = f"qtb-{client_order_id}"

        broker_pre_positions = dict(broker_positions)
        broker_pre_cash = float(broker_cash)

        if dry_run:
            _emit_telemetry(
                telemetry_callback,
                "dry_run_execution_skipped",
                run_id=cycle_run_id,
                reason="dry_run_mode",
                symbol=selected_symbol,
                orders_attempted=1,
                orders_submitted=0,
            )
            order_response = {
                "order_id": f"dry-{cycle_run_id}",
                "client_order_id": client_order_id,
                "status": "accepted",
                "filled_quantity": 0.0,
                "average_fill_price": 0.0,
                "requested_quantity": float(planned_order.get("quantity") or 0.0),
                "symbol": selected_symbol,
                "side": str(planned_order.get("side") or "BUY").lower(),
                "broker_backend": str(getattr(broker, "backend", "ALPACA")),
            }
            lifecycle = {
                "order": order_response,
                "status_transitions": [
                    {
                        "event_time": _utc_iso(),
                        "status": "accepted",
                        "previous_status": "",
                        "broker_order_id": order_response["order_id"],
                        "client_order_id": client_order_id,
                        "requested_quantity": order_response["requested_quantity"],
                        "filled_quantity": 0.0,
                        "average_fill_price": 0.0,
                        "rejection_reason": "",
                        "broker_updated_at": _utc_iso(),
                    }
                ],
                "final_status": "accepted",
                "submission_time": _utc_iso(),
                "fill_time": "",
                "execution_latency_seconds": 0.0,
                "is_filled": False,
            }
            broker_post_positions = broker_pre_positions
            broker_post_cash = broker_pre_cash
        else:
            order_response = _as_dict(
                broker.submit_order(
                    side=str(planned_order.get("side") or "buy").lower(),
                    ticker=selected_symbol,
                    quantity=float(planned_order.get("quantity") or 0.0),
                    client_order_id=client_order_id,
                    order_type="market",
                    time_in_force="day",
                    allow_fractional=bool(PAPER_VALIDATION_ALLOW_FRACTIONAL),
                    wait_for_fill=False,
                    reference_price=float(latest_price),
                )
            )
            lifecycle = track_order_lifecycle(broker=broker, initial_order=order_response, poll_seconds=1.0, max_wait_seconds=45.0)
            broker_post_positions = _as_dict(broker.get_positions())
            try:
                account_post = _as_dict(getattr(broker, "get_account", lambda: {})())
                broker_post_cash = float(account_post.get("cash") or broker.get_buying_power() or broker_pre_cash)
                broker_post_buying_power = float(account_post.get("buying_power") or broker_post_cash)
            except Exception:
                broker_post_cash = float(broker.get_buying_power() or broker_pre_cash)
                broker_post_buying_power = broker_post_cash

        final_order = dict(lifecycle.get("order") or order_response)
        final_status = str(lifecycle.get("final_status") or final_order.get("status") or "unknown").lower()
        paper_order_id = str(final_order.get("order_id") or final_order.get("id") or f"{cycle_run_id}-1")
        requested_qty = float(final_order.get("requested_quantity") or planned_order.get("quantity") or 0.0)
        filled_qty = float(final_order.get("filled_quantity") or 0.0)
        fill_price = float(final_order.get("average_fill_price") or latest_price or 0.0)
        counted_filled_qty = filled_qty if final_status == "filled" else 0.0

        expected_positions = dict(broker_pre_positions)
        if final_status == "filled" and counted_filled_qty > 0:
            expected_positions[selected_symbol] = {
                "quantity": float((expected_positions.get(selected_symbol) or {}).get("quantity") or 0.0) + counted_filled_qty,
                "avg_price": fill_price,
            }

        equity_for_weights = max(float(broker_equity), 1.0)
        planned_positions = {
            symbol: {
                "quantity": float((payload or {}).get("quantity") or 0.0),
                "weight": round(float((payload or {}).get("quantity") or 0.0) * float((payload or {}).get("avg_price") or 0.0) / equity_for_weights, 10),
            }
            for symbol, payload in expected_positions.items()
        }
        actual_positions = {
            symbol: {
                "quantity": float((payload or {}).get("quantity") or 0.0),
                "weight": round(float((payload or {}).get("quantity") or 0.0) * float((payload or {}).get("avg_price") or 0.0) / equity_for_weights, 10),
            }
            for symbol, payload in broker_post_positions.items()
        }

        expected_cash = round(broker_pre_cash - (counted_filled_qty * fill_price), 6)
        actual_buying_power = locals().get("broker_post_buying_power", broker_post_cash)
        reconciliation = reconcile_paper_positions(
            planned_positions=planned_positions,
            actual_positions=actual_positions,
            expected_cash=expected_cash,
            actual_cash=broker_post_cash,
            expected_buying_power=expected_cash,
            actual_buying_power=actual_buying_power,
            orders=[
                {
                    "submission_status": final_status,
                    "filled_quantity": counted_filled_qty,
                    "quantity": requested_qty,
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
                        "strategy_id": str(selected_strategy.get("strategy_id") or "unknown"),
                        "strategy_version": str(selected_strategy.get("strategy_version") or "unknown"),
                        "strategy_fingerprint": f"{selected_strategy.get('strategy_id')}:{selected_strategy.get('strategy_version')}",
                        "research_run_id": cycle_run_id,
                        "scanner_timestamp": completed_at,
                        "started_at": started_at,
                        "completed_at": _utc_iso(),
                        "mode": "PAPER",
                        "status": "completed" if str(reconciliation.get("reconciliation_status") or "").lower() in {"matched", "matched_with_tolerance"} else "failed",
                        "dry_run": bool(dry_run),
                        "proposed_order_count": 1,
                        "approved_order_count": 1,
                        "rejected_order_count": 0,
                        "submitted_order_count": 0 if dry_run else (1 if final_status in {"new", "accepted", "partially_filled", "filled", "pending"} else 0),
                        "filled_order_count": 1 if counted_filled_qty > 0 else 0,
                        "failed_order_count": 1 if final_status in {"rejected", "failed", "canceled", "cancelled", "expired"} else 0,
                        "configuration": {
                            "portfolio": {"maximum_orders": 1, "max_open_positions": max_open_positions},
                            "risk": {
                                "max_position_size": float(MAX_POSITION_SIZE),
                                "max_position_equity_percent": max_position_equity_percent,
                                "max_daily_loss": float(MAX_DAILY_LOSS),
                                "daily_loss_limit": float(DAILY_LOSS_LIMIT),
                            },
                            "dry_run": bool(dry_run),
                        },
                        "risk_snapshot": {
                            "checks": risk_checks,
                            "paused_strategies": sorted(paused),
                            "strategy_allocations": allocations,
                        },
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
                            "side": str(planned_order.get("side") or "BUY").upper(),
                            "quantity": float(planned_order.get("quantity") or 0.0),
                            "notional": float(planned_order.get("notional") or 0.0),
                            "target_weight": float(target_weight),
                            "current_weight": 0.0,
                            "weight_delta": float(target_weight),
                            "reference_price": latest_price,
                            "proposed_at": started_at,
                            "risk_status": "approved",
                            "risk_reason": "approved",
                            "submission_status": final_status,
                            "broker_order_id": paper_order_id,
                            "client_order_id": client_order_id,
                            "requested_quantity": requested_qty,
                            "broker_backend": str(final_order.get("broker_backend") or getattr(broker, "backend", "ALPACA")),
                            "order_type": str(final_order.get("order_type") or "market"),
                            "time_in_force": str(final_order.get("time_in_force") or "day"),
                            "broker_updated_at": str(final_order.get("updated_at") or _utc_iso()),
                            "rejection_reason": str(final_order.get("rejection_reason") or ""),
                            "submitted_at": lifecycle.get("submission_time"),
                            "filled_quantity": counted_filled_qty,
                            "average_fill_price": fill_price if counted_filled_qty > 0 else 0.0,
                            "filled_at": lifecycle.get("fill_time") if counted_filled_qty > 0 else None,
                            "canceled_at": _utc_iso() if final_status in {"canceled", "cancelled"} else None,
                            "failed_at": _utc_iso() if final_status in {"rejected", "failed", "expired"} else None,
                            "error_message": None,
                            "order_payload": {
                                "source": "continuous_scan_cycle",
                                "strategy": selected_strategy,
                                "status_transitions": lifecycle.get("status_transitions") or [],
                                "execution_latency_seconds": lifecycle.get("execution_latency_seconds"),
                                "dry_run": bool(dry_run),
                            },
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
                            "buying_power": actual_buying_power,
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
            save_transitions = getattr(execution_repo, "save_order_status_transitions", None)
            if callable(save_transitions):
                save_transitions(
                    run_id=validation_run_id,
                    symbol=selected_symbol,
                    paper_order_id=f"{validation_run_id}-0001",
                    transitions=[
                        {
                            **dict(item),
                            "execution_latency_seconds": lifecycle.get("execution_latency_seconds"),
                        }
                        for item in list(lifecycle.get("status_transitions") or [])
                    ],
                )

            # Persist closed trades only on completed sell exits that flat a symbol.
            if str(planned_order.get("side") or "").upper() == "SELL" and final_status == "filled" and counted_filled_qty > 0:
                pre_qty = float((broker_pre_positions.get(selected_symbol) or {}).get("quantity") or 0.0)
                post_qty = float((broker_post_positions.get(selected_symbol) or {}).get("quantity") or 0.0)
                if pre_qty > 0 and post_qty <= 0:
                    entry_price = float((broker_pre_positions.get(selected_symbol) or {}).get("avg_price") or fill_price)
                    qty = min(pre_qty, counted_filled_qty)
                    gross = (fill_price - entry_price) * qty
                    est_slippage = abs(float(os.getenv("ESTIMATED_SLIPPAGE_BPS", "5")) / 10000.0 * fill_price * qty)
                    est_fees = abs(float(os.getenv("ESTIMATED_FEES_PER_TRADE", "0")))
                    net = gross - est_slippage - est_fees
                    pct = (net / (entry_price * qty)) if entry_price > 0 and qty > 0 else 0.0
                    save_closed_trade = getattr(execution_repo, "save_closed_trade", None)
                    if callable(save_closed_trade):
                        save_closed_trade(
                            {
                                "strategy_id": str(selected_strategy.get("strategy_id") or "unknown"),
                                "strategy_version": str(selected_strategy.get("strategy_version") or "unknown"),
                                "symbol": selected_symbol,
                                "entry_timestamp": started_at,
                                "exit_timestamp": _utc_iso(),
                                "entry_price": entry_price,
                                "exit_price": fill_price,
                                "quantity": qty,
                                "realized_gross_pnl": round(gross, 6),
                                "estimated_fees": round(est_fees, 6),
                                "estimated_slippage": round(est_slippage, 6),
                                "net_pnl": round(net, 6),
                                "percentage_return": round(pct, 6),
                                "holding_duration_hours": float(os.getenv("DEFAULT_HOLDING_DURATION_HOURS", "0")),
                                "max_adverse_excursion": 0.0,
                                "max_favorable_excursion": 0.0,
                                "exit_reason": str(final_order.get("rejection_reason") or "filled_exit"),
                                "market_regime": str(selected_strategy.get("market_regime") or "unknown"),
                                "close_type": "signal_exit",
                            }
                        )

            list_closed = getattr(execution_repo, "list_closed_trades", None)
            replace_leaderboard = getattr(execution_repo, "replace_strategy_leaderboard", None)
            if callable(list_closed) and callable(replace_leaderboard):
                leaderboard_rows = build_strategy_leaderboard(list_closed(limit=5000))
                replace_leaderboard(leaderboard_rows)

        confirmed = 1 if str(reconciliation.get("reconciliation_status") or "").lower() in {"matched", "matched_with_tolerance"} and int(reconciliation.get("position_mismatch_count") or 0) == 0 and final_status not in {"rejected", "failed", "canceled", "cancelled", "expired"} else 0

        return _result(
            status="completed",
            execution_status="completed",
            confirmed_order_count=confirmed,
            scan=scan_result,
            selection=shortlist_payload,
            execution={
                "selected": selected_candidate,
                "strategy_signals": strategy_signals,
                "selected_strategy": selected_strategy,
                "paper_order": {
                    "order_id": paper_order_id,
                    "client_order_id": client_order_id,
                    "symbol": selected_symbol,
                    "shares": counted_filled_qty,
                    "requested_quantity": requested_qty,
                    "fill_price": fill_price if counted_filled_qty > 0 else 0.0,
                    "timestamp": _utc_iso(),
                    "submission_status": final_status,
                    "status_transitions": lifecycle.get("status_transitions") or [],
                    "execution_latency_seconds": lifecycle.get("execution_latency_seconds"),
                    "submission_time": lifecycle.get("submission_time"),
                    "fill_time": lifecycle.get("fill_time"),
                    "rejection_reason": final_order.get("rejection_reason") or "",
                    "dry_run": bool(dry_run),
                },
                "risk_result": {"approved": True, "checks": risk_checks, "strategy_allocations": allocations},
                "reconciliation": reconciliation,
                "execution_fingerprint": execution_fingerprint,
                "validation_run_id": validation_run_id,
            },
            persistence_payload={"scan": {"status": "saved" if persist else "skipped"}, "execution": {"status": "saved" if persist else "skipped", "run_id": validation_run_id if persist else None}},
        )
    finally:
        execution_repo.close()
