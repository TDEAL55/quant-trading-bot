from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _as_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes"}


def _normalize_signal(signal):
    text = str(signal or "").strip().upper()
    return text if text in {"BUY", "HOLD", "SELL"} else "HOLD"


@dataclass
class DashboardDataset:
    db_connected: bool = False
    latest_run: dict[str, Any] = field(default_factory=dict)
    latest_success: dict[str, Any] = field(default_factory=dict)
    latest_signal: dict[str, Any] = field(default_factory=dict)
    latest_account: dict[str, Any] = field(default_factory=dict)
    recent_runs: list[dict[str, Any]] = field(default_factory=list)
    recent_orders: list[dict[str, Any]] = field(default_factory=list)
    portfolio_history: list[dict[str, Any]] = field(default_factory=list)
    signal_history: list[dict[str, Any]] = field(default_factory=list)
    order_count_by_day: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class DashboardStatusSummary:
    severity: str
    label: str
    explanation: str
    timestamp: str
    source: str


@dataclass
class DashboardViewModel:
    bot_health: dict[str, Any]
    market_status: dict[str, Any]
    signal: dict[str, Any]
    risk_matrix: list[dict[str, Any]]
    portfolio: dict[str, Any]
    orders: dict[str, Any]
    performance: dict[str, Any]
    operations: dict[str, Any]
    alerts: list[dict[str, Any]]
    intelligence: list[str]
    freshness: dict[str, Any]
    helpers: dict[str, Any]


def build_dashboard_dataset(payload: dict[str, Any]) -> DashboardDataset:
    return DashboardDataset(
        db_connected=bool(payload.get("db_connected")),
        latest_run=dict(payload.get("latest_run") or {}),
        latest_success=dict(payload.get("latest_success") or {}),
        latest_signal=dict(payload.get("latest_signal") or {}),
        latest_account=dict(payload.get("latest_account") or {}),
        recent_runs=list(payload.get("recent_runs") or []),
        recent_orders=list(payload.get("recent_orders") or []),
        portfolio_history=list(payload.get("portfolio_history") or []),
        signal_history=list(payload.get("signal_history") or []),
        order_count_by_day=list(payload.get("order_count_by_day") or []),
    )


