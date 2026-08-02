from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
import tempfile
from typing import Any, Callable
from zoneinfo import ZoneInfo

from config import DISCORD_NO_TRADE_NOTIFICATION_MINUTES
from daily_research_runner import run_daily_research_cycle
from deployment_config import DeploymentConfigError, load_deployment_config
from discord_notifier import DiscordNotifier
from run_lock import DailyRunLock, RunLockBusyError


EASTERN_TZ = ZoneInfo("America/New_York")
STATE_DEFAULT_PATH = Path(".discord_cycle_state.json")


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat()


def _result(status: str, **fields: Any) -> dict[str, Any]:
    payload = {"status": status, "timestamp": _utc_iso()}
    payload.update(fields)
    return payload


def _market_is_open(now_eastern: datetime) -> bool:
    if now_eastern.weekday() >= 5:
        return False
    current = (now_eastern.hour, now_eastern.minute)
    return (9, 30) <= current < (16, 0)


def _classify_cycle_status(summary: dict[str, Any]) -> str:
    cycle_status = str(summary.get("cycle_status") or "").upper()
    if cycle_status in {"ENTRY_CANDIDATES_PROCESSED", "MONITOR_ONLY_POSITION_CAP"}:
        return "completed"
    if cycle_status == "FAILED_PRECHECK":
        return "integrity_failed"
    return "failed"


def _load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(default)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), suffix=".tmp", delete=False) as handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.replace(str(temp_path), str(path))
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)


def _rate_limit_no_trade(now_utc: datetime, state_path: Path, minutes: int) -> bool:
    if minutes <= 0:
        return True

    state = _load_json(state_path, {})
    last_sent = str(state.get("last_no_trade_notification_utc") or "").strip()
    if last_sent:
        try:
            parsed = datetime.fromisoformat(last_sent)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            parsed = parsed.astimezone(UTC)
            if now_utc < parsed + timedelta(minutes=minutes):
                return False
        except Exception:
            pass

    state["last_no_trade_notification_utc"] = now_utc.isoformat()
    _atomic_write_json(state_path, state)
    return True


def _event_id(prefix: str, cycle_id: str, index: int = 0) -> str:
    return f"{prefix}:{cycle_id}:{index}"


def _safe_alert(notifier: DiscordNotifier, event: str, event_id: str, **fields: Any) -> bool:
    try:
        return bool(notifier.send_alert(event, event_id, **fields))
    except Exception:
        return False


def _send_cycle_notifications(
    summary: dict[str, Any],
    notifier: DiscordNotifier,
    state_path: Path,
    now_utc: datetime,
    no_trade_minutes: int,
) -> dict[str, int]:
    sent = 0
    failed = 0

    cycle_id = str(summary.get("cycle_id") or "unknown-cycle")
    monitor_results = list(summary.get("monitor_results") or [])
    entry_results = list(summary.get("entry_results") or [])

    for idx, row in enumerate(entry_results):
        if str(row.get("execution_status") or "") != "FILLED":
            continue
        ok = _safe_alert(
            notifier,
            "paper_trade_opened",
            _event_id("paper_open", cycle_id, idx),
            symbol=row.get("symbol"),
            quantity=row.get("quantity"),
            fill_price=row.get("simulated_fill_price"),
            order_fingerprint=row.get("order_fingerprint"),
        )
        sent += int(ok)
        failed += int(not ok)

    for idx, row in enumerate(monitor_results):
        status = str(row.get("monitoring_status") or "")
        if status not in {"EXITED_STOP", "EXITED_TARGET"}:
            continue

        close_ok = _safe_alert(
            notifier,
            "paper_trade_closed",
            _event_id("paper_close", cycle_id, idx),
            symbol=row.get("symbol"),
            quantity=row.get("quantity_before"),
            exit_price=row.get("simulated_exit_price"),
            realized_profit_loss=row.get("realized_profit_loss"),
            realized_return_percentage=row.get("realized_return_percentage"),
            exit_fingerprint=row.get("exit_fingerprint"),
        )
        sent += int(close_ok)
        failed += int(not close_ok)

        trigger_ok = _safe_alert(
            notifier,
            "paper_exit_triggered",
            _event_id("paper_trigger", cycle_id, idx),
            symbol=row.get("symbol"),
            trigger=status,
            stop_price=row.get("stop_price"),
            target_price=row.get("target_price"),
        )
        sent += int(trigger_ok)
        failed += int(not trigger_ok)

    candidates = int(summary.get("candidates_selected") or 0)
    scanner_executed = bool(summary.get("scanner_executed"))
    if scanner_executed and candidates > 0:
        ok = _safe_alert(
            notifier,
            "scan_completed_with_candidates",
            _event_id("scan_candidates", cycle_id),
            symbols_scanned=summary.get("symbols_scanned"),
            candidates_selected=summary.get("candidates_selected"),
            tickets_generated=summary.get("tickets_generated"),
            strategy_identifiers=summary.get("strategy_identifiers"),
        )
        sent += int(ok)
        failed += int(not ok)
    elif scanner_executed and candidates == 0:
        if _rate_limit_no_trade(now_utc=now_utc, state_path=state_path, minutes=no_trade_minutes):
            ok = _safe_alert(
                notifier,
                "scan_completed_no_trade",
                _event_id("scan_no_trade", cycle_id),
                symbols_scanned=summary.get("symbols_scanned"),
                candidates_selected=summary.get("candidates_selected"),
                decision_reason=summary.get("decision_reason"),
            )
            sent += int(ok)
            failed += int(not ok)

    stop_exits = int(summary.get("stop_exits") or 0)
    target_exits = int(summary.get("target_exits") or 0)
    if stop_exits > 0 or target_exits > 0:
        ok = _safe_alert(
            notifier,
            "realized_pnl_update",
            _event_id("realized_pnl", cycle_id),
            stop_exits=stop_exits,
            target_exits=target_exits,
            ending_cash=summary.get("ending_cash"),
            ending_equity=summary.get("ending_equity"),
        )
        sent += int(ok)
        failed += int(not ok)

    return {"sent": sent, "failed": failed}


