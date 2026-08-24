from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from continuous_scan_cycle import run_continuous_scan_cycle
from deployment_config import load_deployment_config
from logger_setup import logger
from notification_service import NotificationService, format_daily_summary_message, format_weekly_summary_message
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


def _stop_requested(stop_event: Any | None) -> bool:
    if stop_event is None:
        return False
    checker = getattr(stop_event, "is_set", None)
    if not callable(checker):
        return False
    try:
        return bool(checker())
    except Exception:
        return False


def _sleep_with_stop(
    sleep_fn: Callable[[float], None],
    sleep_seconds: float,
    stop_event: Any | None,
    *,
    chunk_seconds: float = 60.0,
) -> bool:
    remaining = max(0.0, float(sleep_seconds))
    if remaining <= 0:
        return _stop_requested(stop_event)

    if stop_event is None:
        sleep_fn(remaining)
        return False

    max_chunk = max(1.0, float(chunk_seconds))
    while remaining > 0:
        if _stop_requested(stop_event):
            return True
        window = min(remaining, max_chunk)
        try:
            sleep_fn(window)
        except InterruptedError:
            # Some runtimes interrupt sleep when a signal arrives.
            if _stop_requested(stop_event):
                return True
            continue
        remaining -= window

    return _stop_requested(stop_event)


def _result_value(result: Any, key: str, default: Any = None) -> Any:
    if isinstance(result, dict):
        return result.get(key, default)
    return getattr(result, key, default)


def _parse_summary_time_et(value: str | None) -> tuple[int, int]:
    text = str(value or "16:15").strip()
    parts = text.split(":")
    if len(parts) != 2:
        return 16, 15
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except Exception:
        return 16, 15
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return 16, 15
    return hour, minute


def _new_order_count(execution: dict[str, Any]) -> int:
    paper_order = execution.get("paper_order") or {}
    order_id = str(paper_order.get("order_id") or "").strip()
    paper_orders = execution.get("paper_orders")
    if isinstance(paper_orders, list):
        return sum(1 for item in paper_orders if str((item or {}).get("order_id") or "").strip())
    return 1 if order_id else 0


def _execution_counters(result: dict[str, Any]) -> dict[str, int]:
    execution = _result_value(result, "execution") or {}
    raw = dict(execution.get("execution_counters") or {})
    submission_requested = raw.get("orders_submission_requested")
    if submission_requested is None:
        submission_requested = raw.get("orders_attempted")
    counters = {
        "orders_recommended": int(raw.get("orders_recommended") or 0),
        "orders_submission_requested": int(submission_requested or 0),
        "orders_submitted": int(raw.get("orders_submitted") or 0),
        "orders_filled": int(raw.get("orders_filled") or 0),
        "orders_rejected": int(raw.get("orders_rejected") or 0),
    }
    # Deprecated compatibility alias for historical records.
    counters["orders_attempted"] = int(counters["orders_submission_requested"])
    return counters


def _confirmed_submitted_order_count(result: dict[str, Any]) -> int:
    if str(_result_value(result, "execution_status") or "").strip().lower() != "completed":
        return 0

    counters = _execution_counters(result)
    if counters["orders_submitted"] > 0:
        return int(counters["orders_submitted"])

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


