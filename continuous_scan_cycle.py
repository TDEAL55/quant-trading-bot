from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import inspect
import os
from typing import Any, Callable

from config import (
    BENCHMARK_SYMBOL,
    CORRELATION_ALLOCATION_REDUCTION_FACTOR,
    CORRELATION_LOOKBACK_DAYS,
    CORRELATION_MIN_OVERLAP_DAYS,
    DAILY_LOSS_LIMIT,
    MAX_DAILY_ORDERS,
    MAX_OPEN_POSITIONS,
    MAX_POSITION_EQUITY_PERCENT,
    MAX_DAILY_LOSS,
    MAX_POSITION_SIZE,
    MAX_VALIDATION_ORDERS,
    MAX_VALIDATION_ORDER_NOTIONAL,
    PAPER_VALIDATION_ALLOW_FRACTIONAL,
    PAPER_VALIDATION_CASH_BUFFER,
    PAPER_VALIDATION_DUPLICATE_RUN_PROTECTION,
    PAPER_VALIDATION_MAX_ORDER_NOTIONAL,
    PAPER_VALIDATION_MAX_ORDERS,
    PAPER_VALIDATION_MIN_ORDER_NOTIONAL,
    PAPER_VALIDATION_QUANTITY_PRECISION,
    PAPER_VALIDATION_REBALANCE_TOLERANCE,
    PAPER_VALIDATION_RECONCILIATION_TOLERANCE,
    PAPER_EXECUTION_ENABLED,
    SCANNER_MAX_COARSE_CANDIDATES,
    SCANNER_MAX_DEEP_SCORE_SYMBOLS,
    SCANNER_MAX_SCAN_SECONDS,
    PORTFOLIO_ALLOCATION_MODE,
    PORTFOLIO_MAX_CORRELATION,
    PORTFOLIO_MAX_POSITION_PERCENT,
    PORTFOLIO_MAX_POSITIONS,
    PORTFOLIO_MAX_SECTOR_PERCENT,
    PORTFOLIO_MAX_STRATEGY_PERCENT,
    PORTFOLIO_MIN_CASH_RESERVE_PERCENT,
    PORTFOLIO_MIN_QUANTUM_SCORE,
    PORTFOLIO_MIN_RISK_REWARD,
    PORTFOLIO_UNKNOWN_SECTOR_MAX_PERCENT,
    CONTROLLED_PAPER_VALIDATION,
)
from correlation_engine import CorrelationPolicy
from market_data import download_price_data, download_price_data_batch
from deployment_config import load_deployment_config
from order_lifecycle import track_order_lifecycle
from paper_broker import create_paper_broker
from paper_execution_repository import MonitoringPaperExecutionRepository, PaperValidationRunPayload
from paper_exit_execution import execute_guard_exit
from paper_order_planner import OrderPlannerSettings, plan_paper_orders
from paper_position_guard import PositionGuardSettings, review_paper_positions
from paper_reconciliation import reconcile_paper_positions
from portfolio_allocator import AllocationPolicy
from portfolio_intelligence import run_portfolio_intelligence
from risk_manager import RiskManager
from scanner_repository import save_scan_results
from scanner_runner import SAMPLE_SYMBOLS, _load_paper_positions, _symbol_records_from_list, run_scan, run_shortlist_only
from sector_enrichment import enrich_sector_records
from sector_manager import SectorPolicy
from self_improving_repository import SelfImprovingRepository
from sprint_10_2_execution_validation import _execution_fingerprint
from stock_universe import AlpacaAssetUniverseError, get_universe_cache_stats, load_stock_universe
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


def _emit_notification(
    notification_callback: Callable[..., Any] | None,
    *,
    event_type: str,
    title: str,
    message: str,
    severity: str,
    metadata: dict[str, Any] | None = None,
    deduplication_key: str | None = None,
) -> None:
    if notification_callback is None:
        return
    try:
        notification_callback(
            event_type=event_type,
            title=title,
            message=message,
            severity=severity,
            metadata=dict(metadata or {}),
            deduplication_key=deduplication_key,
        )
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


def _correlation_history_window(lookback_days: int) -> tuple[str, str]:
    end_date = _utc_now().date()
    bounded_lookback = max(int(lookback_days), 30)
    start_date = end_date - timedelta(days=max(bounded_lookback * 2, 90))
    return start_date.isoformat(), end_date.isoformat()


def _normalize_history_rows(rows: Any, lookback_days: int) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    if isinstance(rows, dict):
        for date_key, value in rows.items():
            try:
                close = float(value)
            except (TypeError, ValueError):
                continue
            if close > 0:
                normalized.append({"date": str(date_key), "close": close})
    elif isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            date_key = row.get("date") or row.get("timestamp") or row.get("t")
            try:
                close = float(row.get("close") or row.get("price") or row.get("c"))
            except (TypeError, ValueError):
                continue
            if date_key is None or close <= 0:
                continue
            normalized.append({"date": str(date_key), "close": close})
    normalized = sorted(normalized, key=lambda item: str(item.get("date") or ""))
    return normalized[-max(int(lookback_days), 2) :]


def _extract_scan_history(scan_payload: dict[str, Any], lookback_days: int) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    source = dict(scan_payload.get("price_history_by_symbol") or {})
    for symbol, rows in source.items():
        normalized_symbol = str(symbol or "").strip().upper()
        if not normalized_symbol:
            continue
        normalized_rows = _normalize_history_rows(rows, lookback_days)
        if normalized_rows:
            out[normalized_symbol] = normalized_rows

    benchmark_rows = _normalize_history_rows(scan_payload.get("benchmark_price_history") or [], lookback_days)
    if benchmark_rows:
        out[str(BENCHMARK_SYMBOL).upper()] = benchmark_rows
    return out


def _frame_to_history_rows(frame: Any, lookback_days: int) -> list[dict[str, Any]]:
    try:
        close_series = frame.get("close")
    except Exception:
        close_series = None
    if close_series is None:
        return []

    rows: list[dict[str, Any]] = []
    try:
        series = close_series.dropna()
    except Exception:
        return []
    if len(series) < 2:
        return []

    tail = series.tail(max(int(lookback_days), 2))
    for idx, value in tail.items():
        try:
            close = float(value)
        except (TypeError, ValueError):
            continue
        if close <= 0:
            continue
        try:
            date_key = idx.date().isoformat()
        except Exception:
            date_key = str(idx)
        rows.append({"date": str(date_key), "close": close})
    return rows