def _summary_from_daily_research(result: dict[str, Any], symbols: list[str]) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    execution = dict(result.get("execution") or {})
    paper_order = dict(execution.get("paper_order") or {})
    selected_symbol = str(execution.get("selected_symbol") or paper_order.get("symbol") or "")
    order_status = str(paper_order.get("status") or "").upper()
    filled_qty = float(paper_order.get("shares") or 0.0)
    fill_price = float(paper_order.get("fill_price") or 0.0)
    run_id = str(result.get("run_id") or execution.get("run_id") or "daily-run")

    entry_result = {
        "symbol": selected_symbol,
        "execution_status": "FILLED" if order_status in {"FILLED", "PARTIALLY_FILLED"} and filled_qty > 0 else order_status,
        "quantity": filled_qty,
        "simulated_fill_price": fill_price,
        "order_fingerprint": str(execution.get("risk_result", {}).get("execution_fingerprint") or execution.get("paper_order", {}).get("client_order_id") or ""),
    }

    summary = {
        "cycle_id": run_id,
        "cycle_status": "ENTRY_CANDIDATES_PROCESSED" if str(result.get("execution_status") or "") == "completed" else "FAILED_PRECHECK",
        "scanner_executed": True,
        "symbols_scanned": len(symbols),
        "candidates_selected": int(execution.get("qualified_securities") or 0),
        "tickets_generated": 1 if selected_symbol else 0,
        "strategy_identifiers": ["strategy_id:sprint_10_2_execution_validation"],
        "entry_results": [entry_result] if selected_symbol else [],
        "monitor_results": [],
        "stop_exits": 0,
        "target_exits": 0,
        "ending_cash": execution.get("cash_after"),
        "ending_equity": execution.get("cash_after"),
        "errors": [] if str(result.get("execution_status") or "") == "completed" else [str(result.get("execution_status") or "failed")],
        "decision_reason": str(result.get("execution_status") or ""),
    }
    return summary, [{"stage": "daily_research", "status": str(result.get("execution_status") or "unknown")}], {"total_cycles": len(result.get("daily_dashboard_payload", {}).get("history") or [])}