def build_normalized_view_model(payload: dict[str, Any]) -> DashboardViewModel:
    dataset = build_dashboard_dataset(payload)
    latest_run = dataset.latest_run
    latest_success = dataset.latest_success
    latest_signal = dataset.latest_signal
    latest_account = dataset.latest_account
    portfolio_history = dataset.portfolio_history
    signal_history = dataset.signal_history
    recent_orders = dataset.recent_orders

    values = [_as_float(item.get("portfolio_value"), 0.0) for item in portfolio_history]
    daily_pl = values[-1] - values[-2] if len(values) >= 2 else 0.0
    total_pl = values[-1] - values[0] if values else 0.0
    mission_signal_counts = {"BUY": 0, "HOLD": 0, "SELL": 0}
    for item in signal_history:
        mission_signal_counts[_normalize_signal(item.get("generated_signal"))] += 1

    submitted_orders = len([item for item in recent_orders if _as_bool(item.get("submitted"))])
    blocked_orders = len(recent_orders) - submitted_orders
    portfolio_value = _as_float(latest_account.get("portfolio_value"), 0.0)
    cash = _as_float(latest_account.get("cash"), 0.0)
    open_position_value = max(portfolio_value - cash, 0.0)

    risk_matrix = [
        {"safeguard": "PAPER mode", "limit": "Required", "current_usage": str(latest_run.get("trading_mode", "PAPER")), "status": "Armed" if str(latest_run.get("trading_mode", "PAPER")).upper() == "PAPER" else "Critical", "explanation": "Dashboard is read-only and the worker must remain in PAPER mode."},
        {"safeguard": "LIVE blocked", "limit": "Always blocked", "current_usage": str(latest_run.get("trading_mode", "PAPER")), "status": "Armed" if str(latest_run.get("trading_mode", "PAPER")).upper() != "LIVE" else "Triggered", "explanation": "LIVE trading stays blocked."},
        {"safeguard": "$10 maximum order", "limit": "$10", "current_usage": f"{_as_float(latest_signal.get('daily_submitted_notional'), 0.0):.2f}", "status": "Armed", "explanation": "Per-order notional remains capped in the worker."},
        {"safeguard": "3-order daily maximum", "limit": "3", "current_usage": str(int(latest_signal.get("daily_submitted_order_count") or 0)), "status": "Armed", "explanation": "The daily order count ceiling remains enforced."},
        {"safeguard": "$30 daily notional ceiling", "limit": "$30", "current_usage": f"{_as_float(latest_signal.get('daily_submitted_notional'), 0.0):.2f}", "status": "Armed", "explanation": "Daily submitted notional remains capped."},
        {"safeguard": "30-minute cooldown", "limit": "30 minutes", "current_usage": str(latest_signal.get("cooldown_status", "Unknown")), "status": "Armed", "explanation": "Cooldown logic stays active in the worker."},
        {"safeguard": "duplicate-signal protection", "limit": "Enabled", "current_usage": str(latest_signal.get("duplicate_signal_status", "Unknown")), "status": "Armed", "explanation": "Duplicate entries remain blocked."},
        {"safeguard": "pending-order protection", "limit": "Enabled", "current_usage": str(latest_signal.get("pending_order_status", "Unknown")), "status": "Armed", "explanation": "Pending orders remain protected."},
        {"safeguard": "daily loss stop", "limit": "Enabled", "current_usage": str(latest_signal.get("daily_loss_stop_status", "Unknown")), "status": "Armed", "explanation": "Daily loss stop remains active."},
        {"safeguard": "total paper loss stop", "limit": "Enabled", "current_usage": str(latest_run.get("stop_reason", "Unknown")), "status": "Armed", "explanation": "Total paper loss stop remains active."},
        {"safeguard": "persistent-state health", "limit": "Available", "current_usage": "Connected" if dataset.db_connected else "Disconnected", "status": "Healthy" if dataset.db_connected else "Warning", "explanation": "Persistent monitoring state is read only."},
        {"safeguard": "review-required status", "limit": "False", "current_usage": str(_as_bool(latest_run.get("review_required"))), "status": "Triggered" if _as_bool(latest_run.get("review_required")) else "Armed", "explanation": "Review-required is the only critical control state here."},
    ]

    freshness = {
        "db_connected": dataset.db_connected,
        "portfolio_value": portfolio_value,
        "cash": cash,
        "open_position_value": open_position_value,
        "latest_run_timestamp": latest_run.get("run_timestamp"),
        "latest_success_timestamp": latest_success.get("run_timestamp"),
        "latest_signal_timestamp": latest_signal.get("latest_market_data_timestamp"),
    }

    return DashboardViewModel(
        bot_health={"label": str(latest_run.get("bot_status", "Unknown")).title(), "style": "healthy" if str(latest_run.get("bot_status", "")).lower() == "healthy" else "warning"},
        market_status={"label": "Open" if _as_bool(latest_signal.get("market_open")) else "Closed", "style": "healthy" if _as_bool(latest_signal.get("market_open")) else "neutral"},
        signal={"label": _normalize_signal(latest_signal.get("generated_signal")), "style": str(_normalize_signal(latest_signal.get("generated_signal"))).lower()},
        risk_matrix=risk_matrix,
        portfolio={"cash": cash, "buying_power": _as_float(latest_account.get("buying_power"), 0.0), "open_position_value": open_position_value, "latest_update": latest_account.get("snapshot_timestamp"), "positions": latest_account.get("positions") or []},
        orders={"submitted": submitted_orders, "blocked": blocked_orders, "recent_orders": recent_orders},
        performance={"portfolio_values": values, "daily_pl": daily_pl, "total_pl": total_pl, "signal_counts": mission_signal_counts},
        operations={"db_connected": dataset.db_connected, "latest_run": latest_run, "latest_success": latest_success, "latest_signal": latest_signal, "latest_account": latest_account},
        alerts=list(recent_orders),
        intelligence=["The dashboard remains read only.", "HOLD is a neutral decision.", "No positions is normal when there is no valid entry signal."],
        freshness=freshness,
        helpers={"submitted_orders": submitted_orders, "blocked_orders": blocked_orders, "daily_notional_used": _as_float(latest_signal.get("daily_submitted_notional"), 0.0)},
    )
