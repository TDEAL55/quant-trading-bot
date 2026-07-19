from __future__ import annotations

from typing import Any

from performance_repository import PerformanceRepository
from performance_report import build_daily_performance_report, build_monthly_summary, build_weekly_summary


def fetch_performance_dashboard_payload(database_url: str | None) -> dict[str, Any]:
    repository = PerformanceRepository(database_url=database_url)
    payload = {
        "db_connected": repository.db.enabled,
        "latest_run": {},
        "daily_equity": [],
        "trade_statistics": [],
        "portfolio_snapshots": [],
        "metrics": [],
        "metrics_map": {},
        "daily_report": {},
        "weekly_summary": [],
        "monthly_summary": [],
    }
    if not repository.db.enabled:
        return payload
    try:
        latest_run = repository.latest_run() or {}
        run_id = str(latest_run.get("run_id") or "")
        if not run_id:
            return payload
        daily_equity = repository.fetch_daily_equity(run_id)
        trade_stats = repository.fetch_trade_statistics(run_id)
        snapshots = repository.fetch_portfolio_snapshots(run_id)
        metrics_rows = repository.fetch_metrics(run_id)
        metrics_map = {str(item.get("metric_name") or ""): item.get("metric_value") for item in metrics_rows}

        payload.update(
            {
                "latest_run": latest_run,
                "daily_equity": daily_equity,
                "trade_statistics": trade_stats,
                "portfolio_snapshots": snapshots,
                "metrics": metrics_rows,
                "metrics_map": metrics_map,
                "daily_report": build_daily_performance_report(daily_equity, metrics_map),
                "weekly_summary": build_weekly_summary(daily_equity),
                "monthly_summary": build_monthly_summary(daily_equity),
            }
        )
        return payload
    finally:
        repository.close()