def run_unattended_daily_cycle(
    database_url: str | None = None,
    config_loader: Callable[[], Any] = load_deployment_config,
    cycle_runner: Callable[..., Any] = run_daily_research_cycle,
    lock_factory: Callable[..., DailyRunLock] = DailyRunLock,
    notifier_factory: Callable[[], DiscordNotifier] = DiscordNotifier.from_env,
) -> dict[str, Any]:
    del database_url

    try:
        config = config_loader()
    except DeploymentConfigError as exc:
        return _result("failed", error=str(exc))

    if config.kill_switch:
        return _result("killed", error="kill switch enabled")

    if str(config.trading_mode).upper() != "PAPER":
        return _result("failed", error="TRADING_MODE must be PAPER")

    if not bool(config.auto_approve_paper):
        return _result("auto_approval_disabled", error="AUTO_APPROVE_PAPER=false")

    now_et = datetime.now(EASTERN_TZ)
    if not _market_is_open(now_et):
        return _result("market_closed", market_time_et=now_et.isoformat())

    lock_path = Path(config.database_path).with_suffix(".daily.lock")
    notify_state_path = Path(os.getenv("DISCORD_CYCLE_STATE_PATH", str(STATE_DEFAULT_PATH)))
    notification_counts = {"sent": 0, "failed": 0}

    try:
        with lock_factory(lock_path=lock_path, stale_after_seconds=7200, owner="unattended-daily-run"):
            notifier = notifier_factory()
            cycle_started = _utc_iso()
            startup_ok = _safe_alert(
                notifier,
                "bot_startup",
                f"bot_startup:{cycle_started}",
                trading_mode="PAPER",
                market_time_et=now_et.isoformat(),
            )
            notification_counts["sent"] += int(startup_ok)
            notification_counts["failed"] += int(not startup_ok)

            configured_symbols = [str(item).upper() for item in list(getattr(config, "scan_symbols", ("JPM", "MSFT", "AAPL"))) if str(item).strip()]
            try:
                try:
                    cycle_output = cycle_runner(
                        database_url=getattr(config, "database_url", None),
                        manual_approval="YES",
                        symbols=configured_symbols,
                        persist=True,
                    )
                except TypeError:
                    cycle_output = cycle_runner()
                if isinstance(cycle_output, tuple) and len(cycle_output) == 3:
                    summary, stage_rows, history = cycle_output
                elif isinstance(cycle_output, dict):
                    summary, stage_rows, history = _summary_from_daily_research(cycle_output, configured_symbols)
                else:
                    raise RuntimeError("cycle runner returned unsupported payload")
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                lowered = message.lower()
                is_data_error = "market data" in lowered or "download" in lowered or "yfinance" in lowered

                error_ok = _safe_alert(
                    notifier,
                    "unhandled_cycle_error",
                    f"cycle_error:{cycle_started}",
                    error_type=type(exc).__name__,
                    error_message=message,
                )
                notification_counts["sent"] += int(error_ok)
                notification_counts["failed"] += int(not error_ok)

                if is_data_error:
                    data_ok = _safe_alert(
                        notifier,
                        "data_source_failure",
                        f"data_failure:{cycle_started}",
                        error_message=message,
                    )
                    notification_counts["sent"] += int(data_ok)
                    notification_counts["failed"] += int(not data_ok)

                shutdown_ok = _safe_alert(
                    notifier,
                    "bot_shutdown",
                    f"bot_shutdown_failed:{cycle_started}",
                    run_status="failed",
                )
                notification_counts["sent"] += int(shutdown_ok)
                notification_counts["failed"] += int(not shutdown_ok)
                return _result("failed", error=message, notification=notification_counts)

            status = _classify_cycle_status(summary)
            if status == "integrity_failed":
                integrity_ok = _safe_alert(
                    notifier,
                    "integrity_failure",
                    f"integrity_failure:{summary.get('cycle_id')}",
                    cycle_status=summary.get("cycle_status"),
                    errors=summary.get("errors"),
                )
                notification_counts["sent"] += int(integrity_ok)
                notification_counts["failed"] += int(not integrity_ok)

            cycle_notifications = _send_cycle_notifications(
                summary=summary,
                notifier=notifier,
                state_path=notify_state_path,
                now_utc=datetime.now(UTC),
                no_trade_minutes=int(DISCORD_NO_TRADE_NOTIFICATION_MINUTES),
            )
            notification_counts["sent"] += cycle_notifications["sent"]
            notification_counts["failed"] += cycle_notifications["failed"]

            shutdown_ok = _safe_alert(
                notifier,
                "bot_shutdown",
                f"bot_shutdown:{summary.get('cycle_id')}",
                run_status=status,
                cycle_id=summary.get("cycle_id"),
            )
            notification_counts["sent"] += int(shutdown_ok)
            notification_counts["failed"] += int(not shutdown_ok)

            return _result(
                status,
                cycle=summary,
                stage_rows=stage_rows,
                history_total_cycles=int((history or {}).get("total_cycles") or 0),
                notification=notification_counts,
            )
    except RunLockBusyError:
        return _result("failed", error="daily run lock is already held")


def main() -> int:
    parser = argparse.ArgumentParser(description="Unattended paper deployment runner")
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()

    result = run_unattended_daily_cycle(database_url=args.database_url)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0 if result.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