def _fetch_missing_history(
    symbols: list[str],
    *,
    lookback_days: int,
    batch_loader: Callable[[list[str], str, str], dict[str, Any]],
    single_loader: Callable[[str, str, str], Any],
) -> tuple[dict[str, list[dict[str, Any]]], list[str], list[str], bool]:
    if not symbols:
        return {}, [], [], False

    start_date, end_date = _correlation_history_window(lookback_days)
    unique_symbols = sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()})
    history: dict[str, list[dict[str, Any]]] = {}
    missing: list[str] = []
    used_batch_loader = False

    frames: dict[str, Any] = {}
    try:
        frames = dict(batch_loader(unique_symbols, start_date, end_date) or {})
        used_batch_loader = True
    except Exception:
        frames = {}

    for symbol in unique_symbols:
        frame = frames.get(symbol)
        rows = _frame_to_history_rows(frame, lookback_days) if frame is not None else []
        if rows:
            history[symbol] = rows
            continue

        try:
            fallback_frame = single_loader(symbol, start_date, end_date)
            rows = _frame_to_history_rows(fallback_frame, lookback_days)
        except Exception:
            rows = []
        if rows:
            history[symbol] = rows
        else:
            missing.append(symbol)

    return history, sorted(history.keys()), sorted(missing), used_batch_loader


