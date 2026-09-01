from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

from quantum_score_engine import compute_strategy_specific_scores


@dataclass(frozen=True)
class StrategySignal:
    symbol: str
    signal: str
    entry_reason: str
    proposed_entry: float
    stop: float
    target_or_exit_rule: str
    confidence: float
    strategy_id: str
    strategy_version: str
    market_regime: str
    requested_risk_allocation: float
    quantum_score: float
    strategy_score: float
    expected_reward_risk: float
    supporting_factors: dict[str, Any]
    data_quality_status: str
    supports_scaling: bool = False

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _paper_short_enabled() -> bool:
    return bool(
        str(os.getenv("TRADING_MODE", "SIMULATION")).strip().upper() == "PAPER"
        and str(os.getenv("PAPER_ALLOW_SHORT_SELLING", "false")).strip().lower() in {"1", "true", "yes", "on"}
    )


def _payloads(candidate: dict[str, Any]) -> dict[str, dict[str, Any]]:
    existing = dict(candidate.get("strategy_specific_scores") or {})
    supported_ids = {
        "stock_trend_pullback_v3",
        "stock_trend_ensemble_v2",
        "stock_mean_reversion_v2",
        "stock_bearish_trend_v2",
    }
    if supported_ids.intersection(existing):
        return existing
    quantum = dict(candidate.get("quantum_score") or {})
    if quantum.get("normalized_component_scores"):
        return compute_strategy_specific_scores(quantum)

    # A shortlist score is not a substitute for the factor and regime evidence
    # required by the v2 sleeves. Missing quantum evidence must fail closed.
    return {}


def _supporting(candidate: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    quantum = dict(candidate.get("quantum_score") or {})
    return {
        "confirmations": dict(payload.get("confirmations") or {}),
        "confirmation_count": int(payload.get("confirmation_count") or 0),
        "components": dict(quantum.get("normalized_component_scores") or {}),
        "factor_values": dict(quantum.get("factor_values") or {}),
        "warnings": list(dict.fromkeys([*list(quantum.get("warnings") or []), *list(payload.get("warnings") or [])])),
        "rejection_reasons": list(payload.get("rejection_reasons") or []),
    }


def _risk_levels(candidate: dict[str, Any], price: float, *, short: bool = False) -> tuple[float, float, float]:
    risk = dict((candidate.get("quantum_score") or {}).get("risk_reward") or {})
    rr = _safe_float(risk.get("reward_risk_ratio"), 0.0)
    if short:
        return round(price * 1.04, 6), round(price * 0.92, 6), max(rr, 2.0)
    stop = _safe_float(risk.get("stop"), price * 0.96)
    target = _safe_float(risk.get("target"), price * 1.08)
    if not 0 < stop < price:
        stop = price * 0.96
    if target <= price:
        target = price * 1.08
    return round(stop, 6), round(target, 6), rr


def _build_signal(
    candidate: dict[str, Any],
    strategy_id: str,
    *,
    entry_reason: str,
    exit_rule: str,
    requested_signal: str,
) -> StrategySignal:
    symbol = str(candidate.get("symbol") or "").upper()
    price = _safe_float(candidate.get("latest_price"), 0.0)
    quantum = dict(candidate.get("quantum_score") or {})
    payload = dict(_payloads(candidate).get(strategy_id) or {})
    regime = str(quantum.get("market_regime") or payload.get("market_regime") or "unknown")
    is_short = requested_signal == "SELL"
    short_requested = str(candidate.get("trade_side") or "").strip().upper() == "SELL"
    eligible = bool(payload.get("eligible", False))
    if is_short:
        signal = "SELL" if eligible and short_requested and _paper_short_enabled() else "HOLD"
    else:
        signal = "BUY" if eligible else "HOLD"
    stop, target, rr = _risk_levels(candidate, price, short=is_short)
    return StrategySignal(
        symbol=symbol,
        signal=signal,
        entry_reason=entry_reason,
        proposed_entry=price,
        stop=stop,
        target_or_exit_rule=f"{exit_rule}; initial target {target:.4f}",
        confidence=_clip(_safe_float(payload.get("confidence"), 0.0), 0.0, 100.0),
        strategy_id=strategy_id,
        strategy_version=str(payload.get("strategy_version") or "2.0.0"),
        market_regime=regime,
        requested_risk_allocation=1.0 if signal in {"BUY", "SELL"} else 0.0,
        quantum_score=_safe_float(quantum.get("final_score"), candidate.get("overall_score") or 0.0),
        strategy_score=_safe_float(payload.get("strategy_score"), 0.0),
        expected_reward_risk=rr,
        supporting_factors=_supporting(candidate, payload),
        data_quality_status=str(quantum.get("data_quality_status") or "warn"),
    )


def evaluate_all_strategies(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the single cohesive stock entry strategy.

    Legacy v2 strategies remain supported by the exit policy for existing
    positions, but they no longer create new entries. This prevents strategy
    soup and keeps all new stock risk tied to one trend/pullback thesis.
    """
    symbol = str(candidate.get("symbol") or "").upper()
    price = _safe_float(candidate.get("latest_price"), 0.0)
    if not symbol or price <= 0:
        return []

    available_payloads = _payloads(candidate)
    strategy_id = (
        "stock_trend_pullback_v3"
        if "stock_trend_pullback_v3" in available_payloads
        else "stock_trend_ensemble_v2"
    )
    is_v3 = strategy_id == "stock_trend_pullback_v3"
    signals = [
        _build_signal(
            candidate,
            strategy_id,
            entry_reason=(
                "market regime, relative strength, established trend, and controlled pullback agree"
                if is_v3
                else "legacy trend signal retained only for in-flight compatibility"
            ),
            exit_rule=(
                "exit on ATR risk stop, trailing stop, trend breakdown, profit target, or time stop"
                if is_v3
                else "exit on trend breakdown, risk stop, or profit target"
            ),
            requested_signal="BUY",
        ),
    ]
    return [signal.to_dict() for signal in signals]
