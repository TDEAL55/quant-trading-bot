from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from deployment_config import DeploymentConfigError, load_deployment_config
from daily_research_runner import run_daily_research_cycle
from notification_service import NotificationService
from run_lock import DailyRunLock, RunLockBusyError
from sprint_10_2_execution_validation import _market_snapshot


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _result(status: str, **fields: Any) -> dict[str, Any]:
    payload = {"status": status, "timestamp": _utc_iso()}
    payload.update(fields)
    return payload


def _execution_status(result: dict[str, Any]) -> str:
    execution = result.get("execution") or {}
    risk = execution.get("risk_result") or {}
    checks = risk.get("checks") or {}
    reconciliation = execution.get("reconciliation") or {}
    if result.get("execution_status") == "no_candidates":
        return "no_candidates"
    if not risk:
        return "failed"
    if checks.get("duplicate_protection") is False:
        return "duplicate_rejected"
    if risk.get("approved") is False:
        return "risk_rejected"
    if str(reconciliation.get("reconciliation_status") or "").lower() != "matched":
        return "failed"
    if int(reconciliation.get("position_mismatch_count") or 0) != 0:
        return "failed"
    if not result.get("dashboard_updated"):
        return "failed"
    return "completed"


def _new_order_count(execution: dict[str, Any]) -> int:
    paper_order = execution.get("paper_order") or {}
    order_id = str(paper_order.get("order_id") or "").strip()
    paper_orders = execution.get("paper_orders")
    if isinstance(paper_orders, list):
        return sum(1 for item in paper_orders if str((item or {}).get("order_id") or "").strip())
    return 1 if order_id else 0


def run_unattended_daily_cycle(
    database_url: str | None = None,
    config_loader: Callable[[], Any] = load_deployment_config,
    runner: Callable[..., dict[str, Any]] = run_daily_research_cycle,
    market_snapshot_loader: Callable[[], dict[str, Any]] = _market_snapshot,
    lock_factory: Callable[..., DailyRunLock] = DailyRunLock,
    notification_service_factory: Callable[..., NotificationService] = NotificationService,
) -> dict[str, Any]:
    try:
        config = config_loader()
    except DeploymentConfigError as exc:
        return _result("failed", error=str(exc))

    if config.kill_switch:
        return _result("killed", error="kill switch enabled")

    if config.trading_mode != "PAPER":
        return _result("failed", error="TRADING_MODE must be PAPER")

    if not config.auto_approve_paper:
        return _result("auto_approval_disabled", error="AUTO_APPROVE_PAPER=false")

    lock_path = Path(config.database_path).with_suffix(".daily.lock")
    try:
        with lock_factory(lock_path=lock_path, stale_after_seconds=7200, owner="unattended-daily-run"):
            market = market_snapshot_loader()
            session_type = str(market.get("session_type") or "latest_completed_session").strip().lower()
            valid_session = session_type in {"today", "latest_completed_session"}
            age_days = market.get("age_days")
            if (not market.get("fresh")) or market.get("stale") or (age_days is not None and int(age_days) > 3) or (not valid_session):
                return _result("stale_market_data", market=market)

            result = runner(
                database_url=database_url or config.database_url,
                manual_approval="YES",
                symbols=["JPM", "MSFT", "AAPL"],
                persist=True,
            )

            execution = result.get("execution") or {}
            paper_order = execution.get("paper_order") or {}
            order_count = _new_order_count(execution)
            allowed_orders = min(int(config.max_daily_orders), 1)
            if order_count > allowed_orders:
                return _result("failed", error="more than one order was created")

            status = _execution_status(result)
            if status == "completed":
                status = "completed"
            elif status == "duplicate_rejected":
                status = "duplicate_rejected"
            elif status == "risk_rejected":
                status = "risk_rejected"
            elif status == "no_candidates":
                status = "no_candidates"
            else:
                status = "failed"

            notification_status = "skipped"
            if config.notifications_enabled:
                try:
                    notifier = notification_service_factory(output="console")
                    notifier.send(
                        {
                            "run_status": status,
                            "selected_symbol": execution.get("selected_symbol"),
                            "score": execution.get("overall_score"),
                            "confidence": execution.get("confidence"),
                            "risk_result": execution.get("risk_result"),
                            "order_fill": paper_order,
                            "reconciliation": execution.get("reconciliation"),
                            "portfolio_value": (result.get("performance") or {}).get("metrics", {}).get("portfolio_value"),
                            "dashboard_update": result.get("dashboard_updated"),
                        }
                    )
                    notification_status = "sent"
                except Exception:
                    notification_status = "failed"

            return _result(
                status,
                run=result,
                execution=result.get("execution") or {},
                performance=result.get("performance") or {},
                database_row_persisted=bool((result.get("persistence") or {}).get("run_id")),
                notification_status=notification_status,
                order_count=order_count,
                market=market,
            )
    except RunLockBusyError:
        return _result("failed", error="daily run lock is already held")
    except RuntimeError as exc:
        message = str(exc)
        if "market data" in message.lower() or "freshness" in message.lower():
            return _result("stale_market_data", error=message)
        return _result("failed", error=message)
    except Exception as exc:
        return _result("failed", error=f"{type(exc).__name__}: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Unattended paper deployment runner")
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()

    result = run_unattended_daily_cycle(database_url=args.database_url)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0 if result.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