def _average_overlap_days(correlation_summary: dict[str, Any]) -> float | None:
    details = list(correlation_summary.get("pair_details") or [])
    overlaps = [int(item.get("overlap_days") or 0) for item in details if item.get("correlation") is not None]
    if not overlaps:
        return None
    return round(sum(overlaps) / len(overlaps), 6)


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
    history_batch_loader: Callable[[list[str], str, str], dict[str, Any]] = download_price_data_batch,
    history_single_loader: Callable[[str, str, str], Any] = download_price_data,
    sector_enricher: Callable[..., tuple[list[dict[str, Any]], dict[str, Any]]] = enrich_sector_records,
    sample_symbols: list[str] | None = None,
    symbols: list[str] | None = None,
    persist: bool = True,
    dry_run: bool = False,
    diagnostic_symbol_limit: int | None = None,
    telemetry_callback: Callable[[str, dict[str, Any]], None] | None = None,
    notification_callback: Callable[..., Any] | None = None,
) -> ContinuousScanCycleResult:
    config = config_loader()
    if str(config.trading_mode).upper() != "PAPER":
        raise RuntimeError("continuous scan cycle requires TRADING_MODE=PAPER")

    validation_order_limit = max(int(MAX_VALIDATION_ORDERS or 0), 0)
    autonomous_execution_enabled = bool(PAPER_EXECUTION_ENABLED)
    controlled_validation_mode = bool(autonomous_execution_enabled and CONTROLLED_PAPER_VALIDATION)
    validation_notional_cap = max(float(MAX_VALIDATION_ORDER_NOTIONAL or 0.0), 0.0) if controlled_validation_mode else 0.0

    started_dt = _coerce_utc(now_provider())
    started_at = started_dt.isoformat()
    run_stamp = started_dt.strftime("%Y%m%d%H%M%S%f")
    cycle_run_id = f"continuous-scan-{run_stamp}"

    _emit_notification(
        notification_callback,
        event_type="scan_started",
        title="Scan Started",
        message="Starting scan cycle.",
        severity="INFO",
        metadata={"run_id": cycle_run_id, "dry_run": bool(dry_run), "status": "scan_started"},
        deduplication_key=f"scan_started:{cycle_run_id}",
    )

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
        execution_payload = dict(execution or {})
        counters = {
            "orders_recommended": 0,
            "orders_submission_requested": 0,
            "orders_submitted": 0,
            "orders_filled": 0,
            "orders_rejected": 0,
        }
        raw_counters = dict(execution_payload.get("execution_counters") or {})
        if "orders_submission_requested" not in raw_counters and "orders_attempted" in raw_counters:
            # Backward compatibility for historical payloads that only emitted orders_attempted.
            raw_counters["orders_submission_requested"] = int(raw_counters.get("orders_attempted") or 0)
        for key in counters:
            counters[key] = int(raw_counters.get(key) or 0)
        # Deprecated compatibility alias for older downstream consumers.
        counters["orders_attempted"] = int(counters["orders_submission_requested"])
        execution_payload["execution_counters"] = counters

        result_payload = ContinuousScanCycleResult(
            run_id=cycle_run_id,
            started_at=started_at,
            completed_at=_utc_iso(),
            status=status,
            execution_status=execution_status,
            confirmed_order_count=confirmed_order_count,
            scan=scan,
            selection=selection,
            execution=execution_payload,
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
            orders_recommended=int(counters["orders_recommended"]),
            orders_submission_requested=int(counters["orders_submission_requested"]),
            orders_submitted=int(counters["orders_submitted"]),
            orders_filled=int(counters["orders_filled"]),
            orders_rejected=int(counters["orders_rejected"]),
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
        try:
            full_universe_records = list(universe_loader())
        except AlpacaAssetUniverseError as exc:
            telemetry = dict(getattr(exc, "telemetry", {}) or {})
            _emit_telemetry(
                telemetry_callback,
                "alpaca_asset_universe_fetch_failed",
                run_id=cycle_run_id,
                api_exception_type=str(telemetry.get("api_exception_type") or type(exc).__name__),
                api_request_elapsed_time=float(telemetry.get("api_request_elapsed_time") or 0.0),
                fallback_used=bool(telemetry.get("fallback_used", False)),
                unfiltered_asset_count=int(telemetry.get("unfiltered_asset_count") or 0),
                filtered_api_asset_count=int(telemetry.get("filtered_api_asset_count") or 0),
                client_filtered_asset_count=int(telemetry.get("client_filtered_asset_count") or 0),
                active_count=int(telemetry.get("active_count") or 0),
                tradable_count=int(telemetry.get("tradable_count") or 0),
                us_equity_count=int(telemetry.get("us_equity_count") or 0),
                rejected_by_asset_class=int(telemetry.get("rejected_by_asset_class") or 0),
                rejected_by_status=int(telemetry.get("rejected_by_status") or 0),
                rejected_non_tradable=int(telemetry.get("rejected_non_tradable") or 0),
                rejected_missing_symbol=int(telemetry.get("rejected_missing_symbol") or 0),
                error_message=str(exc),
            )
            if int(telemetry.get("unfiltered_asset_count") or 0) == 0:
                _emit_telemetry(
                    telemetry_callback,
                    "alpaca_asset_universe_empty",
                    run_id=cycle_run_id,
                    unfiltered_asset_count=0,
                    filtered_api_asset_count=int(telemetry.get("filtered_api_asset_count") or 0),
                    client_filtered_asset_count=int(telemetry.get("client_filtered_asset_count") or 0),
                )
            raise
        telemetry_stats = dict(get_universe_cache_stats() or {})
        if telemetry_stats:
            _emit_telemetry(
                telemetry_callback,
                "alpaca_asset_universe_telemetry",
                run_id=cycle_run_id,
                unfiltered_asset_count=int(telemetry_stats.get("unfiltered_asset_count") or 0),
                filtered_api_asset_count=int(telemetry_stats.get("filtered_api_asset_count") or 0),
                client_filtered_asset_count=int(telemetry_stats.get("client_filtered_asset_count") or 0),
                active_count=int(telemetry_stats.get("active_count") or 0),
                tradable_count=int(telemetry_stats.get("tradable_count") or 0),
                us_equity_count=int(telemetry_stats.get("us_equity_count") or 0),
                rejected_by_asset_class=int(telemetry_stats.get("rejected_by_asset_class") or 0),
                rejected_by_status=int(telemetry_stats.get("rejected_by_status") or 0),
                rejected_non_tradable=int(telemetry_stats.get("rejected_non_tradable") or 0),
                rejected_missing_symbol=int(telemetry_stats.get("rejected_missing_symbol") or 0),
                fallback_used=bool(telemetry_stats.get("fallback_used", False)),
                api_exception_type=str(telemetry_stats.get("api_exception_type") or ""),
                api_request_elapsed_time=float(telemetry_stats.get("api_request_elapsed_time") or 0.0),
            )
            if bool(telemetry_stats.get("fallback_used", False)):
                _emit_telemetry(
                    telemetry_callback,
                    "alpaca_asset_filter_fallback_used",
                    run_id=cycle_run_id,
                    unfiltered_asset_count=int(telemetry_stats.get("unfiltered_asset_count") or 0),
                    filtered_api_asset_count=int(telemetry_stats.get("filtered_api_asset_count") or 0),
                    client_filtered_asset_count=int(telemetry_stats.get("client_filtered_asset_count") or 0),
                )
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
    except Exception as exc:
        broker_cash = float(getattr(broker, "get_cash", lambda: 0.0)() or 0.0)
        broker_buying_power = float(getattr(broker, "get_buying_power", lambda: broker_cash)() or broker_cash)
        broker_equity = float(getattr(broker, "get_equity", lambda: broker_buying_power)() or broker_buying_power)
        _emit_notification(
            notification_callback,
            event_type="broker_connection_failed",
            title="Broker Connection Warning",
            message="Broker account snapshot fallback was used.",
            severity="ERROR",
            metadata={
                "run_id": cycle_run_id,
                "dry_run": bool(dry_run),
                "status": "fallback",
                "error_type": type(exc).__name__,
                "safe_error_message": str(exc),
            },
            deduplication_key=f"broker_connection_failed:{cycle_run_id}",
        )

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

    position_guard_payload: dict[str, Any] = {"reviews": [], "exit_candidates": [], "summary": {"enabled": False}}
    if bool(getattr(config, "position_guard_enabled", False)):
        position_guard_payload = review_paper_positions(
            positions=broker_positions,
            open_orders=broker_open_orders,
            settings=PositionGuardSettings(
                stop_loss_percent=float(getattr(config, "position_guard_stop_loss_percent", 4.0)),
                take_profit_percent=float(getattr(config, "position_guard_take_profit_percent", 8.0)),
                max_exits_per_cycle=int(getattr(config, "position_guard_max_exits_per_cycle", 1)),
            ),
        )
        position_guard_payload.setdefault("summary", {})["enabled"] = True
        _emit_telemetry(
            telemetry_callback,
            "paper_position_guard_complete",
            run_id=cycle_run_id,
            dry_run=bool(dry_run),
            **dict(position_guard_payload.get("summary") or {}),
        )

        exit_candidates = list(position_guard_payload.get("exit_candidates") or [])
        if exit_candidates:
            selected_exit = dict(exit_candidates[0])
            execution_repo = execution_repo_factory(database_url=database_url or getattr(config, "database_url", None))
            try:
                exit_execution = execute_guard_exit(
                    selected_exit,
                    broker=broker,
                    broker_positions=broker_positions,
                    broker_cash=broker_cash,
                    broker_buying_power=broker_buying_power,
                    broker_equity=broker_equity,
                    execution_repo=execution_repo,
                    cycle_run_id=cycle_run_id,
                    started_at=started_at,
                    dry_run=bool(dry_run),
                    paper_execution_enabled=bool(PAPER_EXECUTION_ENABLED)
                    and bool(getattr(config, "position_guard_auto_exit_enabled", False)),
                    allow_fractional=bool(PAPER_VALIDATION_ALLOW_FRACTIONAL),
                    reconciliation_tolerance=float(PAPER_VALIDATION_RECONCILIATION_TOLERANCE),
                    persist=bool(persist),
                )
            finally:
                execution_repo.close()

            exit_status = str(exit_execution.get("status") or "failed")
            exit_order = dict(exit_execution.get("paper_order") or {})
            if exit_status == "completed":
                _emit_notification(
                    notification_callback,
                    event_type="position_closed",
                    title="Paper Position Closed",
                    message="Position guard completed a PAPER exit.",
                    severity="SUCCESS",
                    metadata={
                        "run_id": cycle_run_id,
                        "dry_run": bool(dry_run),
                        "symbol": selected_exit.get("symbol"),
                        "reason": selected_exit.get("exit_reason"),
                        "status": "position_closed",
                        "orders_recommended": 1,
                        "orders_submission_requested": 1,
                        "orders_submitted": int((exit_execution.get("execution_counters") or {}).get("orders_submitted") or 0),
                        "orders_filled": int((exit_execution.get("execution_counters") or {}).get("orders_filled") or 0),
                        "orders_rejected": int((exit_execution.get("execution_counters") or {}).get("orders_rejected") or 0),
                    },
                    deduplication_key=f"position_closed:{cycle_run_id}:{selected_exit.get('symbol')}:{exit_order.get('order_id')}",
                )
            elif exit_status in {"duplicate_rejected", "risk_rejected", "failed"}:
                _emit_notification(
                    notification_callback,
                    event_type="risk_limit_triggered",
                    title="Position Exit Blocked",
                    message="A position-guard exit was blocked or failed safe.",
                    severity="WARNING",
                    metadata={
                        "run_id": cycle_run_id,
                        "dry_run": bool(dry_run),
                        "symbol": selected_exit.get("symbol"),
                        "reason": exit_status,
                        "status": exit_status,
                    },
                    deduplication_key=f"position_guard_exit:{cycle_run_id}:{selected_exit.get('symbol')}:{exit_status}",
                )

            return _result(
                status=f"position_guard_{exit_status}",
                execution_status=exit_status,
                confirmed_order_count=int(exit_execution.get("confirmed_order_count") or 0),
                scan={
                    "scan_payload": {
                        "summary": {"status": "position_guard_exit", "position_guard": position_guard_payload.get("summary") or {}},
                        "ranked_candidates": [],
                        "scan_results": [],
                    },
                    "scan_run": {},
                },
                selection={"selected": [], "position_reviews": position_guard_payload.get("reviews") or []},
                execution={**exit_execution, "selected": selected_exit, "position_reviews": position_guard_payload.get("reviews") or []},
                persistence_payload={"scan": {"status": "skipped_for_position_exit"}, "execution": {"status": exit_status}},
            )

    def _scan_progress_telemetry(payload: dict[str, Any]) -> None:
        payload_dict = dict(payload or {})
        event_name = str(payload_dict.pop("event", "scan_progress") or "scan_progress")
        base = {
            "run_id": cycle_run_id,
            "dry_run": bool(dry_run),
            "trading_mode": str(config.trading_mode),
        }
        base.update(payload_dict)
        _emit_telemetry(telemetry_callback, event_name, **base)

    scan_payload = _invoke_scan_runner(
        scan_runner,
        universe_records,
        progress_callback=_scan_progress_telemetry,
        max_scan_seconds=int(SCANNER_MAX_SCAN_SECONDS),
        coarse_candidate_limit=int(SCANNER_MAX_COARSE_CANDIDATES),
        deep_score_limit=int(SCANNER_MAX_DEEP_SCORE_SYMBOLS),
    )
    summary = dict(scan_payload.get("summary") or {})
    summary["full_universe_count"] = int(full_universe_count)
    summary["diagnostic_symbol_limit"] = int(diagnostic_symbol_limit) if diagnostic_symbol_limit is not None else None
    summary["diagnostic_mode"] = bool(diagnostic_symbol_limit is not None and int(diagnostic_symbol_limit) > 0)
    scan_payload["summary"] = summary

    shortlist_positions = _positions_list(broker_positions)
    sector_enrichment_metadata: dict[str, Any] = {
        "enabled": bool(getattr(config, "sector_enrichment_enabled", False)),
        "status": "disabled",
    }
    if bool(getattr(config, "sector_enrichment_enabled", False)):
        sector_enrichment_started = _utc_now()
        original_ranked = [dict(item or {}) for item in list(scan_payload.get("ranked_candidates") or [])]
        combined_records = [
            *[{**item, "_sector_record_group": "position"} for item in shortlist_positions],
            *[{**item, "_sector_record_group": "candidate"} for item in original_ranked],
        ]
        try:
            enriched_records, sector_enrichment_metadata = sector_enricher(
                combined_records,
                cache_path=str(getattr(config, "sector_enrichment_cache_path", "sector-cache.json")),
                max_symbols=int(getattr(config, "sector_enrichment_max_symbols", 30)),
                timeout_seconds=float(getattr(config, "sector_enrichment_timeout_seconds", 6)),
                total_timeout_seconds=float(getattr(config, "sector_enrichment_total_timeout_seconds", 25)),
                max_workers=int(getattr(config, "sector_enrichment_max_workers", 6)),
                cache_ttl_days=int(getattr(config, "sector_enrichment_cache_ttl_days", 30)),
            )
            sector_enrichment_metadata = dict(sector_enrichment_metadata or {})
            sector_enrichment_metadata["status"] = "completed"
            shortlist_positions = []
            enriched_ranked: list[dict[str, Any]] = []
            for enriched in enriched_records:
                normalized = dict(enriched or {})
                group = str(normalized.pop("_sector_record_group", "") or "")
                if group == "position":
                    shortlist_positions.append(normalized)
                elif group == "candidate":
                    enriched_ranked.append(normalized)
            scan_payload["ranked_candidates"] = enriched_ranked

            sector_by_symbol = {
                str(item.get("symbol") or "").strip().upper(): {
                    "sector": item.get("sector"),
                    "industry": item.get("industry"),
                    "sector_source": item.get("sector_source"),
                }
                for item in enriched_ranked
                if str(item.get("symbol") or "").strip()
            }
            enriched_scan_results = []
            for raw_row in list(scan_payload.get("scan_results") or []):
                row = dict(raw_row or {})
                metadata = sector_by_symbol.get(str(row.get("symbol") or "").strip().upper())
                if metadata and str(metadata.get("sector") or "").strip().lower() not in {"", "unknown"}:
                    row.update(metadata)
                enriched_scan_results.append(row)
            scan_payload["scan_results"] = enriched_scan_results
        except Exception as exc:
            sector_enrichment_metadata = {
                "enabled": True,
                "status": "failed_safe",
                "error_type": type(exc).__name__,
                "safe_error_message": str(exc)[:300],
            }
        sector_enrichment_metadata["elapsed_seconds"] = round(
            max((_utc_now() - sector_enrichment_started).total_seconds(), 0.0), 4
        )
        _emit_telemetry(
            telemetry_callback,
            "sector_enrichment_complete",
            run_id=cycle_run_id,
            **sector_enrichment_metadata,
        )

    summary = dict(scan_payload.get("summary") or {})
    summary["sector_enrichment"] = sector_enrichment_metadata
    scan_payload["summary"] = summary
    shortlist_payload = dict(shortlist_runner(scan_payload, shortlist_positions, broker_cash, broker_equity) or {})
    portfolio_intelligence_payload: dict[str, Any] = {}
    portfolio_started = _utc_now()
    _emit_telemetry(
        telemetry_callback,
        "portfolio_intelligence_start",
        run_id=cycle_run_id,
        dry_run=bool(dry_run),
        candidate_count=int(len(scan_payload.get("ranked_candidates") or [])),
    )
    try:
        strategy_board: list[dict[str, Any]] = []
        intelligence_repo = SelfImprovingRepository(database_url=database_url or getattr(config, "database_url", None))
        try:
            intelligence_payload = intelligence_repo.fetch_dashboard_payload()
            strategy_board = list(intelligence_payload.get("strategy_leaderboard") or [])
        finally:
            intelligence_repo.close()

        history_symbols_requested = sorted(
            {
                str(item.get("symbol") or "").strip().upper()
                for item in list(scan_payload.get("ranked_candidates") or []) + list(shortlist_positions or [])
                if str(item.get("symbol") or "").strip()
            }
        )
        scan_history = _extract_scan_history(scan_payload, int(CORRELATION_LOOKBACK_DAYS))
        missing_symbols = [symbol for symbol in history_symbols_requested if symbol not in scan_history]

        fetched_history: dict[str, list[dict[str, Any]]] = {}
        fetched_usable: list[str] = []
        fetched_missing: list[str] = []
        used_batch_loader = False
        try:
            fetched_history, fetched_usable, fetched_missing, used_batch_loader = _fetch_missing_history(
                missing_symbols,
                lookback_days=int(CORRELATION_LOOKBACK_DAYS),
                batch_loader=history_batch_loader,
                single_loader=history_single_loader,
            )
        except Exception:
            fetched_history = {}
            fetched_usable = []
            fetched_missing = sorted(missing_symbols)
            used_batch_loader = False

        correlation_history_by_symbol = dict(scan_history)
        correlation_history_by_symbol.update(fetched_history)

        history_symbols_usable = sorted(correlation_history_by_symbol.keys())
        history_symbols_missing = sorted({*fetched_missing, *[sym for sym in history_symbols_requested if sym not in correlation_history_by_symbol]})

        allocation_policy = AllocationPolicy(
            max_positions=int(PORTFOLIO_MAX_POSITIONS),
            max_position_percent=float(PORTFOLIO_MAX_POSITION_PERCENT),
            max_sector_percent=float(PORTFOLIO_MAX_SECTOR_PERCENT),
            min_cash_reserve_percent=float(PORTFOLIO_MIN_CASH_RESERVE_PERCENT),
            max_correlation=float(PORTFOLIO_MAX_CORRELATION),
            max_strategy_percent=float(PORTFOLIO_MAX_STRATEGY_PERCENT),
            min_quantum_score=float(PORTFOLIO_MIN_QUANTUM_SCORE),
            min_risk_reward=float(PORTFOLIO_MIN_RISK_REWARD),
            allocation_mode=str(PORTFOLIO_ALLOCATION_MODE),
            unknown_sector_max_percent=float(PORTFOLIO_UNKNOWN_SECTOR_MAX_PERCENT),
            allow_fractional_quantity=bool(PAPER_VALIDATION_ALLOW_FRACTIONAL),
        )
        corr_policy = CorrelationPolicy(
            lookback_days=int(CORRELATION_LOOKBACK_DAYS),
            min_overlap_days=int(CORRELATION_MIN_OVERLAP_DAYS),
            max_correlation=float(PORTFOLIO_MAX_CORRELATION),
            allocation_reduction_factor=float(CORRELATION_ALLOCATION_REDUCTION_FACTOR),
        )
        sec_policy = SectorPolicy(
            max_sector_percent=float(PORTFOLIO_MAX_SECTOR_PERCENT),
            unknown_sector_max_percent=float(PORTFOLIO_UNKNOWN_SECTOR_MAX_PERCENT),
        )

        portfolio_intelligence = run_portfolio_intelligence(
            ranked_candidates=list(scan_payload.get("ranked_candidates") or []),
            current_positions=shortlist_positions,
            account_equity=float(broker_equity),
            available_cash=float(broker_cash),
            price_history_by_symbol=correlation_history_by_symbol,
            strategy_leaderboard=strategy_board,
            allocation_policy=allocation_policy,
            correlation_policy=corr_policy,
            sector_policy=sec_policy,
        )
        portfolio_intelligence_payload = portfolio_intelligence.to_dict()
        portfolio_intelligence_payload["correlation_history_metadata"] = {
            "symbols_requested": history_symbols_requested,
            "symbols_with_usable_history": history_symbols_usable,
            "symbols_missing_history": history_symbols_missing,
            "used_batch_loader_for_missing": bool(used_batch_loader),
        }
        shortlist_payload["portfolio_intelligence"] = portfolio_intelligence_payload

        overlap_days_average = _average_overlap_days(dict(portfolio_intelligence_payload.get("correlation_summary") or {}))
        _emit_telemetry(
            telemetry_callback,
            "correlation_analysis_complete",
            run_id=cycle_run_id,
            average_correlation=portfolio_intelligence_payload.get("correlation_summary", {}).get("average_correlation"),
            maximum_correlation=portfolio_intelligence_payload.get("correlation_summary", {}).get("maximum_correlation"),
            status=portfolio_intelligence_payload.get("correlation_summary", {}).get("status"),
            symbols_requested=int(len(history_symbols_requested)),
            symbols_with_usable_history=int(len(history_symbols_usable)),
            symbols_missing_history=int(len(history_symbols_missing)),
            symbols_missing_list=history_symbols_missing,
            average_overlap_days=overlap_days_average,
            elapsed_time=round(max((_utc_now() - portfolio_started).total_seconds(), 0.0), 4),
        )
        _emit_telemetry(
            telemetry_callback,
            "sector_analysis_complete",
            run_id=cycle_run_id,
            sector_summary=portfolio_intelligence_payload.get("sector_exposures"),
            elapsed_time=round(max((_utc_now() - portfolio_started).total_seconds(), 0.0), 4),
        )
        _emit_telemetry(
            telemetry_callback,
            "strategy_allocation_analysis_complete",
            run_id=cycle_run_id,
            strategy_summary=portfolio_intelligence_payload.get("strategy_exposures"),
            elapsed_time=round(max((_utc_now() - portfolio_started).total_seconds(), 0.0), 4),
        )
        _emit_telemetry(
            telemetry_callback,
            "portfolio_allocation_complete",
            run_id=cycle_run_id,
            selected_count=int(portfolio_intelligence_payload.get("selected_count") or 0),
            rejected_count=int(portfolio_intelligence_payload.get("rejected_count") or 0),
            cash_reserve=float(portfolio_intelligence_payload.get("cash_reserve") or 0.0),
            exposure=float(portfolio_intelligence_payload.get("total_proposed_exposure") or 0.0),
            warnings=list(portfolio_intelligence_payload.get("top_warnings") or []),
            elapsed_time=round(max((_utc_now() - portfolio_started).total_seconds(), 0.0), 4),
        )

        if persist:
            persistence_repo = SelfImprovingRepository(database_url=database_url or getattr(config, "database_url", None))
            try:
                persistence_repo.save_portfolio_intelligence_result(
                    allocation_run_id=f"portfolio-intel:{cycle_run_id}",
                    source_scan_run_id=cycle_run_id,
                    account_equity=float(broker_equity),
                    available_cash=float(broker_cash),
                    investable_capital=max(float(broker_cash) - (float(broker_equity) * float(PORTFOLIO_MIN_CASH_RESERVE_PERCENT) / 100.0), 0.0),
                    result=portfolio_intelligence_payload,
                    configuration={
                        "policy_version": "portfolio_intelligence_v1",
                        "allocation_mode": str(PORTFOLIO_ALLOCATION_MODE),
                        "review_only": True,
                    },
                )
            finally:
                persistence_repo.close()

        if dry_run:
            _emit_telemetry(
                telemetry_callback,
                "portfolio_recommendation_generated",
                run_id=cycle_run_id,
                selected_count=int(portfolio_intelligence_payload.get("selected_count") or 0),
                rejected_count=int(portfolio_intelligence_payload.get("rejected_count") or 0),
                cash_reserve=float(portfolio_intelligence_payload.get("cash_reserve") or 0.0),
                exposure=float(portfolio_intelligence_payload.get("total_proposed_exposure") or 0.0),
                sector_summary=portfolio_intelligence_payload.get("sector_exposures"),
                strategy_summary=portfolio_intelligence_payload.get("strategy_exposures"),
                correlation_summary=portfolio_intelligence_payload.get("correlation_summary"),
                warnings=list(portfolio_intelligence_payload.get("top_warnings") or []),
                orders_submitted=0,
                elapsed_time=round(max((_utc_now() - portfolio_started).total_seconds(), 0.0), 4),
            )
            _emit_notification(
                notification_callback,
                event_type="portfolio_recommendation_generated",
                title="Portfolio Recommendation Generated",
                message="Final portfolio recommendation is ready for human review.",
                severity="INFO",
                metadata={
                    "run_id": cycle_run_id,
                    "dry_run": True,
                    "status": "portfolio_recommendation_generated",
                    "orders_recommended": 0,
                    "orders_submission_requested": 0,
                    "orders_attempted": 0,
                    "orders_submitted": 0,
                    "orders_filled": 0,
                    "orders_rejected": 0,
                    "proposed_notional": float(portfolio_intelligence_payload.get("total_proposed_exposure") or 0.0),
                },
                deduplication_key=f"portfolio_recommendation_generated:{cycle_run_id}",
            )

    except Exception as exc:
        _emit_telemetry(
            telemetry_callback,
            "portfolio_intelligence_failed",
            run_id=cycle_run_id,
            error_type=type(exc).__name__,
            safe_error_message=str(exc),
            elapsed_time=round(max((_utc_now() - portfolio_started).total_seconds(), 0.0), 4),
        )

    selected_candidates = list(shortlist_payload.get("selected") or [])
    selected_candidate = dict(selected_candidates[0]) if selected_candidates else {}
    completed_at = _utc_iso()
    scan_run_payload = _scan_run_payload(cycle_run_id, scan_payload, len(universe_records), completed_at)

    if persist:
        scan_persistor(
            run_payload=scan_run_payload,
            scan_results=list(scan_payload.get("scan_results") or []),
            candidates=selected_candidates,
            position_reviews=list(position_guard_payload.get("reviews") or []),
            database_url=database_url or getattr(config, "database_url", None),
        )

    scan_result = {"scan_payload": scan_payload, "scan_run": scan_run_payload}
    if portfolio_intelligence_payload:
        scan_result["portfolio_intelligence"] = portfolio_intelligence_payload
    if not selected_candidate:
        if dry_run:
            _emit_telemetry(
                telemetry_callback,
                "dry_run_execution_skipped",
                run_id=cycle_run_id,
                reason="no_candidates",
                orders_recommended=0,
                orders_submission_requested=0,
                orders_attempted=0,
                orders_submitted=0,
                orders_filled=0,
                orders_rejected=0,
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
    _emit_notification(
        notification_callback,
        event_type="candidate_selected",
        title="Candidate Selected",
        message="Top candidate selected for execution planning.",
        severity="SUCCESS",
        metadata={
            "run_id": cycle_run_id,
            "dry_run": bool(dry_run),
            "symbol": selected_symbol,
            "quantum_score": float((selected_candidate.get("quantum_score") or {}).get("final_score") or selected_candidate.get("overall_score") or selected_candidate.get("score") or 0.0),
            "strategy_id": str((selected_candidate.get("strategy_ids") or [""])[0] or ""),
            "status": "candidate_selected",
        },
        deduplication_key=f"candidate_selected:{cycle_run_id}:{selected_symbol}",
    )
    latest_price = _latest_price_for_symbol(scan_payload, selected_symbol)
    if latest_price <= 0:
        if dry_run:
            _emit_telemetry(
                telemetry_callback,
                "dry_run_execution_skipped",
                run_id=cycle_run_id,
                reason="no_latest_price",
                orders_recommended=0,
                orders_submission_requested=0,
                orders_attempted=0,
                orders_submitted=0,
                orders_filled=0,
                orders_rejected=0,
            )
            _emit_notification(
                notification_callback,
                event_type="dry_run_trade_skipped",
                title="Dry Run Skip",
                message="No strategy signals available for selected candidate.",
                severity="INFO",
                metadata={
                    "run_id": cycle_run_id,
                    "dry_run": True,
                    "symbol": selected_symbol,
                    "quantum_score": float((selected_candidate.get("quantum_score") or {}).get("final_score") or selected_candidate.get("overall_score") or 0.0),
                    "proposed_notional": float(selected_candidate.get("suggested_paper_notional") or 0.0),
                    "reason": "no_strategy_signals",
                    "orders_recommended": 0,
                    "orders_submission_requested": 0,
                    "orders_attempted": 0,
                    "orders_submitted": 0,
                    "orders_filled": 0,
                    "orders_rejected": 0,
                    "status": "skipped",
                },
                deduplication_key=f"dry_run_trade_skipped:{cycle_run_id}:{selected_symbol}:no_strategy_signals",
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
                orders_recommended=0,
                orders_submission_requested=0,
                orders_attempted=0,
                orders_submitted=0,
                orders_filled=0,
                orders_rejected=0,
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
                    orders_recommended=0,
                    orders_submission_requested=0,
                    orders_attempted=0,
                    orders_submitted=0,
                    orders_filled=0,
                    orders_rejected=0,
                )
                _emit_notification(
                    notification_callback,
                    event_type="dry_run_trade_skipped",
                    title="Dry Run Skip",
                    message="All buy signals are paused or unavailable.",
                    severity="INFO",
                    metadata={
                        "run_id": cycle_run_id,
                        "dry_run": True,
                        "symbol": selected_symbol,
                        "reason": "no_active_buy_signals",
                        "orders_recommended": 0,
                        "orders_submission_requested": 0,
                        "orders_attempted": 0,
                        "orders_submitted": 0,
                        "orders_filled": 0,
                        "orders_rejected": 0,
                        "status": "skipped",
                    },
                    deduplication_key=f"dry_run_trade_skipped:{cycle_run_id}:{selected_symbol}:no_active_buy_signals",
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
        if validation_notional_cap > 0:
            target_notional = min(float(target_notional), float(validation_notional_cap))

        if target_notional <= 0:
            if dry_run:
                _emit_telemetry(
                    telemetry_callback,
                    "dry_run_execution_skipped",
                    run_id=cycle_run_id,
                    reason="zero_target_notional",
                    orders_recommended=0,
                    orders_submission_requested=0,
                    orders_attempted=0,
                    orders_submitted=0,
                    orders_filled=0,
                    orders_rejected=0,
                )
                _emit_notification(
                    notification_callback,
                    event_type="dry_run_trade_skipped",
                    title="Dry Run Skip",
                    message="Trade notional resolved to zero.",
                    severity="INFO",
                    metadata={
                        "run_id": cycle_run_id,
                        "dry_run": True,
                        "symbol": selected_symbol,
                        "proposed_notional": float(target_notional),
                        "reason": "zero_target_notional",
                        "orders_recommended": 0,
                        "orders_submission_requested": 0,
                        "orders_attempted": 0,
                        "orders_submitted": 0,
                        "orders_filled": 0,
                        "orders_rejected": 0,
                        "status": "skipped",
                    },
                    deduplication_key=f"dry_run_trade_skipped:{cycle_run_id}:{selected_symbol}:zero_target_notional",
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
            maximum_order_notional=min(
                float(PAPER_VALIDATION_MAX_ORDER_NOTIONAL),
                float(validation_notional_cap if validation_notional_cap > 0 else PAPER_VALIDATION_MAX_ORDER_NOTIONAL),
            ),
            allow_fractional=bool(PAPER_VALIDATION_ALLOW_FRACTIONAL),
            quantity_precision=int(PAPER_VALIDATION_QUANTITY_PRECISION),
            rebalance_tolerance=float(PAPER_VALIDATION_REBALANCE_TOLERANCE),
            maximum_orders=(
                max(1, min(int(PAPER_VALIDATION_MAX_ORDERS), int(validation_order_limit or 1)))
                if controlled_validation_mode
                else max(1, int(PAPER_VALIDATION_MAX_ORDERS))
            ),
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
                    orders_recommended=0,
                    orders_submission_requested=0,
                    orders_attempted=0,
                    orders_submitted=0,
                    orders_filled=0,
                    orders_rejected=0,
                )
                _emit_notification(
                    notification_callback,
                    event_type="dry_run_trade_skipped",
                    title="Dry Run Skip",
                    message="Order planner rejected the candidate.",
                    severity="INFO",
                    metadata={
                        "run_id": cycle_run_id,
                        "dry_run": True,
                        "symbol": selected_symbol,
                        "reason": "planner_rejected",
                        "orders_recommended": 0,
                        "orders_submission_requested": 0,
                        "orders_attempted": 0,
                        "orders_submitted": 0,
                        "orders_filled": 0,
                        "orders_rejected": 0,
                        "status": "skipped",
                    },
                    deduplication_key=f"dry_run_trade_skipped:{cycle_run_id}:{selected_symbol}:planner_rejected",
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

        _emit_notification(
            notification_callback,
            event_type="trade_recommended",
            title="Trade Recommended",
            message="Recommendation only. No paper order has been submitted.",
            severity="INFO",
            metadata={
                "run_id": cycle_run_id,
                "dry_run": bool(dry_run),
                "symbol": selected_symbol,
                "strategy_id": str(selected_strategy.get("strategy_id") or ""),
                "proposed_quantity": float(planned_order.get("quantity") or 0.0),
                "proposed_notional": float(planned_order.get("notional") or 0.0),
                "orders_recommended": 1,
                "orders_submission_requested": 0,
                "orders_submitted": 0,
                "orders_filled": 0,
                "orders_rejected": 0,
                "status": "recommendation_only",
            },
            deduplication_key=f"trade_recommended:{cycle_run_id}:{selected_symbol}:{selected_strategy.get('strategy_id')}",
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
            "validation_order_limit": bool((not controlled_validation_mode) or validation_order_limit >= 1),
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
            _emit_notification(
                notification_callback,
                event_type="risk_limit_triggered",
                title="Risk Limit Triggered",
                message="Trade was blocked by risk controls.",
                severity="WARNING",
                metadata={
                    "run_id": cycle_run_id,
                    "dry_run": bool(dry_run),
                    "symbol": selected_symbol,
                    "reason": rejected_status,
                    "status": "risk_rejected",
                    "orders_recommended": 1,
                    "orders_submission_requested": 0,
                    "orders_submitted": 0,
                    "orders_filled": 0,
                    "orders_rejected": 0,
                },
                deduplication_key=f"risk_limit_triggered:{cycle_run_id}:{selected_symbol}:{rejected_status}",
            )
            if dry_run:
                _emit_telemetry(
                    telemetry_callback,
                    "dry_run_execution_skipped",
                    run_id=cycle_run_id,
                    reason=rejected_status,
                    orders_recommended=1,
                    orders_submission_requested=0,
                    orders_submitted=0,
                    orders_filled=0,
                    orders_rejected=0,
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
                    "execution_counters": {
                        "orders_recommended": 1,
                        "orders_submission_requested": 0,
                        "orders_submitted": 0,
                        "orders_filled": 0,
                        "orders_rejected": 0,
                    },
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
        submission_requested = (not bool(dry_run)) and bool(autonomous_execution_enabled)

        if not submission_requested:
            skip_reason = "dry_run_mode" if bool(dry_run) else "paper_execution_disabled"
            _emit_telemetry(
                telemetry_callback,
                "dry_run_execution_skipped",
                run_id=cycle_run_id,
                reason=skip_reason,
                symbol=selected_symbol,
                orders_recommended=1,
                orders_submission_requested=0,
                orders_submitted=0,
                orders_filled=0,
                orders_rejected=0,
            )
            _emit_notification(
                notification_callback,
                event_type="dry_run_trade_skipped",
                title="Execution Skipped",
                message=("PAPER DRY RUN mode skipped order submission." if bool(dry_run) else "Autonomous paper execution is disabled by configuration."),
                severity="INFO",
                metadata={
                    "run_id": cycle_run_id,
                    "dry_run": bool(dry_run),
                    "symbol": selected_symbol,
                    "quantum_score": float((selected_candidate.get("quantum_score") or {}).get("final_score") or selected_candidate.get("overall_score") or 0.0),
                    "strategy_id": str(selected_strategy.get("strategy_id") or ""),
                    "proposed_quantity": float(planned_order.get("quantity") or 0.0),
                    "proposed_notional": float(planned_order.get("notional") or 0.0),
                    "reason": skip_reason,
                    "orders_recommended": 1,
                    "orders_submission_requested": 0,
                    "orders_submitted": 0,
                    "orders_filled": 0,
                    "orders_rejected": 0,
                    "status": "skipped",
                },
                deduplication_key=f"dry_run_trade_skipped:{cycle_run_id}:{selected_symbol}:{skip_reason}",
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
            _emit_notification(
                notification_callback,
                event_type="paper_order_submission_requested",
                title="Paper Order Submission Requested",
                message="Execution gate passed; requesting paper order submission.",
                severity="INFO",
                metadata={
                    "run_id": cycle_run_id,
                    "dry_run": False,
                    "symbol": selected_symbol,
                    "strategy_id": str(selected_strategy.get("strategy_id") or ""),
                    "proposed_quantity": float(planned_order.get("quantity") or 0.0),
                    "proposed_notional": float(planned_order.get("notional") or 0.0),
                    "orders_recommended": 1,
                    "orders_submission_requested": 1,
                    "orders_submitted": 0,
                    "orders_filled": 0,
                    "orders_rejected": 0,
                    "status": "submission_requested",
                },
                deduplication_key=f"paper_order_submission_requested:{cycle_run_id}:{selected_symbol}:{client_order_id}",
            )
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
            response_status = str(order_response.get("status") or "").lower()
            accepted_submission_statuses = {"new", "accepted", "pending", "pending_new", "partially_filled", "filled"}
            accepted_by_broker = response_status in accepted_submission_statuses
            if accepted_by_broker:
                _emit_notification(
                    notification_callback,
                    event_type="paper_order_submitted",
                    title="Paper Order Submitted",
                    message="Accepted by Alpaca PAPER.",
                    severity="SUCCESS",
                    metadata={
                        "run_id": cycle_run_id,
                        "dry_run": False,
                        "symbol": selected_symbol,
                        "strategy_id": str(selected_strategy.get("strategy_id") or ""),
                        "proposed_quantity": float(planned_order.get("quantity") or 0.0),
                        "proposed_notional": float(planned_order.get("notional") or 0.0),
                        "orders_recommended": 1,
                        "orders_submission_requested": 1,
                        "orders_submitted": 1,
                        "orders_filled": 0,
                        "orders_rejected": 0,
                        "status": "submitted",
                    },
                    deduplication_key=f"paper_order_submitted:{cycle_run_id}:{selected_symbol}:{client_order_id}",
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
        initial_submission_status = str(order_response.get("status") or "").lower()
        accepted_submission_statuses = {"new", "accepted", "pending", "pending_new", "partially_filled", "filled"}
        accepted_by_broker = bool(submission_requested and initial_submission_status in accepted_submission_statuses)
        final_status = str(lifecycle.get("final_status") or final_order.get("status") or "unknown").lower()
        rejected_statuses = {"rejected", "failed", "expired", "submission_blocked_by_config"}
        cancelled_statuses = {"canceled", "cancelled"}
        if final_status == "filled":
            _emit_notification(
                notification_callback,
                event_type="paper_order_filled",
                title="Paper Order Filled",
                message="Paper order reached filled status.",
                severity="SUCCESS",
                metadata={
                    "run_id": cycle_run_id,
                    "dry_run": bool(dry_run),
                    "symbol": selected_symbol,
                    "strategy_id": str(selected_strategy.get("strategy_id") or ""),
                    "filled_quantity": float(final_order.get("filled_quantity") or 0.0),
                    "average_fill_price": float(final_order.get("average_fill_price") or latest_price or 0.0),
                    "status": "filled",
                },
                deduplication_key=f"paper_order_filled:{cycle_run_id}:{selected_symbol}:{client_order_id}",
            )
        if final_status == "partially_filled":
            _emit_notification(
                notification_callback,
                event_type="paper_order_partially_filled",
                title="Paper Order Partially Filled",
                message="Paper order received a partial fill.",
                severity="WARNING",
                metadata={
                    "run_id": cycle_run_id,
                    "dry_run": bool(dry_run),
                    "symbol": selected_symbol,
                    "strategy_id": str(selected_strategy.get("strategy_id") or ""),
                    "status": "partially_filled",
                },
                deduplication_key=f"paper_order_partially_filled:{cycle_run_id}:{selected_symbol}:{client_order_id}",
            )
        if final_status in cancelled_statuses:
            _emit_notification(
                notification_callback,
                event_type="paper_order_cancelled",
                title="Paper Order Cancelled",
                message="Paper order was cancelled before full fill.",
                severity="WARNING",
                metadata={
                    "run_id": cycle_run_id,
                    "dry_run": bool(dry_run),
                    "symbol": selected_symbol,
                    "strategy_id": str(selected_strategy.get("strategy_id") or ""),
                    "status": final_status,
                    "reason": str(final_order.get("rejection_reason") or final_status),
                },
                deduplication_key=f"paper_order_cancelled:{cycle_run_id}:{selected_symbol}:{client_order_id}:{final_status}",
            )
        if final_status in rejected_statuses:
            _emit_notification(
                notification_callback,
                event_type="paper_order_rejected",
                title="Paper Order Rejected",
                message="Paper order failed or was rejected by broker.",
                severity="ERROR",
                metadata={
                    "run_id": cycle_run_id,
                    "dry_run": bool(dry_run),
                    "symbol": selected_symbol,
                    "strategy_id": str(selected_strategy.get("strategy_id") or ""),
                    "status": final_status,
                    "reason": str(final_order.get("rejection_reason") or final_status),
                },
                deduplication_key=f"paper_order_rejected:{cycle_run_id}:{selected_symbol}:{client_order_id}:{final_status}",
            )
        paper_order_id = str(final_order.get("order_id") or final_order.get("id") or f"{cycle_run_id}-1")
        submission_blocked = str(final_order.get("status") or "").lower() == "submission_blocked_by_config"
        submitted_to_broker = bool(accepted_by_broker and not submission_blocked)
        orders_rejected_count = 1 if final_status in rejected_statuses else 0
        orders_filled_count = 1 if final_status == "filled" else 0
        requested_qty = float(final_order.get("requested_quantity") or planned_order.get("quantity") or 0.0)
        filled_qty = float(final_order.get("filled_quantity") or 0.0)
        fill_price = float(final_order.get("average_fill_price") or latest_price or 0.0)
        counted_filled_qty = filled_qty if final_status in {"filled", "partially_filled"} else 0.0

        expected_positions = dict(broker_pre_positions)
        if final_status in {"filled", "partially_filled"} and counted_filled_qty > 0:
            expected_positions[selected_symbol] = {
                "quantity": float((expected_positions.get(selected_symbol) or {}).get("quantity") or 0.0) + counted_filled_qty,
                "avg_price": fill_price,
            }
            if float((broker_pre_positions.get(selected_symbol) or {}).get("quantity") or 0.0) <= 0:
                _emit_notification(
                    notification_callback,
                    event_type="position_opened",
                    title="Position Opened",
                    message="A new paper position was opened.",
                    severity="SUCCESS",
                    metadata={
                        "run_id": cycle_run_id,
                        "dry_run": bool(dry_run),
                        "symbol": selected_symbol,
                        "strategy_id": str(selected_strategy.get("strategy_id") or ""),
                        "status": "position_opened",
                    },
                    deduplication_key=f"position_opened:{cycle_run_id}:{selected_symbol}:{client_order_id}",
                )

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
            # Margin buying power does not move dollar-for-dollar with cash. Position
            # quantity and settled cash remain hard reconciliation gates; buying power
            # is captured as diagnostic account state instead of guessed here.
            expected_buying_power=None,
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
        persisted_submission_status = final_status if submitted_to_broker else "not_submitted"
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
                        "submitted_order_count": 1 if submitted_to_broker else 0,
                        "filled_order_count": int(orders_filled_count),
                        "failed_order_count": 1 if final_status in {"rejected", "failed", "canceled", "cancelled", "expired", "submission_blocked_by_config"} else 0,
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
                            "submission_status": persisted_submission_status,
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

        confirmed = 1 if submitted_to_broker and str(reconciliation.get("reconciliation_status") or "").lower() in {"matched", "matched_with_tolerance"} and int(reconciliation.get("position_mismatch_count") or 0) == 0 and final_status not in {"rejected", "failed", "canceled", "cancelled", "expired"} else 0

        if str(planned_order.get("side") or "").upper() == "SELL" and final_status == "filled":
            post_qty = float((broker_post_positions.get(selected_symbol) or {}).get("quantity") or 0.0)
            if post_qty <= 0:
                _emit_notification(
                    notification_callback,
                    event_type="position_closed",
                    title="Position Closed",
                    message="Paper position was closed.",
                    severity="SUCCESS",
                    metadata={
                        "run_id": cycle_run_id,
                        "dry_run": bool(dry_run),
                        "symbol": selected_symbol,
                        "strategy_id": str(selected_strategy.get("strategy_id") or ""),
                        "status": "position_closed",
                    },
                    deduplication_key=f"position_closed:{cycle_run_id}:{selected_symbol}:{client_order_id}",
                )

        _emit_notification(
            notification_callback,
            event_type="scan_completed",
            title="Scan Completed",
            message="Scan cycle completed successfully.",
            severity="SUCCESS",
            metadata={
                "run_id": cycle_run_id,
                "dry_run": bool(dry_run),
                "status": "completed",
                "orders_recommended": 1,
                "orders_submission_requested": (1 if submission_requested else 0),
                "orders_submitted": (1 if submitted_to_broker else 0),
                "orders_filled": int(orders_filled_count),
                "orders_rejected": int(orders_rejected_count),
                "orders_attempted": (1 if submission_requested else 0),
            },
            deduplication_key=f"scan_completed:{cycle_run_id}",
        )

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
                "execution_counters": {
                    "orders_recommended": 1,
                    "orders_submission_requested": (1 if submission_requested else 0),
                    "orders_submitted": (1 if submitted_to_broker else 0),
                    "orders_filled": int(orders_filled_count),
                    "orders_rejected": int(orders_rejected_count),
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
