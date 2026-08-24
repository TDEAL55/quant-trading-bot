from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from typing import Any

from alpaca_paper_broker import AlpacaPaperBroker
from monitoring_db import MonitoringDatabase
from dashboard_models import build_dashboard_dataset
from evaluation_data import fetch_evaluation_dashboard_payload
from daily_run_repository import DailyRunRepository
from factor_attribution import fetch_factor_attribution_dashboard_payload
from factor_intelligence_data import fetch_factor_intelligence_dashboard_payload
from performance_dashboard import fetch_performance_dashboard_payload
from paper_validation_data import fetch_paper_validation_dashboard_payload
from portfolio_research_data import fetch_portfolio_research_dashboard_payload
from quantum_score_data import fetch_quantum_score_dashboard_payload
from research_data import fetch_research_dashboard_payload
from scanner_data import fetch_scan_rejection_reasons, fetch_scanner_sector_distribution, fetch_top_ranked_stocks
from self_improving_data import fetch_self_improving_dashboard_payload
from strategy_lab_data import fetch_strategy_lab_dashboard_payload
from walk_forward_data import fetch_walk_forward_dashboard_payload


def _enabled(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _systemd_service_active(service_name: str) -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", service_name],
            check=False,
            capture_output=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _broker_order_to_dashboard_event(order: dict[str, Any]) -> dict[str, Any]:
    status = str(order.get("status") or "unknown").strip().lower()
    side = str(order.get("side") or "").strip().upper()
    timestamp = str(order.get("updated_at") or order.get("submitted_at") or "")
    requested_quantity = abs(float(order.get("requested_quantity") or 0.0))
    filled_quantity = abs(float(order.get("filled_quantity") or 0.0))
    fill_price = float(order.get("average_fill_price") or 0.0)
    event_quantity = filled_quantity if filled_quantity > 0 else requested_quantity
    return {
        "event_timestamp": timestamp,
        "market_date": timestamp[:10] if len(timestamp) >= 10 else "",
        "symbol": str(order.get("symbol") or "").upper(),
        "signal": side,
        "side": side,
        "quantity": requested_quantity,
        "filled_quantity": filled_quantity,
        "average_fill_price": fill_price,
        "notional": event_quantity * fill_price,
        "order_type": str(order.get("order_type") or "market").lower(),
        "time_in_force": str(order.get("time_in_force") or "day").lower(),
        "safe_order_status": status,
        "status": status,
        "submitted": 1,
        "broker_order_id": str(order.get("order_id") or ""),
        "client_order_id": str(order.get("client_order_id") or ""),
        "stop_reason": str(order.get("rejection_reason") or ""),
        "source": "alpaca_paper_broker",
    }


def _fetch_paper_account_snapshot(paper_broker_factory=AlpacaPaperBroker) -> dict[str, Any]:
    broker = paper_broker_factory(mode="PAPER")
    account = broker.get_account()
    positions_by_symbol = broker.get_positions()
    open_orders = broker.get_open_orders()
    broker_orders = list(getattr(broker, "get_order_history", lambda limit=50: [])(limit=120) or [])
    positions = [
        {
            "symbol": symbol,
            "quantity": details.get("quantity", 0.0),
            "average_entry_price": details.get("avg_price", 0.0),
            "current_price": details.get("current_price", 0.0),
            "market_value": details.get("market_value", 0.0),
            "unrealized_pl": details.get("unrealized_pl", 0.0),
        }
        for symbol, details in sorted(positions_by_symbol.items())
    ]
    recent_orders = [_broker_order_to_dashboard_event(order) for order in broker_orders]
    equity = float(account.get("equity") or account.get("portfolio_value") or 0.0)
    last_equity = float(account.get("last_equity") or equity)
    return {
        "snapshot_timestamp": datetime.now(timezone.utc).isoformat(),
        "account_status": account.get("status", "unknown"),
        "portfolio_value": account.get("portfolio_value", account.get("equity", 0.0)),
        "equity": equity,
        "last_equity": last_equity,
        "day_pl": float(account.get("day_pl") if account.get("day_pl") is not None else equity - last_equity),
        "cash": account.get("cash", 0.0),
        "buying_power": account.get("buying_power", 0.0),
        "open_positions": len(positions),
        "unrealized_paper_pl": sum(float(item.get("unrealized_pl") or 0.0) for item in positions),
        "pending_orders": len(open_orders),
        "positions": positions,
        "short_positions": sum(1 for item in positions if float(item.get("quantity") or 0.0) < 0),
        "gross_exposure": sum(abs(float(item.get("market_value") or 0.0)) for item in positions),
        "recent_orders": recent_orders,
        "source": "alpaca_paper_read_only",
    }


def _fetch_paper_tuning_snapshot(db: MonitoringDatabase) -> dict[str, Any]:
    validation = db.query_one(
        """
        SELECT
            COUNT(*) AS validation_runs,
            COALESCE(SUM(proposed_order_count), 0) AS orders_proposed,
            COALESCE(SUM(approved_order_count), 0) AS orders_approved,
            COALESCE(SUM(submitted_order_count), 0) AS orders_submitted,
            COALESCE(SUM(filled_order_count), 0) AS orders_filled,
            COALESCE(SUM(failed_order_count), 0) AS orders_failed,
            COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) AS reconciliation_failures
        FROM paper_validation_runs
        WHERE dry_run = 0
        """
    ) or {}
    closed_trades = db.query_one(
        """
        SELECT
            COUNT(*) AS closed_trades,
            COALESCE(SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END), 0) AS winning_trades,
            COALESCE(SUM(CASE WHEN net_pnl < 0 THEN 1 ELSE 0 END), 0) AS losing_trades,
            COALESCE(SUM(net_pnl), 0) AS net_pnl,
            COALESCE(AVG(net_pnl), 0) AS expectancy,
            COALESCE(AVG(percentage_return), 0) AS average_return
        FROM strategy_closed_trades
        """
    ) or {}
    order_statuses = db.query_all(
        """
        SELECT LOWER(submission_status) AS status, COUNT(*) AS count
        FROM paper_orders
        GROUP BY LOWER(submission_status)
        ORDER BY count DESC, status ASC
        """
    )
    notification_statuses = db.query_all(
        """
        SELECT delivery_status AS status, COUNT(*) AS count
        FROM notification_history
        GROUP BY delivery_status
        ORDER BY count DESC, delivery_status ASC
        """
    )
    latest_notification = db.query_one(
        """
        SELECT timestamp, provider, delivery_status, retry_count, safe_error_message
        FROM notification_history
        ORDER BY timestamp DESC
        LIMIT 1
        """
    ) or {}
    latest_position_reviews = db.fetch_latest_position_reviews(limit=25)
    return {
        "validation": dict(validation),
        "closed_trades": dict(closed_trades),
        "order_statuses": list(order_statuses or []),
        "notification_statuses": list(notification_statuses or []),
        "latest_notification": dict(latest_notification),
        "latest_position_reviews": list(latest_position_reviews or []),
    }


def fetch_dashboard_payload(
    database_url: str | None,
    database_factory=MonitoringDatabase,
    paper_broker_factory=AlpacaPaperBroker,
    service_probe=_systemd_service_active,
) -> dict[str, Any]:
    db = database_factory(database_url=database_url)
    try:
        research_payload = fetch_research_dashboard_payload(database_url, database_factory=MonitoringDatabase)
        evaluation_payload = fetch_evaluation_dashboard_payload(database_url, database_factory=MonitoringDatabase)
        factor_attribution_payload = fetch_factor_attribution_dashboard_payload(database_url, database_factory=MonitoringDatabase)
        factor_intelligence_payload = fetch_factor_intelligence_dashboard_payload(database_url)
        walk_forward_payload = fetch_walk_forward_dashboard_payload(database_url)
        portfolio_research_payload = fetch_portfolio_research_dashboard_payload(database_url)
        strategy_lab_payload = fetch_strategy_lab_dashboard_payload(database_url)
        paper_validation_payload = fetch_paper_validation_dashboard_payload(database_url)
        performance_payload = fetch_performance_dashboard_payload(database_url)
        quantum_payload = fetch_quantum_score_dashboard_payload(database_url)
        self_improving_payload = fetch_self_improving_dashboard_payload(database_url)
        daily_run_repo = DailyRunRepository(database_url=database_url)
        try:
            daily_run_payload = daily_run_repo.dashboard_payload()
        finally:
            daily_run_repo.close()
        research_payload["evaluation"] = evaluation_payload
        research_payload["factor_attribution"] = factor_attribution_payload
        research_payload["factor_intelligence"] = factor_intelligence_payload
        research_payload["walk_forward"] = walk_forward_payload
        research_payload["portfolio_research"] = portfolio_research_payload
        research_payload["strategy_lab"] = strategy_lab_payload
        research_payload["paper_validation"] = paper_validation_payload
        research_payload["performance_intelligence"] = performance_payload
        research_payload["quantum_score"] = quantum_payload
        research_payload["self_improving"] = self_improving_payload
        research_payload["daily_runs"] = daily_run_payload
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
            "evaluation": {
                "db_connected": False,
                "latest_labeling_run": {},
                "recent_labeled_observations": [],
                "recent_label_failures": [],
                "selected_horizon": "20d",
                "evaluation_analytics": {
                    "benchmark_symbol": "SPY",
                    "total_observations": 0,
                    "labeled_candidates": 0,
                    "status_counts": {"pending": 0, "partial": 0, "complete": 0, "unavailable": 0, "data_error": 0},
                    "horizons": {},
                    "score_buckets": {},
                    "confidence_buckets": {},
                    "regime_analysis": {},
                    "sector_analysis": {},
                    "signal_analysis": {},
                    "rank_analysis": {},
                    "recurring_symbol_analysis": {},
                    "correlations": {},
                    "latest_attempted_at": None,
                },
                "evaluation_config": {},
            },
            "factor_attribution": {
                "db_connected": False,
                "selected_horizon": "20d",
                "selected_factor": "overall_score",
                "factor_attribution_analytics": {
                    "factor_bucket_analysis": {},
                    "factor_distributions": {},
                    "factor_correlations": [],
                    "feature_importance_summary": [],
                    "strongest_predictive_factors": [],
                    "weakest_predictive_factors": [],
                    "minimum_sample_warnings": [],
                    "top_factor_combinations": {},
                },
                "factor_options": [],
            },
            "factor_intelligence": {
                "db_connected": False,
                "latest_run": {},
                "leaderboard": [],
                "predictive": [],
                "bucket": [],
                "stability": [],
                "regime": [],
                "redundancy": [],
                "warnings": [],
                "research_note": "Historical research analytics only. No automatic strategy-weight updates.",
            },
            "walk_forward": {
                "db_connected": False,
                "total_validation_runs": 0,
                "latest_run": {},
                "windows": [],
            },
            "portfolio_research": {
                "db_connected": False,
                "total_runs": 0,
                "latest_run": {},
                "snapshots": [],
            },
            "strategy_lab": {
                "db_connected": False,
                "total_runs": 0,
                "latest_run": {},
                "results": [],
                "pairwise": [],
            },
            "paper_validation": {
                "db_connected": False,
                "approvals": [],
                "latest_run": {},
                "latest_orders": [],
                "latest_position_snapshots": [],
                "history": [],
            },
            "performance_intelligence": {
                "db_connected": False,
                "latest_run": {},
                "daily_equity": [],
                "trade_statistics": [],
                "portfolio_snapshots": [],
                "metrics": [],
                "metrics_map": {},
                "daily_report": {},
                "weekly_summary": [],
                "monthly_summary": [],
            },
            "quantum_score": {
                "db_connected": False,
                "latest_run": {},
                "top_candidates": [],
                "selected_candidate": {},
                "candidate_details": {},
            },
            "self_improving": {
                "db_connected": False,
                "trade_memory": [],
                "strategy_leaderboard": [],
                "latest_regime": {},
                "strategy_regime_matrix": [],
                "factor_effectiveness": [],
                "allocation_recommendations": [],
                "strategy_state_recommendations": [],
                "weight_change_recommendations": [],
                "daily_report": {},
                "weekly_report": {},
                "portfolio_intelligence": {
                    "run": {},
                    "recommendations": [],
                    "exposures": [],
                    "reports": [],
                },
            },
            "daily_runs": {
                "db_connected": False,
                "latest_run": {},
                "history": [],
            },
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
        "latest_scanner_run": {},
        "top_scanner_results": [],
        "scanner_rejections": [],
        "scanner_sector_distribution": [],
        "service_health": {},
        "paper_tuning": {},
        "research": research_payload,
    }
    if not db.enabled:
        return payload
    db.ensure_schema()
    payload["latest_run"] = db.fetch_latest_bot_run() or {}
    payload["latest_success"] = db.fetch_latest_successful_run() or {}
    payload["latest_signal"] = db.fetch_latest_signal_snapshot() or {}
    database_account = db.fetch_latest_account_snapshot() or {}
    database_orders = db.fetch_recent_order_events(limit=120)
    payload["latest_account"] = database_account
    payload["recent_orders"] = database_orders
    if (
        _enabled(os.getenv("DASHBOARD_BROKER_ACCOUNT_FALLBACK_ENABLED", "true"))
        and str(os.getenv("TRADING_MODE", "PAPER")).strip().upper() == "PAPER"
    ):
        try:
            broker_snapshot = _fetch_paper_account_snapshot(paper_broker_factory)
            payload["latest_account"] = broker_snapshot
            payload["recent_orders"] = list(broker_snapshot.get("recent_orders") or [])
        except Exception as exc:
            payload["broker_sync_error"] = type(exc).__name__
    payload["recent_runs"] = db.fetch_recent_runs(limit=80)
    payload["portfolio_history"] = list(reversed(db.fetch_portfolio_history(limit=500)))
    payload["signal_history"] = list(reversed(db.fetch_signal_history(limit=500)))
    payload["order_count_by_day"] = list(reversed(db.fetch_order_count_by_day(limit=90)))
    payload["latest_scanner_run"] = db.fetch_latest_scanner_run() or {}
    payload["top_scanner_results"] = fetch_top_ranked_stocks(database_url, limit=20, database_factory=MonitoringDatabase)
    payload["scanner_rejections"] = fetch_scan_rejection_reasons(database_url, limit=50, database_factory=MonitoringDatabase)
    payload["scanner_sector_distribution"] = fetch_scanner_sector_distribution(database_url, database_factory=MonitoringDatabase)
    payload["paper_tuning"] = _fetch_paper_tuning_snapshot(db)

    recent_runs = payload["recent_runs"] or []
    observed_restarts = 0
    previous_run_id = None
    for row in recent_runs:
        run_id = str((row or {}).get("run_id") or "").strip()
        if not run_id:
            continue
        if previous_run_id is not None and run_id != previous_run_id:
            observed_restarts += 1
        previous_run_id = run_id
    recent_error_count_row = db.query_one(
        """
        SELECT COUNT(*) AS error_count
        FROM bot_runs
        WHERE bot_status = ?
          AND run_timestamp >= datetime('now', '-24 hours')
        """,
        ("error",),
    )
    payload["service_health"] = {
        "recent_error_count_24h": int((recent_error_count_row or {}).get("error_count") or 0),
        "observed_runner_restarts_24h": int(observed_restarts),
        "observed_runner_run_id_transitions": int(observed_restarts),
        "continuous_service_active": bool(service_probe("quant-bot-continuous.service")),
    }

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
        "latest_scanner_run": payload["latest_scanner_run"],
        "top_scanner_results": payload["top_scanner_results"],
        "scanner_rejections": payload["scanner_rejections"],
        "scanner_sector_distribution": payload["scanner_sector_distribution"],
        "service_health": payload["service_health"],
        "paper_tuning": payload["paper_tuning"],
        "research": payload["research"],
    }
