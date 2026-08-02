from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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


def _trend_momentum(symbol: str, price: float, score: float, confidence: float, regime: str) -> StrategySignal:
    signal = "BUY" if score >= 70 and confidence >= 60 else "HOLD"
    return StrategySignal(
        symbol=symbol,
        signal=signal,
        entry_reason="trend and momentum alignment from scanner score and confidence",
        proposed_entry=price,
        stop=round(price * 0.97, 6),
        target_or_exit_rule="exit on trend breakdown or trailing-stop 3%",
        confidence=_clip((score + confidence) / 2.0, 0.0, 100.0),
        strategy_id="trend_momentum",
        strategy_version="1.0.0",
        market_regime=regime,
        requested_risk_allocation=0.25,
    )


def _moving_average_trend(symbol: str, price: float, score: float, confidence: float, regime: str) -> StrategySignal:
    synthetic_fast = score
    synthetic_slow = confidence
    signal = "BUY" if synthetic_fast >= synthetic_slow else "HOLD"
    return StrategySignal(
        symbol=symbol,
        signal=signal,
        entry_reason="fast-vs-slow trend proxy confirms direction",
        proposed_entry=price,
        stop=round(price * 0.965, 6),
        target_or_exit_rule="exit when fast trend proxy crosses below slow proxy",
        confidence=_clip((0.6 * score) + (0.4 * confidence), 0.0, 100.0),
        strategy_id="ma_trend_follow",
        strategy_version="1.0.0",
        market_regime=regime,
        requested_risk_allocation=0.25,
    )


def _mean_reversion(symbol: str, price: float, score: float, confidence: float, regime: str) -> StrategySignal:
    oversold_proxy = 100.0 - score
    signal = "BUY" if oversold_proxy >= 30 and regime != "risk_off" else "HOLD"
    return StrategySignal(
        symbol=symbol,
        signal=signal,
        entry_reason="short-term pullback with non-risk-off regime",
        proposed_entry=price,
        stop=round(price * 0.98, 6),
        target_or_exit_rule="exit near mean reversion target or time-stop at 3 sessions",
        confidence=_clip((oversold_proxy * 0.5) + (confidence * 0.5), 0.0, 100.0),
        strategy_id="short_term_mean_reversion",
        strategy_version="1.0.0",
        market_regime=regime,
        requested_risk_allocation=0.25,
    )


def _breakout_volume(symbol: str, price: float, score: float, confidence: float, regime: str) -> StrategySignal:
    signal = "BUY" if score >= 75 and confidence >= 55 else "HOLD"
    return StrategySignal(
        symbol=symbol,
        signal=signal,
        entry_reason="breakout proxy with volume-confidence confirmation",
        proposed_entry=price,
        stop=round(price * 0.96, 6),
        target_or_exit_rule="exit on failed breakout close back below breakout range",
        confidence=_clip((0.7 * score) + (0.3 * confidence), 0.0, 100.0),
        strategy_id="breakout_volume_confirmation",
        strategy_version="1.0.0",
        market_regime=regime,
        requested_risk_allocation=0.25,
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
        _trend_momentum(symbol, price, score, confidence, regime),
        _moving_average_trend(symbol, price, score, confidence, regime),
        _mean_reversion(symbol, price, score, confidence, regime),
        _breakout_volume(symbol, price, score, confidence, regime),
    ]
    return [item.to_dict() for item in signals]
