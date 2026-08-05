from __future__ import annotations

from dataclasses import dataclass
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
        return {
            "symbol": self.symbol,
            "signal": self.signal,
            "entry_reason": self.entry_reason,
            "proposed_entry": self.proposed_entry,
            "stop": self.stop,
            "target_or_exit_rule": self.target_or_exit_rule,
            "confidence": self.confidence,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "market_regime": self.market_regime,
            "requested_risk_allocation": self.requested_risk_allocation,
            "quantum_score": self.quantum_score,
            "strategy_score": self.strategy_score,
            "expected_reward_risk": self.expected_reward_risk,
            "supporting_factors": dict(self.supporting_factors),
            "data_quality_status": self.data_quality_status,
            "supports_scaling": self.supports_scaling,
        }


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _derive_regime(overall_score: float, confidence: float) -> str:
    if overall_score >= 80 and confidence >= 65:
        return "bull"
    if overall_score <= 45:
        return "risk_off"
    return "neutral"


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _strategy_payload(candidate: dict[str, Any], strategy_id: str) -> dict[str, Any]:
    specific = dict(candidate.get("strategy_specific_scores") or {})
    if strategy_id in specific:
        return dict(specific.get(strategy_id) or {})
    quantum = dict(candidate.get("quantum_score") or {})
    fallback = compute_strategy_specific_scores(quantum) if quantum else {}
    return dict(fallback.get(strategy_id) or {})


def _supporting(candidate: dict[str, Any]) -> dict[str, Any]:
    quantum = dict(candidate.get("quantum_score") or {})
    components = dict(quantum.get("normalized_component_scores") or {})
    return {
        "components": components,
        "warnings": list(quantum.get("warnings") or []),
        "rejection_reasons": list(quantum.get("rejection_reasons") or []),
    }


def _rr(candidate: dict[str, Any]) -> float:
    quantum = dict(candidate.get("quantum_score") or {})
    risk_reward = dict(quantum.get("risk_reward") or {})
    return _safe_float(risk_reward.get("reward_risk_ratio"), 0.0)


def _trend_momentum(symbol: str, price: float, score: float, confidence: float, regime: str, candidate: dict[str, Any]) -> StrategySignal:
    payload = _strategy_payload(candidate, "trend_momentum_v1")
    strategy_score = _safe_float(payload.get("strategy_score"), 0.0)
    fallback_buy = score >= 70 and confidence >= 60
    signal = "BUY" if (bool(payload.get("eligible", False)) or (not payload and fallback_buy)) else "HOLD"
    return StrategySignal(
        symbol=symbol,
        signal=signal,
        entry_reason="trend and momentum alignment from quantum and strategy-specific scores",
        proposed_entry=price,
        stop=round(price * 0.97, 6),
        target_or_exit_rule="exit on trend breakdown or trailing-stop 3%",
        confidence=_clip(_safe_float(payload.get("confidence"), (score + confidence) / 2.0), 0.0, 100.0),
        strategy_id="trend_momentum_v1",
        strategy_version="1.0.0",
        market_regime=regime,
        requested_risk_allocation=0.25,
        quantum_score=score,
        strategy_score=strategy_score,
        expected_reward_risk=_rr(candidate),
        supporting_factors=_supporting(candidate),
        data_quality_status=str((candidate.get("quantum_score") or {}).get("data_quality_status") or "warn"),
    )


def _moving_average_trend(symbol: str, price: float, score: float, confidence: float, regime: str, candidate: dict[str, Any]) -> StrategySignal:
    payload = _strategy_payload(candidate, "moving_average_trend_v1")
    strategy_score = _safe_float(payload.get("strategy_score"), 0.0)
    fallback_buy = score >= confidence
    signal = "BUY" if (bool(payload.get("eligible", False)) or (not payload and fallback_buy)) else "HOLD"
    return StrategySignal(
        symbol=symbol,
        signal=signal,
        entry_reason="fast-vs-slow trend proxy confirms direction",
        proposed_entry=price,
        stop=round(price * 0.965, 6),
        target_or_exit_rule="exit when fast trend proxy crosses below slow proxy",
        confidence=_clip(_safe_float(payload.get("confidence"), (0.6 * score) + (0.4 * confidence)), 0.0, 100.0),
        strategy_id="moving_average_trend_v1",
        strategy_version="1.0.0",
        market_regime=regime,
        requested_risk_allocation=0.25,
        quantum_score=score,
        strategy_score=strategy_score,
        expected_reward_risk=_rr(candidate),
        supporting_factors=_supporting(candidate),
        data_quality_status=str((candidate.get("quantum_score") or {}).get("data_quality_status") or "warn"),
    )


