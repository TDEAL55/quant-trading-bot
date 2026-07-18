from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from config import is_safe_mode


ALLOWED_MODES = {"PAPER", "SIMULATION"}
ALLOWED_BROKER_TYPES = {"paper", "simulation"}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True, separators=(",", ":"))


def _parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def approval_configuration_fingerprint(
    strategy_id: str,
    strategy_version: str,
    strategy_fingerprint: str,
    portfolio_configuration: dict[str, Any],
    risk_configuration: dict[str, Any],
    benchmark: str,
    horizon: int,
) -> str:
    payload = {
        "strategy_id": str(strategy_id),
        "strategy_version": str(strategy_version),
        "strategy_fingerprint": str(strategy_fingerprint),
        "portfolio_configuration": portfolio_configuration or {},
        "risk_configuration": risk_configuration or {},
        "benchmark": str(benchmark),
        "horizon": int(horizon),
    }
    digest = hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()
    return digest[:24]


@dataclass(frozen=True)
class ApprovalValidationResult:
    valid: bool
    reasons: list[str]
    fingerprint_status: str


def build_approval_record(
    approval_id: str,
    strategy_id: str,
    strategy_version: str,
    strategy_fingerprint: str,
    portfolio_configuration: dict[str, Any],
    risk_configuration: dict[str, Any],
    benchmark: str,
    horizon: int,
    approved_by: str,
    approved_at: str,
    expires_at: str,
    enabled: bool,
    notes: str,
) -> dict[str, Any]:
    config_fingerprint = approval_configuration_fingerprint(
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        strategy_fingerprint=strategy_fingerprint,
        portfolio_configuration=portfolio_configuration,
        risk_configuration=risk_configuration,
        benchmark=benchmark,
        horizon=horizon,
    )
    now = _utc_iso()
    return {
        "approval_id": str(approval_id),
        "strategy_id": str(strategy_id),
        "strategy_version": str(strategy_version),
        "strategy_fingerprint": str(strategy_fingerprint),
        "portfolio_configuration": dict(portfolio_configuration or {}),
        "risk_configuration": dict(risk_configuration or {}),
        "benchmark": str(benchmark),
        "horizon": int(horizon),
        "approved_by": str(approved_by),
        "approved_at": str(approved_at),
        "expires_at": str(expires_at),
        "enabled": bool(enabled),
        "notes": str(notes or ""),
        "configuration_fingerprint": config_fingerprint,
        "created_at": now,
        "updated_at": now,
    }


def validate_approval(
    approval: dict[str, Any] | None,
    expected_strategy_id: str,
    expected_strategy_version: str,
    expected_strategy_fingerprint: str,
    expected_portfolio_configuration: dict[str, Any],
    expected_risk_configuration: dict[str, Any],
    mode: str,
    broker_type: str,
    now_ts: str | None = None,
) -> ApprovalValidationResult:
    reasons: list[str] = []
    if not approval:
        return ApprovalValidationResult(valid=False, reasons=["approval missing"], fingerprint_status="missing")

    now_dt = _parse_dt(now_ts) or datetime.now(timezone.utc)
    approval_enabled = bool(approval.get("enabled", False))
    if not approval_enabled:
        reasons.append("approval is disabled")

    expires_dt = _parse_dt(approval.get("expires_at"))
    if expires_dt is None:
        reasons.append("approval expiration is invalid")
    elif expires_dt <= now_dt:
        reasons.append("approval is expired")

    normalized_mode = str(mode or "").strip().upper()
    if normalized_mode == "LIVE":
        reasons.append("LIVE mode is rejected")
    if not is_safe_mode(normalized_mode):
        reasons.append("mode is not paper-safe")
    if normalized_mode not in ALLOWED_MODES:
        reasons.append("mode must be PAPER or SIMULATION")

    normalized_broker = str(broker_type or "").strip().lower()
    if normalized_broker == "live":
        reasons.append("live broker type is rejected")
    if normalized_broker not in ALLOWED_BROKER_TYPES:
        reasons.append("broker type must be paper or simulation")

    if str(approval.get("strategy_id") or "") != str(expected_strategy_id):
        reasons.append("strategy id mismatch")
    if str(approval.get("strategy_version") or "") != str(expected_strategy_version):
        reasons.append("strategy version mismatch")
    if str(approval.get("strategy_fingerprint") or "") != str(expected_strategy_fingerprint):
        reasons.append("strategy fingerprint mismatch")

    expected_cfg_fingerprint = approval_configuration_fingerprint(
        strategy_id=expected_strategy_id,
        strategy_version=expected_strategy_version,
        strategy_fingerprint=expected_strategy_fingerprint,
        portfolio_configuration=expected_portfolio_configuration,
        risk_configuration=expected_risk_configuration,
        benchmark=str(approval.get("benchmark") or ""),
        horizon=int(approval.get("horizon") or 0),
    )
    approval_cfg_fingerprint = str(approval.get("configuration_fingerprint") or "")
    fingerprint_status = "matched" if expected_cfg_fingerprint == approval_cfg_fingerprint else "mismatch"

    if dict(approval.get("portfolio_configuration") or {}) != dict(expected_portfolio_configuration or {}):
        reasons.append("portfolio configuration changed")
    if dict(approval.get("risk_configuration") or {}) != dict(expected_risk_configuration or {}):
        reasons.append("risk configuration changed")
    if fingerprint_status != "matched":
        reasons.append("configuration fingerprint mismatch")

    return ApprovalValidationResult(valid=not reasons, reasons=reasons, fingerprint_status=fingerprint_status)
