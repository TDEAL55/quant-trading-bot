from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from continuous_scan_cycle import run_continuous_scan_cycle
from deployment_config import load_deployment_config
from logger_setup import logger
from run_lock import DailyRunLock, RunLockBusyError


EASTERN_TZ = ZoneInfo("America/New_York")
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 30
MARKET_CLOSE_HOUR = 16
MARKET_CLOSE_MINUTE = 0


def _log_event(event: str, **fields: Any) -> None:
    payload = {"event": event, "timestamp": datetime.now(EASTERN_TZ).isoformat(), **fields}
    encoded = json.dumps(payload, sort_keys=True, default=str)
    print(encoded, flush=True)
    logger.info(encoded)
    jsonl_path = Path(str(os.getenv("SCANNER_JSONL_LOG_FILE", "full_universe_dry_scan.jsonl")).strip() or "full_universe_dry_scan.jsonl")
    try:
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with open(jsonl_path, "a", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
    except Exception:
        pass
    try:
        sys.stdout.flush()
    except Exception:
        pass


def _telemetry_callback_factory(run_id: str, dry_run: bool, trading_mode: str) -> Callable[[str, dict[str, Any]], None]:
    def _emit(event: str, payload: dict[str, Any]) -> None:
        fields = dict(payload or {})
        fields.setdefault("run_id", run_id)
        fields.setdefault("dry_run", bool(dry_run))
        fields.setdefault("trading_mode", str(trading_mode).upper())
        _log_event(event, **fields)

    return _emit


def build_full_universe_dry_run_command(log_file: str = "full_universe_dry_scan.log") -> str:
    return (
        "set -o pipefail; "
        "SCAN_ONLY_DURING_MARKET_HOURS=false "
        "SCANNER_MAX_UNIVERSE_SIZE=0 "
        "timeout 600 python -u continuous_paper_runner.py --dry-run --max-iterations 1 "
        f"2>&1 | tee {log_file}; "
        "exit_code=${PIPESTATUS[0]}; "
        "echo PYTHON_EXIT_CODE=${exit_code}; "
        "exit ${exit_code}"
    )


def _to_eastern(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=EASTERN_TZ)
    return dt.astimezone(EASTERN_TZ)


def _is_market_open(now_eastern: datetime) -> bool:
    if now_eastern.weekday() >= 5:
        return False
    current = (now_eastern.hour, now_eastern.minute)
    market_open = (MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE)
    market_close = (MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE)
    return market_open <= current < market_close


def _next_market_open(now_eastern: datetime) -> datetime:
    target = now_eastern.replace(
        hour=MARKET_OPEN_HOUR,
        minute=MARKET_OPEN_MINUTE,
        second=0,
        microsecond=0,
    )
    if target <= now_eastern:
        target = target + timedelta(days=1)
    while target.weekday() >= 5:
        target = target + timedelta(days=1)
    return target


def _seconds_until_next_market_open(now_eastern: datetime) -> float:
    target = _next_market_open(now_eastern)
    return max(0.0, (target - now_eastern).total_seconds())


def _result_value(result: Any, key: str, default: Any = None) -> Any:
    if isinstance(result, dict):
        return result.get(key, default)
    return getattr(result, key, default)


def _new_order_count(execution: dict[str, Any]) -> int:
    paper_order = execution.get("paper_order") or {}
    order_id = str(paper_order.get("order_id") or "").strip()
    paper_orders = execution.get("paper_orders")
    if isinstance(paper_orders, list):
        return sum(1 for item in paper_orders if str((item or {}).get("order_id") or "").strip())
    return 1 if order_id else 0


def _confirmed_submitted_order_count(result: dict[str, Any]) -> int:
    if str(_result_value(result, "execution_status") or "").strip().lower() != "completed":
        return 0

    execution = _result_value(result, "execution") or {}
    risk = execution.get("risk_result") or {}
    checks = risk.get("checks") or {}
    reconciliation = execution.get("reconciliation") or {}

    if risk.get("approved") is not True:
        return 0
    if checks.get("duplicate_protection") is not True:
        return 0
    if str(reconciliation.get("reconciliation_status") or "").strip().lower() != "matched":
        return 0
    if int(reconciliation.get("position_mismatch_count") or 0) != 0:
        return 0

    return _new_order_count(execution)


def _load_daily_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    except Exception:
        return {}
    return {}


def _save_daily_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(state, sort_keys=True) + "\n"
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass


def _normalize_daily_state(state: dict[str, Any], market_date: str) -> dict[str, Any]:
    if str(state.get("market_date") or "") != market_date:
        return {"market_date": market_date, "orders_submitted": 0}
    orders_submitted = int(state.get("orders_submitted") or 0)
    return {"market_date": market_date, "orders_submitted": max(0, orders_submitted)}


def run_continuous_paper_runner(
    database_url: str | None = None,
    config_loader: Callable[[], Any] = load_deployment_config,
    runner: Callable[..., Any] = run_continuous_scan_cycle,
    now_provider: Callable[[], datetime] = lambda: datetime.now(EASTERN_TZ),
    sleep_fn: Callable[[float], None] = time.sleep,
    lock_factory: Callable[..., DailyRunLock] = DailyRunLock,
    state_path: str | Path | None = None,
    max_iterations: int | None = None,
    stop_event: Any | None = None,
    max_cycles: int | None = None,
    dry_run_override: bool | None = None,
    diagnostic_symbol_limit: int | None = None,
) -> dict[str, int]:
    run_id = f"continuous-runner-{datetime.now(EASTERN_TZ).strftime('%Y%m%d%H%M%S%f')}-{uuid.uuid4().hex[:8]}"
    _log_event("continuous_runner_starting", run_id=run_id)
    config = config_loader()
    if str(config.trading_mode).upper() != "PAPER":
        raise RuntimeError("continuous_paper_runner requires TRADING_MODE=PAPER")

    effective_max_iterations = max_iterations if max_iterations is not None else max_cycles
    if effective_max_iterations is not None and int(effective_max_iterations) < 1:
        raise ValueError("max_iterations must be at least 1 when provided")

    scan_interval_seconds = int(config.scan_interval_minutes) * 60
    if scan_interval_seconds <= 0:
        raise ValueError("SCAN_INTERVAL_MINUTES must be at least 1")

    configured_max_daily_orders = int(config.max_daily_orders)
    if configured_max_daily_orders < 1:
        raise ValueError("MAX_DAILY_ORDERS must be at least 1")

    max_daily_orders = configured_max_daily_orders
    scan_only_during_market_hours = bool(getattr(config, "scan_only_during_market_hours", True))
    dry_run = bool(getattr(config, "continuous_runner_dry_run", False)) if dry_run_override is None else bool(dry_run_override)
    telemetry_callback = _telemetry_callback_factory(run_id=run_id, dry_run=dry_run, trading_mode=str(config.trading_mode))

    max_universe_raw = os.getenv("SCANNER_MAX_UNIVERSE_SIZE")
    resolved_max_universe_size = int(str(max_universe_raw or "0").strip() or "0")
    max_universe_source = "environment" if max_universe_raw is not None else "default"

    _log_event(
        "configuration_loaded",
        run_id=run_id,
        dry_run=bool(dry_run),
        trading_mode=str(config.trading_mode).upper(),
        scan_only_during_market_hours=bool(scan_only_during_market_hours),
        scan_interval_minutes=int(config.scan_interval_minutes),
        max_universe_size=int(resolved_max_universe_size),
        max_universe_mode=("unlimited" if int(resolved_max_universe_size) <= 0 else "capped"),
        max_universe_source=max_universe_source,
        universe_source="alpaca_assets_api",
        diagnostic_symbol_limit=(int(diagnostic_symbol_limit) if diagnostic_symbol_limit is not None else None),
    )

    lock_path = Path(config.database_path).with_suffix(".continuous.lock")
    resolved_state_path = Path(state_path) if state_path is not None else Path(config.database_path).with_suffix(".continuous.state.json")

    stats = {
        "cycles": 0,
        "scans_attempted": 0,
        "scans_completed": 0,
        "scans_failed": 0,
        "lock_skips": 0,
        "quota_skips": 0,
        "closed_market_sleeps": 0,
    }

    try:
        while effective_max_iterations is None or stats["cycles"] < int(effective_max_iterations):
            if stop_event is not None and callable(getattr(stop_event, "is_set", None)) and stop_event.is_set():
                _log_event("continuous_runner_shutdown", reason="stop_event", cycles=stats["cycles"])
                break

            now_eastern = _to_eastern(now_provider())
            market_date = now_eastern.date().isoformat()

            if scan_only_during_market_hours and not _is_market_open(now_eastern):
                sleep_seconds = _seconds_until_next_market_open(now_eastern)
                stats["closed_market_sleeps"] += 1
                _log_event(
                    "continuous_runner_market_closed",
                    run_id=run_id,
                    timestamp=now_eastern.isoformat(),
                    sleep_seconds=round(float(sleep_seconds), 3),
                )
                sleep_fn(sleep_seconds)
                stats["cycles"] += 1
                continue

            state = _normalize_daily_state(_load_daily_state(resolved_state_path), market_date)
            if int(state.get("orders_submitted") or 0) >= max_daily_orders:
                stats["quota_skips"] += 1
                _log_event(
                    "continuous_runner_quota_reached",
                    run_id=run_id,
                    timestamp=now_eastern.isoformat(),
                    market_date=market_date,
                    orders_submitted=state.get("orders_submitted"),
                    max_daily_orders=max_daily_orders,
                    sleep_seconds=scan_interval_seconds,
                )
                sleep_fn(scan_interval_seconds)
                stats["cycles"] += 1
                continue

            try:
                lock_acquired = False
                try:
                    with lock_factory(lock_path=lock_path, stale_after_seconds=7200, owner="continuous-paper-runner"):
                        lock_acquired = True
                        _log_event("run_lock_acquired", run_id=run_id, lock_path=str(lock_path))
                        stats["scans_attempted"] += 1
                        result = runner(
                            database_url=database_url or config.database_url,
                            persist=True,
                            dry_run=dry_run,
                            diagnostic_symbol_limit=diagnostic_symbol_limit,
                            telemetry_callback=telemetry_callback,
                        )
                finally:
                    if lock_acquired:
                        _log_event("run_lock_released", run_id=run_id, lock_path=str(lock_path))
                confirmed_order_count = _confirmed_submitted_order_count(result)

                if confirmed_order_count > 0:
                    state["orders_submitted"] = int(state.get("orders_submitted") or 0) + int(confirmed_order_count)
                    _save_daily_state(resolved_state_path, state)

                stats["scans_completed"] += 1
                scan_payload = _result_value(result, "scan") or {}
                scan_summary = ((_result_value(scan_payload, "scan_payload") or {}).get("summary") or {}) if isinstance(scan_payload, dict) else {}
                ranked_candidates = ((_result_value(scan_payload, "scan_payload") or {}).get("ranked_candidates") or []) if isinstance(scan_payload, dict) else []
                top_10 = []
                for item in list(ranked_candidates)[:10]:
                    top_10.append(
                        {
                            "symbol": str(item.get("symbol") or ""),
                            "rank": int(item.get("rank") or 0),
                            "quantum_score": float((item.get("quantum_score") or {}).get("final_score") or item.get("overall_score") or 0.0),
                            "strategy_score": float(item.get("ranking_score") or 0.0),
                            "strategy_ids": sorted([str(key) for key in (item.get("strategy_specific_scores") or {}).keys()]),
                            "risk_reward": float((item.get("quantum_score") or {}).get("reward_risk_ratio") or 0.0),
                            "liquidity": float(item.get("liquidity_score") or 0.0),
                            "data_quality": str(((item.get("data_quality") or {}).get("quantum") or {}).get("status") or "unknown"),
                            "rejection_reasons": list(item.get("rejection_reasons") or []),
                        }
                    )

                _log_event(
                    "full_universe_scan_complete",
                    run_id=run_id,
                    total_universe=int(scan_summary.get("universe_total_count") or 0),
                    stage_b_survivors=int(scan_summary.get("stage_b_survivors") or 0),
                    stage_c_survivors=int(scan_summary.get("stage_c_survivors") or 0),
                    deep_scored_count=int(scan_summary.get("deep_scored_count") or 0),
                    eligible_candidates=int(scan_summary.get("eligible_count") or 0),
                    failed_symbols=int(scan_summary.get("failed_symbol_count") or 0),
                    timeout_count=int(scan_summary.get("timeout_count") or 0),
                    rate_limit_count=int(scan_summary.get("rate_limit_retry_count") or 0),
                    top_10_candidates=top_10,
                    total_duration=float(scan_summary.get("duration_seconds") or 0.0),
                    orders_attempted=(1 if str(_result_value(result, "execution_status") or "") in {"completed", "no_trade", "risk_rejected", "duplicate_rejected"} else 0),
                    orders_submitted=int(confirmed_order_count),
                    exit_status=str(scan_summary.get("status") or _result_value(result, "execution_status") or "unknown"),
                )

                _log_event(
                    "continuous_runner_scan_completed",
                    run_id=run_id,
                    timestamp=now_eastern.isoformat(),
                    market_date=market_date,
                    execution_status=_result_value(result, "execution_status"),
                    confirmed_order_count=confirmed_order_count,
                    orders_submitted=state.get("orders_submitted"),
                )
            except RunLockBusyError:
                stats["lock_skips"] += 1
                _log_event(
                    "continuous_runner_lock_busy",
                    run_id=run_id,
                    timestamp=now_eastern.isoformat(),
                    market_date=market_date,
                    sleep_seconds=scan_interval_seconds,
                )
            except Exception as exc:
                stats["scans_failed"] += 1
                _log_event(
                    "continuous_runner_scan_failed",
                    run_id=run_id,
                    timestamp=now_eastern.isoformat(),
                    market_date=market_date,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
            stats["cycles"] += 1
            if effective_max_iterations is not None and stats["cycles"] >= int(effective_max_iterations):
                break
            sleep_fn(scan_interval_seconds)
    except KeyboardInterrupt:
        _log_event("continuous_runner_shutdown", run_id=run_id, reason="keyboard_interrupt", cycles=stats["cycles"])

    _log_event("continuous_runner_exit", run_id=run_id, **stats)

    return stats


class _SignalStopEvent:
    def __init__(self) -> None:
        self._stop = False

    def is_set(self) -> bool:
        return bool(self._stop)

    def set(self) -> None:
        self._stop = True


def main() -> int:
    parser = argparse.ArgumentParser(description="Continuous paper trading runner")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--max-iterations", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true", help="Force dry-run mode for continuous 5-minute scans")
    parser.add_argument("--diagnostic-symbol-limit", type=int, default=None, help="Use real Alpaca universe but only fully evaluate a deterministic subset")
    args = parser.parse_args()

    stop_event = _SignalStopEvent()

    def _signal_handler(_signum, _frame):
        _log_event("continuous_runner_shutdown_signal", signal=_signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    run_continuous_paper_runner(
        database_url=args.database_url,
        max_iterations=args.max_iterations,
        stop_event=stop_event,
        dry_run_override=True if args.dry_run else None,
        diagnostic_symbol_limit=args.diagnostic_symbol_limit,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())