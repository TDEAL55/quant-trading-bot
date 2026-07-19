from __future__ import annotations

from typing import Any


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def build_daily_run_report(execution_result: dict[str, Any], performance_result: dict[str, Any], execution_time_seconds: float) -> dict[str, Any]:
    market = execution_result.get("market") or {}
    perf_metrics = performance_result.get("metrics") or {}
    paper_order = execution_result.get("paper_order") or {}
    reconciliation = execution_result.get("reconciliation") or {}
    risk_result = execution_result.get("risk_result") or {}

    selected_symbols = []
    if execution_result.get("selected_symbol"):
        selected_symbols.append(str(execution_result.get("selected_symbol")))

    return {
        "Market Timestamp": market.get("market_timestamp"),
        "Session Type": market.get("session_type"),
        "Universe Size": execution_result.get("universe_size"),
        "Qualified Securities": execution_result.get("qualified_securities"),
        "Selected Symbols": selected_symbols,
        "Overall Scores": [execution_result.get("overall_score")] if execution_result.get("overall_score") is not None else [],
        "Confidence": execution_result.get("confidence"),
        "Approval Result": (execution_result.get("approval") or {}).get("granted"),
        "Risk Result": risk_result.get("approved"),
        "Orders Submitted": 1 if paper_order.get("order_id") else 0,
        "Orders Filled": 1 if paper_order.get("order_id") else 0,
        "Cash": execution_result.get("cash_after"),
        "Portfolio Value": perf_metrics.get("portfolio_value"),
        "Daily Return": perf_metrics.get("daily_return"),
        "Total Return": perf_metrics.get("total_return"),
        "Drawdown": perf_metrics.get("current_drawdown"),
        "Sharpe Ratio": perf_metrics.get("sharpe_ratio"),
        "Reconciliation Status": reconciliation.get("reconciliation_status"),
        "Dashboard Updated": bool(execution_result.get("dashboard_updated")),
        "Execution Time": _safe_float(execution_time_seconds, 0.0),
    }
