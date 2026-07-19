from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from typing import Any, Callable

from config import TRADING_MODE, is_safe_mode
from daily_run_report import build_daily_run_report
from daily_run_repository import DailyRunRepository
from performance_dashboard import fetch_performance_dashboard_payload
from performance_engine import run_performance_intelligence
from sprint_10_2_execution_validation import run_sprint_10_2_execution_validation


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _execution_status(execution_result: dict[str, Any], dashboard_updated: bool) -> str:
    status = str(execution_result.get("status") or "").lower()
    if status == "completed":
        reconciliation = (execution_result.get("reconciliation") or {}).get("reconciliation_status")
        mismatch = int((execution_result.get("reconciliation") or {}).get("position_mismatch_count") or 0)
        if str(reconciliation) != "matched" or mismatch != 0:
            return "reconciliation_failed"
        if not dashboard_updated:
            return "dashboard_update_failed"
        return "completed"
    if status == "no_trade":
        return "no_candidates"
    if status == "approval_rejected":
        return "approval_denied"
    if status == "risk_rejected":
        return "risk_rejected"
    return status or "failed"


def run_daily_research_cycle(
    database_url: str | None,
    manual_approval: str = "YES",
    symbols: list[str] | None = None,
    persist: bool = True,
    execution_runner: Callable[..., dict[str, Any]] = run_sprint_10_2_execution_validation,
    performance_runner: Callable[..., dict[str, Any]] = run_performance_intelligence,
    performance_payload_loader: Callable[[str | None], dict[str, Any]] = fetch_performance_dashboard_payload,
) -> dict[str, Any]:
    if not is_safe_mode(TRADING_MODE):
        raise RuntimeError("Daily research cycle is blocked in LIVE mode")

    run_id = f"daily-run-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    started = time.perf_counter()
    timestamp = _utc_iso()

    execution_result = execution_runner(
        database_url=database_url,
        manual_approval=manual_approval,
        symbols=symbols,
        persist=persist,
    )

    performance_result = performance_runner(database_url=database_url)
    performance_payload = performance_payload_loader(database_url)
    dashboard_updated = bool(execution_result.get("dashboard_updated")) and bool((performance_payload.get("latest_run") or {}).get("run_id"))

    exec_status = _execution_status(execution_result, dashboard_updated)
    elapsed = round(time.perf_counter() - started, 6)
    report = build_daily_run_report(execution_result=execution_result, performance_result=performance_result, execution_time_seconds=elapsed)

    selected_symbols = []
    if execution_result.get("selected_symbol"):
        selected_symbols.append(str(execution_result.get("selected_symbol")))

    row = {
        "run_id": run_id,
        "timestamp": timestamp,
        "market_session": (execution_result.get("market") or {}).get("session_type"),
        "market_status": "fresh" if bool((execution_result.get("market") or {}).get("fresh")) else "stale",
        "candidate_count": int(execution_result.get("universe_size") or 0),
        "qualified_count": int(execution_result.get("qualified_securities") or 0),
        "selected_symbols": selected_symbols,
        "execution_status": exec_status,
        "performance_run_id": (performance_result.get("run_id") if isinstance(performance_result, dict) else None) or str((performance_payload.get("latest_run") or {}).get("run_id") or ""),
        "paper_validation_run_id": str((execution_result.get("dashboard_payload") or {}).get("latest_run", {}).get("run_id") or ""),
        "report": report,
        "created_at": timestamp,
        "updated_at": _utc_iso(),
    }

    persistence = {"storage": "disabled", "run_id": run_id}
    dashboard_payload = {"db_connected": False, "latest_run": {}, "history": []}
    if persist:
        repo = DailyRunRepository(database_url=database_url)
        try:
            persistence = repo.save_run(row)
            dashboard_payload = repo.dashboard_payload()
        finally:
            repo.close()

    return {
        "run_id": run_id,
        "timestamp": timestamp,
        "execution_status": exec_status,
        "execution": execution_result,
        "performance": performance_result,
        "report": report,
        "dashboard_updated": dashboard_updated,
        "persistence": persistence,
        "daily_dashboard_payload": dashboard_payload,
        "execution_time_seconds": elapsed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Autonomous Daily Research Cycle runner")
    parser.add_argument("--database-url", default="sqlite:///sprint10_2_validation_run2.db")
    parser.add_argument("--manual-approval", default="YES")
    parser.add_argument("--symbols", default="")
    args = parser.parse_args()

    symbols = [part.strip().upper() for part in str(args.symbols or "").split(",") if part.strip()]
    result = run_daily_research_cycle(
        database_url=args.database_url,
        manual_approval=args.manual_approval,
        symbols=symbols or None,
        persist=True,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
