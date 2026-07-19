from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any


def _iso_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text[:10]


def build_daily_performance_report(daily_equity: list[dict[str, Any]], metrics: dict[str, Any]) -> dict[str, Any]:
    latest = daily_equity[-1] if daily_equity else {}
    return {
        "report_type": "daily",
        "as_of_date": latest.get("equity_date"),
        "portfolio_value": latest.get("portfolio_value"),
        "daily_return": latest.get("daily_return"),
        "current_drawdown": latest.get("current_drawdown"),
        "win_rate": metrics.get("win_rate"),
        "sharpe_ratio": metrics.get("sharpe_ratio"),
        "alpha": metrics.get("alpha"),
    }


def _group_period(daily_equity: list[dict[str, Any]], mode: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in daily_equity:
        date_text = _iso_date(row.get("equity_date"))
        if not date_text:
            continue
        dt = datetime.fromisoformat(date_text)
        if mode == "weekly":
            key = f"{dt.isocalendar().year}-W{dt.isocalendar().week:02d}"
        else:
            key = f"{dt.year}-{dt.month:02d}"
        grouped[key].append(row)
    return grouped


def _period_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"period_return": 0.0, "start_value": 0.0, "end_value": 0.0, "days": 0}
    start_value = float(rows[0].get("portfolio_value") or 0.0)
    end_value = float(rows[-1].get("portfolio_value") or 0.0)
    period_return = ((end_value / start_value) - 1.0) if start_value > 0 else 0.0
    return {
        "period_return": period_return,
        "start_value": start_value,
        "end_value": end_value,
        "days": len(rows),
    }


def build_weekly_summary(daily_equity: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped = _group_period(daily_equity, mode="weekly")
    result = []
    for key in sorted(grouped.keys()):
        summary = _period_summary(grouped[key])
        result.append({"week": key, **summary})
    return result


def build_monthly_summary(daily_equity: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped = _group_period(daily_equity, mode="monthly")
    result = []
    for key in sorted(grouped.keys()):
        summary = _period_summary(grouped[key])
        result.append({"month": key, **summary})
    return result
