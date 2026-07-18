from __future__ import annotations

from typing import Any

from paper_execution_repository import MonitoringPaperExecutionRepository


def fetch_paper_validation_dashboard_payload(database_url: str | None) -> dict[str, Any]:
    repository = MonitoringPaperExecutionRepository(database_url=database_url)
    payload = {
        "db_connected": repository.db.enabled,
        "approvals": [],
        "latest_run": {},
        "latest_orders": [],
        "latest_position_snapshots": [],
        "history": [],
    }
    if not repository.db.enabled:
        return payload
    try:
        repository.db.ensure_schema()
        latest_run = repository.fetch_latest_run() or {}
        run_id = str(latest_run.get("run_id") or "")
        payload["approvals"] = repository.list_approvals(enabled_only=False)
        payload["latest_run"] = latest_run
        payload["latest_orders"] = repository.fetch_orders_for_run(run_id) if run_id else []
        payload["latest_position_snapshots"] = repository.fetch_position_snapshots_for_run(run_id) if run_id else []
        payload["history"] = repository.list_runs(limit=50)
        return payload
    finally:
        repository.close()
