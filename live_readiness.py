from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


ALPACA_PAPER_ENDPOINT = "https://paper-api.alpaca.markets"
ALPACA_LIVE_ENDPOINT = "https://api.alpaca.markets"
DEFAULT_OBSERVATION_DAYS = 14


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _url(value: Any) -> str:
    return str(value or "").strip().rstrip("/").lower()


def _read_report(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _gate(identifier: str, label: str, category: str, passed: bool, value: str) -> dict[str, Any]:
    return {
        "id": identifier,
        "name": label,
        "category": category,
        "pass": bool(passed),
        "value": str(value),
        "required": True,
    }


def run_live_readiness_check(
    *,
    reconciliation_result: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
    now: datetime | None = None,
    status_path: str | Path | None = None,
) -> dict[str, Any]:
    """Persist a PAPER-only launch checklist; this function cannot submit orders."""
    env = os.environ if environ is None else environ
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    resolved_path = Path(
        status_path
        or env.get("LIVE_READINESS_STATUS_PATH", "/var/lib/quant-bot/live-readiness.json")
    )
    prior = _read_report(resolved_path)
    reconciliation = dict(reconciliation_result or {})
    observation_days_required = max(_int(env.get("LIVE_READINESS_OBSERVATION_DAYS"), DEFAULT_OBSERVATION_DAYS), 14)
    observation_date = current.date().isoformat()
    reconciliation_status = str(reconciliation.get("status") or "missing").strip().lower()
    observation = {
        "date": observation_date,
        "reconciliation_status": reconciliation_status,
        "warnings": sorted({str(item) for item in list(reconciliation.get("warnings") or []) if str(item)}),
    }
    observations_by_date = {
        str(item.get("date")): dict(item)
        for item in list(prior.get("observations") or [])
        if isinstance(item, dict) and str(item.get("date") or "")
    }
    observations_by_date[observation_date] = observation
    observations = [observations_by_date[key] for key in sorted(observations_by_date)][-90:]
    recent_observations = observations[-observation_days_required:]
    matched_observation_days = sum(
        1 for item in recent_observations if str(item.get("reconciliation_status") or "").lower() == "matched"
    )
    observation_complete = bool(
        len(recent_observations) >= observation_days_required
        and matched_observation_days == observation_days_required
    )

    trading_mode = str(env.get("TRADING_MODE", "PAPER")).strip().upper()
    live_readiness_mode = _bool(env.get("LIVE_READINESS_MODE"), True)
    live_trading_enabled = _bool(env.get("LIVE_TRADING_ENABLED"), False)
    live_submission_enabled = _bool(env.get("LIVE_ORDER_SUBMISSION_ENABLED"), False)
    activation_token_present = bool(str(env.get("LIVE_ACTIVATION_TOKEN", "")).strip())
    live_credentials_present = bool(
        str(env.get("ALPACA_LIVE_API_KEY", "")).strip()
        and str(env.get("ALPACA_LIVE_API_SECRET", "")).strip()
    )
    max_position_percent = _float(env.get("MAX_POSITION_EQUITY_PERCENT"), 0.0)
    initial_position_percent = _float(env.get("LIVE_INITIAL_MAX_POSITION_EQUITY_PERCENT"), 1.0)
    daily_loss_percent = _float(env.get("LIVE_DAILY_LOSS_STOP_PERCENT"), 2.0)
    max_daily_orders = _int(env.get("LIVE_MAX_DAILY_ORDERS"), 5)
    max_open_positions = _int(env.get("LIVE_MAX_OPEN_POSITIONS"), 10)
    max_data_age_seconds = _int(env.get("LIVE_MAX_MARKET_DATA_AGE_SECONDS"), 120)

    gates = [
        _gate("paper_mode", "PAPER mode remains active", "Isolation", trading_mode == "PAPER", trading_mode),
        _gate("readiness_mode", "Readiness mode enabled", "Isolation", live_readiness_mode, "ENABLED" if live_readiness_mode else "DISABLED"),
        _gate("live_trading_blocked", "LIVE trading hard-blocked", "Isolation", not live_trading_enabled, "BLOCKED" if not live_trading_enabled else "ENABLED"),
        _gate("live_orders_blocked", "LIVE order submission hard-blocked", "Isolation", not live_submission_enabled, "BLOCKED" if not live_submission_enabled else "ENABLED"),
        _gate("activation_token_absent", "Activation token not installed", "Isolation", not activation_token_present, "ABSENT" if not activation_token_present else "PRESENT"),
        _gate("paper_endpoint", "Paper endpoint isolated", "Broker", _url(env.get("ALPACA_PAPER_BASE_URL", ALPACA_PAPER_ENDPOINT)) == _url(ALPACA_PAPER_ENDPOINT), str(env.get("ALPACA_PAPER_BASE_URL", ALPACA_PAPER_ENDPOINT))),
        _gate("live_endpoint", "LIVE endpoint staged", "Broker", _url(env.get("ALPACA_LIVE_BASE_URL", ALPACA_LIVE_ENDPOINT)) == _url(ALPACA_LIVE_ENDPOINT), str(env.get("ALPACA_LIVE_BASE_URL", ALPACA_LIVE_ENDPOINT))),
        _gate("live_credentials", "Separate LIVE credentials staged", "Broker", live_credentials_present, "YES" if live_credentials_present else "NO"),
        _gate("paper_account", "Paper account active", "Broker", str(reconciliation.get("account_status") or "").upper() == "ACTIVE", str(reconciliation.get("account_status") or "UNKNOWN")),
        _gate("reconciliation", "Latest Alpaca reconciliation matched", "Evidence", reconciliation_status == "matched", reconciliation_status.upper()),
        _gate("observation_window", f"{observation_days_required}-day clean PAPER observation", "Evidence", observation_complete, f"{matched_observation_days}/{observation_days_required} matched days"),
        _gate("position_cap", "Absolute position cap is 10% or less", "Risk", 0 < max_position_percent <= 10, f"{max_position_percent:g}%"),
        _gate("initial_position_cap", "Initial LIVE position cap is 1% or less", "Risk", 0 < initial_position_percent <= 1, f"{initial_position_percent:g}%"),
        _gate("daily_loss_stop", "LIVE daily loss stop is 2% or less", "Risk", 0 < daily_loss_percent <= 2, f"{daily_loss_percent:g}%"),
        _gate("daily_order_cap", "Initial LIVE daily order cap is 5 or less", "Risk", 0 < max_daily_orders <= 5, str(max_daily_orders)),
        _gate("open_position_cap", "Initial LIVE open-position cap is 10 or less", "Risk", 0 < max_open_positions <= 10, str(max_open_positions)),
        _gate("kill_switch", "LIVE kill-switch capability enabled", "Execution", _bool(env.get("LIVE_KILL_SWITCH_ENABLED"), True), "ENABLED" if _bool(env.get("LIVE_KILL_SWITCH_ENABLED"), True) else "DISABLED"),
        _gate("duplicate_orders", "Duplicate-order protection enabled", "Execution", _bool(env.get("LIVE_DUPLICATE_ORDER_PROTECTION"), True), "ENABLED" if _bool(env.get("LIVE_DUPLICATE_ORDER_PROTECTION"), True) else "DISABLED"),
        _gate("stale_data", "Stale-data block is 120 seconds or less", "Execution", 0 < max_data_age_seconds <= 120, f"{max_data_age_seconds}s"),
        _gate("notifications", "Failure notifications enabled", "Operations", _bool(env.get("NOTIFICATIONS_ENABLED"), False), "ENABLED" if _bool(env.get("NOTIFICATIONS_ENABLED"), False) else "DISABLED"),
    ]
    blockers = [gate["name"] for gate in gates if gate["required"] and not gate["pass"]]
    if observation_complete and not blockers:
        readiness_status = "READY_FOR_CONTROLLED_LAUNCH"
    elif not observation_complete:
        readiness_status = "OBSERVING"
    else:
        readiness_status = "BLOCKED"

    report = {
        "updated_at": current.isoformat(),
        "status": readiness_status,
        "paper_only": True,
        "live_orders_blocked": True,
        "safe_to_enable_live": False,
        "observation_days_required": observation_days_required,
        "matched_observation_days": matched_observation_days,
        "observation_days_recorded": len(recent_observations),
        "gates_passed": sum(1 for gate in gates if gate["pass"]),
        "gates_total": len(gates),
        "blockers": blockers,
        "gates": gates,
        "observations": observations,
    }
    _write_report(resolved_path, report)
    return report


def load_live_readiness_report(status_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(status_path or os.getenv("LIVE_READINESS_STATUS_PATH", "/var/lib/quant-bot/live-readiness.json"))
    return _read_report(path)