def _mean_reversion(symbol: str, price: float, score: float, confidence: float, regime: str, candidate: dict[str, Any]) -> StrategySignal:
    payload = _strategy_payload(candidate, "short_term_mean_reversion_v1")
    strategy_score = _safe_float(payload.get("strategy_score"), 0.0)
    oversold_proxy = 100.0 - score
    fallback_buy = oversold_proxy >= 30 and regime != "risk_off"
    signal = "BUY" if (bool(payload.get("eligible", False)) or (not payload and fallback_buy)) else "HOLD"
    return StrategySignal(
        symbol=symbol,
        signal=signal,
        entry_reason="short-term pullback with non-risk-off regime",
        proposed_entry=price,
        stop=round(price * 0.98, 6),
        target_or_exit_rule="exit near mean reversion target or time-stop at 3 sessions",
        confidence=_clip(_safe_float(payload.get("confidence"), (oversold_proxy * 0.5) + (confidence * 0.5)), 0.0, 100.0),
        strategy_id="short_term_mean_reversion_v1",
        strategy_version="1.0.0",
        market_regime=regime,
        requested_risk_allocation=0.25,
        quantum_score=score,
        strategy_score=strategy_score,
        expected_reward_risk=_rr(candidate),
        supporting_factors=_supporting(candidate),
        data_quality_status=str((candidate.get("quantum_score") or {}).get("data_quality_status") or "warn"),
    )


def _breakout_volume(symbol: str, price: float, score: float, confidence: float, regime: str, candidate: dict[str, Any]) -> StrategySignal:
    payload = _strategy_payload(candidate, "volume_breakout_v1")
    strategy_score = _safe_float(payload.get("strategy_score"), 0.0)
    fallback_buy = score >= 75 and confidence >= 55
    signal = "BUY" if (bool(payload.get("eligible", False)) or (not payload and fallback_buy)) else "HOLD"
    return StrategySignal(
        symbol=symbol,
        signal=signal,
        entry_reason="breakout proxy with volume-confidence confirmation",
        proposed_entry=price,
        stop=round(price * 0.96, 6),
        target_or_exit_rule="exit on failed breakout close back below breakout range",
        confidence=_clip(_safe_float(payload.get("confidence"), (0.7 * score) + (0.3 * confidence)), 0.0, 100.0),
        strategy_id="volume_breakout_v1",
        strategy_version="1.0.0",
        market_regime=regime,
        requested_risk_allocation=0.25,
        quantum_score=score,
        strategy_score=strategy_score,
        expected_reward_risk=_rr(candidate),
        supporting_factors=_supporting(candidate),
        data_quality_status=str((candidate.get("quantum_score") or {}).get("data_quality_status") or "warn"),
    )


def evaluate_all_strategies(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    symbol = str(candidate.get("symbol") or "").upper()
    if not symbol:
        return []

    price = _safe_float(candidate.get("latest_price"), 0.0)
    score = _safe_float(candidate.get("overall_score", candidate.get("score")), 0.0)
    confidence = _safe_float(candidate.get("confidence"), 0.0)
    if price <= 0:
        return []

    regime = _derive_regime(score, confidence)
    signals = [
        _trend_momentum(symbol, price, score, confidence, regime, candidate),
        _moving_average_trend(symbol, price, score, confidence, regime, candidate),
        _mean_reversion(symbol, price, score, confidence, regime, candidate),
        _breakout_volume(symbol, price, score, confidence, regime, candidate),
    ]
    return [item.to_dict() for item in signals]