def _read_lock_snapshot(lock_path: Path) -> dict[str, Any]:
    if not lock_path.exists():
        return {}
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    acquired_at = str(payload.get("acquired_at") or "")
    age_seconds = None
    if acquired_at:
        try:
            parsed = datetime.fromisoformat(acquired_at)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
            age_seconds = max((datetime.now(ZoneInfo("UTC")) - parsed.astimezone(ZoneInfo("UTC"))).total_seconds(), 0.0)
        except Exception:
            age_seconds = None

    return {
        "owner": str(payload.get("owner") or ""),
        "pid": int(payload.get("pid") or 0),
        "acquired_at": acquired_at,
        "age_seconds": (round(float(age_seconds), 3) if age_seconds is not None else None),
    }


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
    notifier_factory: Callable[..., NotificationService] | None = None,
    crypto_runner: Callable[..., Any] | None = None,
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
    crypto_enabled = str(os.getenv("CRYPTO_TRADING_ENABLED", "false")).strip().lower() in {"1", "true", "yes", "on"}
    crypto_interval_minutes = max(int(os.getenv("CRYPTO_SCAN_INTERVAL_MINUTES", "15")), 1)
    crypto_interval_seconds = crypto_interval_minutes * 60
    if crypto_enabled and crypto_runner is None:
        from crypto_paper_trader import run_crypto_paper_cycle

        crypto_runner = run_crypto_paper_cycle
    telemetry_callback = _telemetry_callback_factory(run_id=run_id, dry_run=dry_run, trading_mode=str(config.trading_mode))
    notifier_builder = notifier_factory or NotificationService.from_env
    notifier = notifier_builder(database_url=(database_url or config.database_url))

    def _notify(
        *,
        event_type: str,
        title: str,
        message: str,
        severity: str,
        metadata: dict[str, Any] | None = None,
        deduplication_key: str | None = None,
        deduplication_window_seconds: int | None = None,
    ) -> None:
        try:
            notifier.notify(
                event_type=event_type,
                title=title,
                message=message,
                severity=severity,
                metadata=dict(metadata or {}),
                deduplication_key=deduplication_key,
                deduplication_window_seconds=deduplication_window_seconds,
            )
        except Exception:
            return

    _notify(
        event_type="bot_started",
        title="Runner Started",
        message="Continuous paper runner started.",
        severity="SUCCESS",
        metadata={"run_id": run_id, "dry_run": bool(dry_run), "status": "starting"},
        deduplication_key=f"bot_started:{run_id}",
    )

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
        crypto_trading_enabled=bool(crypto_enabled),
        crypto_scan_interval_minutes=int(crypto_interval_minutes),
        crypto_market="24/7",
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
        "crypto_cycles_attempted": 0,
        "crypto_cycles_completed": 0,
        "crypto_cycles_failed": 0,
        "crypto_orders_submitted": 0,
    }
    last_active_cycle_key: str | None = None
    last_crypto_cycle_at: datetime | None = None
    daily_summary_enabled = str(os.getenv("NOTIFICATION_DAILY_SUMMARY_ENABLED", "true")).strip().lower() in {"1", "true", "yes", "on"}
    weekly_summary_enabled = str(os.getenv("NOTIFICATION_WEEKLY_SUMMARY_ENABLED", "true")).strip().lower() in {"1", "true", "yes", "on"}
    daily_summary_hour, daily_summary_minute = _parse_summary_time_et(os.getenv("DAILY_SUMMARY_TIME_ET", "16:15"))

    try:
        while effective_max_iterations is None or stats["cycles"] < int(effective_max_iterations):
            if _stop_requested(stop_event):
                _log_event("continuous_runner_shutdown", reason="stop_event", cycles=stats["cycles"])
                break

            now_eastern = _to_eastern(now_provider())
            market_date = now_eastern.date().isoformat()

            crypto_due = bool(
                crypto_enabled
                and callable(crypto_runner)
                and (
                    last_crypto_cycle_at is None
                    or (now_eastern - last_crypto_cycle_at).total_seconds() >= float(crypto_interval_seconds)
                )
            )
            if crypto_due:
                stats["crypto_cycles_attempted"] += 1
                try:
                    crypto_result = dict(
                        crypto_runner(
                            now=now_eastern.astimezone(timezone.utc),
                            dry_run=bool(
                                dry_run
                                or str(os.getenv("CRYPTO_DRY_RUN", "false")).strip().lower()
                                in {"1", "true", "yes", "on"}
                            ),
                        )
                        or {}
                    )
                    confirmed_crypto_orders = int(crypto_result.get("confirmed_order_count") or 0)
                    stats["crypto_cycles_completed"] += 1
                    stats["crypto_orders_submitted"] += confirmed_crypto_orders
                    _log_event(
                        "crypto_cycle_complete",
                        run_id=run_id,
                        timestamp=now_eastern.isoformat(),
                        cycle_status=str(crypto_result.get("cycle_status") or "unknown"),
                        universe_count=int(crypto_result.get("universe_count") or 0),
                        scanned_count=int(crypto_result.get("scanned_count") or 0),
                        buy_signal_count=int(crypto_result.get("buy_signal_count") or 0),
                        sell_signal_count=int(crypto_result.get("sell_signal_count") or 0),
                        confirmed_order_count=confirmed_crypto_orders,
                        action_reason=str(crypto_result.get("action_reason") or ""),
                    )
                except Exception as exc:
                    stats["crypto_cycles_failed"] += 1
                    _log_event(
                        "crypto_cycle_failed",
                        run_id=run_id,
                        timestamp=now_eastern.isoformat(),
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                    )
                finally:
                    last_crypto_cycle_at = now_eastern

            if scan_only_during_market_hours and not _is_market_open(now_eastern):
                sleep_seconds = _seconds_until_next_market_open(now_eastern)
                if crypto_enabled:
                    sleep_seconds = min(float(sleep_seconds), float(crypto_interval_seconds))
                stats["closed_market_sleeps"] += 1
                if daily_summary_enabled and ((now_eastern.hour, now_eastern.minute) >= (daily_summary_hour, daily_summary_minute)):
                    summary_message = format_daily_summary_message(
                        {
                            "date": market_date,
                            "bot_status": "market_closed_wait",
                            "scans_completed": stats["scans_completed"],
                            "failed_scans": stats["scans_failed"],
                            "orders_submitted": int((_normalize_daily_state(_load_daily_state(resolved_state_path), market_date)).get("orders_submitted") or 0),
                        }
                    )
                    _notify(
                        event_type="daily_summary",
                        title="Daily Summary",
                        message=summary_message,
                        severity="INFO",
                        metadata={"run_id": run_id, "dry_run": bool(dry_run), "status": "daily_summary", "date": market_date},
                        deduplication_key=f"daily_summary:{market_date}",
                        deduplication_window_seconds=60 * 60 * 30,
                    )
                if weekly_summary_enabled and now_eastern.weekday() == 4 and ((now_eastern.hour, now_eastern.minute) >= (daily_summary_hour, daily_summary_minute)):
                    iso_year, iso_week, _ = now_eastern.isocalendar()
                    weekly_message = format_weekly_summary_message(
                        {
                            "strategy_leaderboard": "N/A",
                            "total_paper_pl": "N/A",
                            "win_rate": "N/A",
                            "profit_factor": "N/A",
                            "maximum_drawdown": "N/A",
                            "best_strategies": "N/A",
                            "worst_strategies": "N/A",
                            "best_sectors": "N/A",
                            "worst_sectors": "N/A",
                            "factor_effectiveness": "N/A",
                            "recommendations": "N/A",
                            "strategies_recommended_for_pause": "N/A",
                            "proposed_weight_changes": "N/A",
                        }
                    )
                    _notify(
                        event_type="weekly_summary",
                        title="Weekly Summary",
                        message=weekly_message,
                        severity="INFO",
                        metadata={"run_id": run_id, "dry_run": bool(dry_run), "status": "weekly_summary", "week": f"{iso_year}-W{iso_week:02d}"},
                        deduplication_key=f"weekly_summary:{iso_year}:W{iso_week:02d}",
                        deduplication_window_seconds=60 * 60 * 24 * 8,
                    )
                _log_event(
                    "continuous_runner_market_closed",
                    run_id=run_id,
                    timestamp=now_eastern.isoformat(),
                    sleep_seconds=round(float(sleep_seconds), 3),
                )
                _notify(
                    event_type="market_closed_wait",
                    title="Market Closed",
                    message="Runner is waiting for the next market open window.",
                    severity="INFO",
                    metadata={
                        "run_id": run_id,
                        "dry_run": bool(dry_run),
                        "status": "market_closed_wait",
                        "reason": "outside_market_hours",
                        "market_date": market_date,
                    },
                    deduplication_key=f"market_closed_wait:{market_date}",
                    deduplication_window_seconds=60 * 60 * 30,
                )
                stopped_during_sleep = _sleep_with_stop(sleep_fn, sleep_seconds, stop_event)
                stats["cycles"] += 1
                if stopped_during_sleep:
                    _log_event("continuous_runner_shutdown", reason="stop_event_during_sleep", cycles=stats["cycles"])
                    break
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
                stopped_during_sleep = _sleep_with_stop(sleep_fn, scan_interval_seconds, stop_event)
                stats["cycles"] += 1
                if stopped_during_sleep:
                    _log_event("continuous_runner_shutdown", reason="stop_event_during_sleep", cycles=stats["cycles"])
                    break
                continue

            try:
                lock_acquired = False
                try:
                    with lock_factory(lock_path=lock_path, stale_after_seconds=7200, owner="continuous-paper-runner"):
                        lock_acquired = True
                        _log_event("run_lock_acquired", run_id=run_id, lock_path=str(lock_path))
                        stats["scans_attempted"] += 1
                        _notify(
                            event_type="scan_started",
                            title="Scan Started",
                            message="Universe scan cycle started.",
                            severity="INFO",
                            metadata={"run_id": run_id, "dry_run": bool(dry_run), "status": "scan_started"},
                            deduplication_key=f"scan_started:{run_id}:{stats['scans_attempted']}",
                        )
                        result = runner(
                            database_url=database_url or config.database_url,
                            persist=True,
                            dry_run=dry_run,
                            diagnostic_symbol_limit=diagnostic_symbol_limit,
                            telemetry_callback=telemetry_callback,
                            notification_callback=_notify,
                        )
                finally:
                    if lock_acquired:
                        _log_event("run_lock_released", run_id=run_id, lock_path=str(lock_path))
                confirmed_order_count = _confirmed_submitted_order_count(result)
                cycle_counters = _execution_counters(result)

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
                    orders_recommended=int(cycle_counters.get("orders_recommended") or 0),
                    orders_submission_requested=int(cycle_counters.get("orders_submission_requested") or 0),
                    orders_submitted=int(cycle_counters.get("orders_submitted") or 0),
                    orders_filled=int(cycle_counters.get("orders_filled") or 0),
                    orders_rejected=int(cycle_counters.get("orders_rejected") or 0),
                    orders_attempted=int(cycle_counters.get("orders_attempted") or 0),
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
                _notify(
                    event_type="scan_completed",
                    title="Scan Completed",
                    message="Universe scan cycle completed.",
                    severity="SUCCESS",
                    metadata={
                        "run_id": run_id,
                        "dry_run": bool(dry_run),
                        "status": str(_result_value(result, "execution_status") or "unknown"),
                        "orders_recommended": int(cycle_counters.get("orders_recommended") or 0),
                        "orders_submission_requested": int(cycle_counters.get("orders_submission_requested") or 0),
                        "orders_submitted": int(cycle_counters.get("orders_submitted") or 0),
                        "orders_filled": int(cycle_counters.get("orders_filled") or 0),
                        "orders_rejected": int(cycle_counters.get("orders_rejected") or 0),
                        "orders_attempted": int(cycle_counters.get("orders_attempted") or 0),
                    },
                    deduplication_key=f"scan_completed:{run_id}:{stats['scans_completed']}",
                )
            except RunLockBusyError:
                stats["lock_skips"] += 1
                snapshot = _read_lock_snapshot(lock_path)
                active_age_seconds = snapshot.get("age_seconds")
                busy_sleep_seconds = float(scan_interval_seconds)
                if isinstance(active_age_seconds, (int, float)) and active_age_seconds > 0:
                    # Back off when another cycle is still running to reduce lock-busy churn.
                    busy_sleep_seconds = min(max(float(active_age_seconds) / 2.0, float(scan_interval_seconds)), 900.0)
                cycle_key = "|".join(
                    [
                        str(snapshot.get("pid") or 0),
                        str(snapshot.get("acquired_at") or ""),
                        market_date,
                    ]
                )
                if cycle_key != last_active_cycle_key:
                    _log_event(
                        "scan_skipped_previous_cycle_active",
                        run_id=run_id,
                        timestamp=now_eastern.isoformat(),
                        market_date=market_date,
                        sleep_seconds=round(float(busy_sleep_seconds), 3),
                        active_run_owner=str(snapshot.get("owner") or "unknown"),
                        active_run_pid=int(snapshot.get("pid") or 0),
                        active_run_acquired_at=str(snapshot.get("acquired_at") or ""),
                        active_run_age_seconds=snapshot.get("age_seconds"),
                    )
                    last_active_cycle_key = cycle_key
                stats["cycles"] += 1
                if effective_max_iterations is not None and stats["cycles"] >= int(effective_max_iterations):
                    break
                if _sleep_with_stop(sleep_fn, busy_sleep_seconds, stop_event):
                    _log_event("continuous_runner_shutdown", reason="stop_event_during_sleep", cycles=stats["cycles"])
                    break
                continue
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
                _notify(
                    event_type="scan_failed",
                    title="Scan Failed",
                    message="Scan cycle failed before completion.",
                    severity="ERROR",
                    metadata={
                        "run_id": run_id,
                        "dry_run": bool(dry_run),
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "safe_error_message": str(exc),
                    },
                    deduplication_key=f"scan_failed:{run_id}:{stats['scans_failed']}",
                )
            stats["cycles"] += 1
            if effective_max_iterations is not None and stats["cycles"] >= int(effective_max_iterations):
                break
            if _sleep_with_stop(sleep_fn, scan_interval_seconds, stop_event):
                _log_event("continuous_runner_shutdown", reason="stop_event_during_sleep", cycles=stats["cycles"])
                break
    except KeyboardInterrupt:
        _log_event("continuous_runner_shutdown", run_id=run_id, reason="keyboard_interrupt", cycles=stats["cycles"])
        _notify(
            event_type="bot_stopped",
            title="Runner Stopped",
            message="Runner stopped by keyboard interrupt.",
            severity="WARNING",
            metadata={"run_id": run_id, "dry_run": bool(dry_run), "status": "stopped"},
            deduplication_key=f"bot_stopped:{run_id}:keyboard_interrupt",
        )
    except Exception as exc:
        _notify(
            event_type="bot_crashed",
            title="Runner Failed",
            message="Runner encountered a fatal error.",
            severity="CRITICAL",
            metadata={
                "run_id": run_id,
                "dry_run": bool(dry_run),
                "status": "crashed",
                "error_type": type(exc).__name__,
                "safe_error_message": str(exc),
                "orders_submitted": 0,
            },
            deduplication_key=f"bot_crashed:{run_id}",
        )
        notifier.close()
        raise

    _log_event("continuous_runner_exit", run_id=run_id, **stats)
    _notify(
        event_type="bot_stopped",
        title="Runner Stopped",
        message="Runner exited normally.",
        severity="INFO",
        metadata={"run_id": run_id, "dry_run": bool(dry_run), "status": "stopped", **stats},
        deduplication_key=f"bot_stopped:{run_id}:normal",
    )
    notifier.close()

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
