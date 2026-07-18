from __future__ import annotations

from typing import Any

from monitoring_db import MonitoringDatabase
from dashboard_models import build_dashboard_dataset
from research_data import fetch_research_dashboard_payload


def fetch_dashboard_payload(database_url: str | None, database_factory=MonitoringDatabase) -> dict[str, Any]:
    db = database_factory(database_url=database_url)
    try:
        research_payload = fetch_research_dashboard_payload(database_url, database_factory=MonitoringDatabase)
    except Exception:
        research_payload = {
            "db_connected": False,
            "latest_research_run": {},
            "recent_research_runs": [],
            "selected_research_run_id": "",
            "selected_research_candidates": [],
            "research_analytics": {
                "total_research_runs": 0,
                "total_candidate_observations": 0,
                "average_candidates_per_run": 0.0,
                "average_overall_score": 0.0,
                "average_confidence": 0.0,
                "score_distribution": [],
                "confidence_distribution": [],
                "candidate_count_by_sector": [],
                "candidate_count_by_regime": [],
                "signal_distribution": [],
                "top_recurring_symbols": [],
                "average_score_by_sector": [],
                "average_confidence_by_sector": [],
                "average_score_by_regime": [],
                "average_confidence_by_regime": [],
            },
            "latest_research_summary": {},
        }
    payload = {
        "db_connected": db.enabled,
        "latest_run": {},
        "latest_success": {},
        "latest_signal": {},
        "latest_account": {},
        "recent_runs": [],
        "recent_orders": [],
        "portfolio_history": [],
        "signal_history": [],
        "order_count_by_day": [],
        "research": research_payload,
    }
    if not db.enabled:
        return payload
    db.ensure_schema()
    payload["latest_run"] = db.fetch_latest_bot_run() or {}
    payload["latest_success"] = db.fetch_latest_successful_run() or {}
    payload["latest_signal"] = db.fetch_latest_signal_snapshot() or {}
    payload["latest_account"] = db.fetch_latest_account_snapshot() or {}
    payload["recent_runs"] = db.fetch_recent_runs(limit=80)
    payload["recent_orders"] = db.fetch_recent_order_events(limit=120)
    payload["portfolio_history"] = list(reversed(db.fetch_portfolio_history(limit=500)))
    payload["signal_history"] = list(reversed(db.fetch_signal_history(limit=500)))
    payload["order_count_by_day"] = list(reversed(db.fetch_order_count_by_day(limit=90)))
    dataset = build_dashboard_dataset(payload)
    return {
        "db_connected": dataset.db_connected,
        "latest_run": dataset.latest_run,
        "latest_success": dataset.latest_success,
        "latest_signal": dataset.latest_signal,
        "latest_account": dataset.latest_account,
        "recent_runs": dataset.recent_runs,
        "recent_orders": dataset.recent_orders,
        "portfolio_history": dataset.portfolio_history,
        "signal_history": dataset.signal_history,
        "order_count_by_day": dataset.order_count_by_day,
        "research": payload["research"],
    }
