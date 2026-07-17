from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import math

import pandas as pd


EMA_WINDOWS = (20, 50, 200)
RSI_WINDOW = 14
ATR_WINDOW = 14
ROC_WINDOW = 10
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9


@dataclass(frozen=True)
class FactorScore:
    name: str
    score: float
    status: str
    positive_reasons: list[str]
    negative_reasons: list[str]
    warnings: list[str]
    raw_values: dict[str, Any]
    available: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 2),
            "status": self.status,
            "positive_reasons": list(self.positive_reasons),
            "negative_reasons": list(self.negative_reasons),
            "warnings": list(self.warnings),
            "raw_values": dict(self.raw_values),
            "available": self.available,
        }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, float(value)))


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_signal_text(signal: str | None) -> str:
    text = str(signal or "HOLD").strip().upper()
    return text if text in {"STRONG_BUY", "BUY", "HOLD", "REDUCE", "EXIT"} else "HOLD"


def _legacy_signal_text(signal: str | None) -> str:
    normalized = _normalize_signal_text(signal)
    if normalized in {"STRONG_BUY", "BUY"}:
        return "buy"
    if normalized in {"REDUCE", "EXIT"}:
        return "sell"
    return "hold"


def _as_price_frame(prices: pd.Series | pd.DataFrame) -> pd.DataFrame:
    if isinstance(prices, pd.Series):
        frame = pd.DataFrame({"close": prices})
    else:
        frame = prices.copy()
    frame.columns = [str(col).lower() for col in frame.columns]
    if "close" not in frame.columns and len(frame.columns) == 1:
        frame.columns = ["close"]
    if "close" not in frame.columns:
        raise ValueError("Price history must contain a close column")
    return frame.sort_index()


def _ema(series: pd.Series, window: int) -> pd.Series:
    return series.ewm(span=window, adjust=False, min_periods=window).mean()


def _slope_percent(series: pd.Series, periods: int = 5) -> float | None:
    if len(series.dropna()) <= periods:
        return None
    current = _safe_float(series.iloc[-1], None)
    previous = _safe_float(series.iloc[-1 - periods], None)
    if current is None or previous in {None, 0.0}:
        return None
    return ((current - previous) / abs(previous)) * 100.0


def _rsi(series: pd.Series, window: int = RSI_WINDOW) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50.0)


def _macd(series: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    fast = _ema(series, MACD_FAST)
    slow = _ema(series, MACD_SLOW)
    line = fast - slow
    signal = line.ewm(span=MACD_SIGNAL, adjust=False, min_periods=MACD_SIGNAL).mean()
    histogram = line - signal
    return line, signal, histogram


def _roc_percent(series: pd.Series, periods: int = ROC_WINDOW) -> pd.Series:
    return (series / series.shift(periods) - 1.0) * 100.0


def _atr(frame: pd.DataFrame, window: int = ATR_WINDOW) -> pd.Series:
    high = frame.get("high", frame["close"])
    low = frame.get("low", frame["close"])
    close = frame["close"]
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            (high - low).abs(),
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(window=window, min_periods=window).mean()


def _realized_volatility(series: pd.Series, window: int = 20) -> float | None:
    returns = series.pct_change().dropna()
    if len(returns) < window:
        return None
    return _safe_float(returns.tail(window).std() * math.sqrt(252) * 100.0, None)


def _distance_percent(current: float | None, reference: float | None) -> float | None:
    if current is None or reference in {None, 0.0}:
        return None
    return ((current - reference) / abs(reference)) * 100.0


def _rolling_drawdown_percent(series: pd.Series, window: int = 60) -> float | None:
    if len(series) < 2:
        return None
    lookback = series.tail(window)
    running_max = lookback.cummax()
    drawdown = (lookback / running_max - 1.0) * 100.0
    return _safe_float(drawdown.min(), None)


def _history_sufficient(frame: pd.DataFrame, minimum_rows: int = 210) -> bool:
    return len(frame.dropna(subset=["close"])) >= minimum_rows


def trend_factor(frame: pd.DataFrame) -> FactorScore:
    close = frame["close"]
    if len(close.dropna()) < 200:
        return FactorScore(
            name="trend",
            score=50.0,
            status="unavailable",
            positive_reasons=[],
            negative_reasons=["Insufficient price history for long-term trend assessment"],
            warnings=["Need at least 200 observations for full trend scoring"],
            raw_values={},
            available=False,
        )

    ema20 = _ema(close, 20)
    ema50 = _ema(close, 50)
    ema200 = _ema(close, 200)
    latest_close = _safe_float(close.iloc[-1], 0.0)
    latest_ema20 = _safe_float(ema20.iloc[-1], 0.0)
    latest_ema50 = _safe_float(ema50.iloc[-1], 0.0)
    latest_ema200 = _safe_float(ema200.iloc[-1], 0.0)
    slope20 = _slope_percent(ema20)
    slope50 = _slope_percent(ema50)
    slope200 = _slope_percent(ema200)
    long_distance = _distance_percent(latest_close, latest_ema200)
    positive: list[str] = []
    negative: list[str] = []
    score = 50.0

    if latest_close > latest_ema20:
        score += 10
        positive.append("Price is above the 20-day EMA")
    else:
        score -= 10
        negative.append("Price is below the 20-day EMA")
    if latest_ema20 > latest_ema50:
        score += 12
        positive.append("EMA 20 is above EMA 50")
    else:
        score -= 12
        negative.append("EMA 20 is below EMA 50")
    if latest_ema50 > latest_ema200:
        score += 14
        positive.append("EMA 50 is above EMA 200")
    else:
        score -= 14
        negative.append("EMA 50 is below EMA 200")
    for slope, label in ((slope20, "EMA 20"), (slope50, "EMA 50"), (slope200, "EMA 200")):
        if slope is None:
            continue
        if slope > 0.2:
            score += 4
            positive.append(f"{label} is rising")
        elif slope < -0.2:
            score -= 4
            negative.append(f"{label} is falling")
    if long_distance is not None:
        if long_distance > 0:
            score += min(long_distance * 0.35, 10)
        else:
            score += max(long_distance * 0.45, -15)
        if abs(long_distance) > 15:
            negative.append("Price is extended far from the 200-day EMA")

    status = "bullish" if score >= 65 else "bearish" if score <= 35 else "mixed"
    return FactorScore(
        name="trend",
        score=_clamp(score),
        status=status,
        positive_reasons=positive,
        negative_reasons=negative,
        warnings=[],
        raw_values={
            "close": latest_close,
            "ema20": latest_ema20,
            "ema50": latest_ema50,
            "ema200": latest_ema200,
            "ema20_slope_pct": slope20,
            "ema50_slope_pct": slope50,
            "ema200_slope_pct": slope200,
            "distance_from_ema200_pct": long_distance,
        },
    )


def momentum_factor(frame: pd.DataFrame) -> FactorScore:
    close = frame["close"]
    if len(close.dropna()) < max(RSI_WINDOW, MACD_SLOW, ROC_WINDOW) + 5:
        return FactorScore(
            name="momentum",
            score=50.0,
            status="unavailable",
            positive_reasons=[],
            negative_reasons=["Insufficient history for momentum indicators"],
            warnings=["Momentum indicators require additional warm-up history"],
            raw_values={},
            available=False,
        )
    rsi = _rsi(close)
    macd_line, macd_signal, macd_histogram = _macd(close)
    roc = _roc_percent(close)
    latest_rsi = _safe_float(rsi.iloc[-1], 50.0)
    latest_macd = _safe_float(macd_line.iloc[-1], 0.0)
    latest_macd_signal = _safe_float(macd_signal.iloc[-1], 0.0)
    latest_hist = _safe_float(macd_histogram.iloc[-1], 0.0)
    latest_roc = _safe_float(roc.iloc[-1], 0.0)
    positive: list[str] = []
    negative: list[str] = []
    warnings: list[str] = []
    score = 50.0

    if 52 <= latest_rsi <= 68:
        score += 18
        positive.append("RSI shows positive but not extreme momentum")
    elif 68 < latest_rsi <= 78:
        score += 6
        negative.append("RSI is approaching overextended territory")
    elif latest_rsi > 78:
        score -= 8
        negative.append("RSI is severely overextended")
    elif latest_rsi < 35:
        score -= 10
        negative.append("RSI remains weak")

    if latest_macd > latest_macd_signal:
        score += 12
        positive.append("MACD remains bullish")
    else:
        score -= 12
        negative.append("MACD is below its signal line")
    if latest_hist > 0:
        score += min(latest_hist * 25.0, 8)
        positive.append("MACD histogram is positive")
    elif latest_hist < 0:
        score -= min(abs(latest_hist) * 25.0, 8)
        negative.append("MACD histogram is negative")
    if latest_roc > 0:
        score += min(latest_roc * 1.5, 10)
    else:
        score += max(latest_roc * 1.8, -12)
    if abs(latest_roc) > 12:
        warnings.append("Short-term price move is unusually extended")

    status = "bullish" if score >= 65 else "bearish" if score <= 35 else "mixed"
    return FactorScore(
        name="momentum",
        score=_clamp(score),
        status=status,
        positive_reasons=positive,
        negative_reasons=negative,
        warnings=warnings,
        raw_values={
            "rsi14": latest_rsi,
            "macd_line": latest_macd,
            "macd_signal": latest_macd_signal,
            "macd_histogram": latest_hist,
            "roc10_pct": latest_roc,
        },
    )


def volume_factor(frame: pd.DataFrame) -> FactorScore:
    if "volume" not in frame.columns or frame["volume"].dropna().empty:
        return FactorScore(
            name="volume",
            score=50.0,
            status="unavailable",
            positive_reasons=[],
            negative_reasons=[],
            warnings=["Reliable volume data is unavailable"],
            raw_values={"volume_available": False},
            available=False,
        )

    volume = frame["volume"].astype(float)
    close = frame["close"].astype(float)
    avg20 = volume.rolling(window=20, min_periods=20).mean()
    if pd.isna(avg20.iloc[-1]):
        return FactorScore(
            name="volume",
            score=50.0,
            status="unavailable",
            positive_reasons=[],
            negative_reasons=["Insufficient volume history for confirmation analysis"],
            warnings=["Need 20 observations for volume factor"],
            raw_values={"volume_available": True},
            available=False,
        )

    current_volume = _safe_float(volume.iloc[-1], 0.0)
    average_volume = _safe_float(avg20.iloc[-1], 0.0)
    ratio = current_volume / average_volume if average_volume else 1.0
    volume_trend = _safe_float(volume.tail(5).mean() / average_volume, 1.0)
    price_change = _safe_float(close.pct_change().iloc[-1], 0.0) * 100.0
    score = 50.0
    positive: list[str] = []
    negative: list[str] = []

    if price_change > 0 and ratio >= 1.1:
        score += 20
        positive.append("Price strength is supported by above-average volume")
    elif price_change > 0 and ratio < 0.9:
        score -= 15
        negative.append("Price is rising without volume confirmation")
    if ratio >= 1.4:
        score += 10
        positive.append("Current volume is well above the 20-day average")
    elif ratio <= 0.7:
        score -= 10
        negative.append("Current volume is light versus the 20-day average")
    if volume_trend > 1.05:
        score += 6
        positive.append("Recent volume trend is improving")
    elif volume_trend < 0.9:
        score -= 6
        negative.append("Recent volume trend is fading")

    status = "confirmed" if score >= 65 else "weak" if score <= 35 else "mixed"
    return FactorScore(
        name="volume",
        score=_clamp(score),
        status=status,
        positive_reasons=positive,
        negative_reasons=negative,
        warnings=[],
        raw_values={
            "current_volume": current_volume,
            "average_volume20": average_volume,
            "volume_ratio": ratio,
            "volume_trend_ratio": volume_trend,
            "latest_price_change_pct": price_change,
            "volume_available": True,
        },
    )


def volatility_factor(frame: pd.DataFrame) -> FactorScore:
    close = frame["close"].astype(float)
    if len(close.dropna()) < 20:
        return FactorScore(
            name="volatility",
            score=50.0,
            status="unavailable",
            positive_reasons=[],
            negative_reasons=["Insufficient history for volatility analysis"],
            warnings=["Need at least 20 observations for volatility factor"],
            raw_values={},
            available=False,
        )

    atr = _atr(frame)
    atr_value = _safe_float(atr.iloc[-1], None)
    latest_close = _safe_float(close.iloc[-1], None)
    atr_pct = (atr_value / latest_close * 100.0) if atr_value is not None and latest_close else None
    realized_vol = _realized_volatility(close)
    recent_range = _safe_float(close.tail(10).pct_change().abs().mean(), 0.0) * 100.0
    score = 70.0
    positive: list[str] = []
    negative: list[str] = []

    if atr_pct is not None:
        if 1.0 <= atr_pct <= 4.5:
            positive.append("ATR indicates stable, tradeable movement")
        elif atr_pct > 6.0:
            score -= 22
            negative.append("ATR indicates extreme volatility")
        elif atr_pct < 0.4:
            score -= 15
            negative.append("Price movement is unusually compressed")
        score -= max(0.0, abs(atr_pct - 2.4) * 3.0)
    if realized_vol is not None:
        if realized_vol > 45:
            score -= 18
            negative.append("Realized volatility is elevated")
        elif realized_vol < 8:
            score -= 10
            negative.append("Realized volatility is unusually low")
        else:
            positive.append("Realized volatility remains contained")
    if recent_range < 0.15:
        score -= 8

    status = "stable" if score >= 65 else "unstable" if score <= 35 else "mixed"
    return FactorScore(
        name="volatility",
        score=_clamp(score),
        status=status,
        positive_reasons=positive,
        negative_reasons=negative,
        warnings=[],
        raw_values={
            "atr14": atr_value,
            "atr_pct": atr_pct,
            "realized_volatility_pct": realized_vol,
            "avg_absolute_return10_pct": recent_range,
        },
    )


def market_regime_factor(frame: pd.DataFrame) -> FactorScore:
    close = frame["close"].astype(float)
    if len(close.dropna()) < 200:
        return FactorScore(
            name="market_regime",
            score=50.0,
            status="unavailable",
            positive_reasons=[],
            negative_reasons=["Insufficient benchmark history for regime analysis"],
            warnings=["Need 200 observations for full market regime scoring"],
            raw_values={"regime": "unknown"},
            available=False,
        )

    ema50 = _ema(close, 50)
    ema200 = _ema(close, 200)
    latest_close = _safe_float(close.iloc[-1], 0.0)
    latest_ema50 = _safe_float(ema50.iloc[-1], 0.0)
    latest_ema200 = _safe_float(ema200.iloc[-1], 0.0)
    slope50 = _slope_percent(ema50)
    slope200 = _slope_percent(ema200)
    realized_vol = _realized_volatility(close)
    drawdown = _rolling_drawdown_percent(close)
    score = 50.0
    regime = "sideways"
    positive: list[str] = []
    negative: list[str] = []

    if latest_close > latest_ema50 > latest_ema200 and (slope50 or 0.0) > 0 and (slope200 or 0.0) >= 0:
        regime = "strong_bull"
        score = 88.0
        positive.append("Benchmark is above the 50-day and 200-day EMAs")
    elif latest_close > latest_ema200:
        regime = "weak_bull"
        score = 70.0
        positive.append("Benchmark remains above its long-term trend")
    elif latest_close < latest_ema50 < latest_ema200 and (slope50 or 0.0) < 0:
        regime = "strong_bear"
        score = 18.0
        negative.append("Benchmark is below both major trend averages")
    elif latest_close < latest_ema200:
        regime = "weak_bear"
        score = 32.0
        negative.append("Benchmark is below its 200-day EMA")

    if realized_vol is not None and realized_vol > 35:
        regime = "high_volatility_risk_off"
        score = min(score, 25.0)
        negative.append("Benchmark volatility is elevated")
    elif drawdown is not None and drawdown < -8:
        negative.append("Benchmark drawdown remains significant")
        score -= 8

    status = regime
    return FactorScore(
        name="market_regime",
        score=_clamp(score),
        status=status,
        positive_reasons=positive,
        negative_reasons=negative,
        warnings=[],
        raw_values={
            "benchmark_close": latest_close,
            "benchmark_ema50": latest_ema50,
            "benchmark_ema200": latest_ema200,
            "benchmark_ema50_slope_pct": slope50,
            "benchmark_ema200_slope_pct": slope200,
            "benchmark_realized_volatility_pct": realized_vol,
            "benchmark_drawdown_pct": drawdown,
            "regime": regime,
        },
    )


def risk_quality_factor(frame: pd.DataFrame) -> FactorScore:
    close = frame["close"].astype(float)
    if len(close.dropna()) < 60:
        return FactorScore(
            name="risk_quality",
            score=50.0,
            status="unavailable",
            positive_reasons=[],
            negative_reasons=["Insufficient history for risk-quality analysis"],
            warnings=["Need 60 observations for risk-quality scoring"],
            raw_values={},
            available=False,
        )

    atr = _atr(frame)
    atr_value = _safe_float(atr.iloc[-1], None)
    latest_close = _safe_float(close.iloc[-1], None)
    atr_pct = (atr_value / latest_close * 100.0) if atr_value is not None and latest_close else None
    recent_high = _safe_float(close.tail(60).max(), None)
    drawdown_from_high = _distance_percent(latest_close, recent_high)
    downside_returns = close.pct_change().dropna()
    downside_vol = _safe_float(downside_returns[downside_returns < 0].tail(20).std() * math.sqrt(252) * 100.0, None)
    gap_risk = _safe_float(close.pct_change().abs().tail(20).max(), 0.0) * 100.0
    score = 75.0
    positive: list[str] = []
    negative: list[str] = []

    if atr_pct is not None:
        if atr_pct > 6:
            score -= 22
            negative.append("ATR as a percentage of price is elevated")
        elif atr_pct < 0.5:
            score -= 10
            negative.append("Risk profile may be too compressed to be informative")
        else:
            positive.append("ATR percentage is in a manageable range")
    if drawdown_from_high is not None:
        if drawdown_from_high < -12:
            score -= 18
            negative.append("Price is materially below its recent high")
        elif drawdown_from_high > -5:
            score += 6
            positive.append("Price remains relatively close to its recent high")
    if downside_vol is not None and downside_vol > 30:
        score -= 15
        negative.append("Downside volatility is elevated")
    if gap_risk > 4:
        score -= 10
        negative.append("Recent gap risk is elevated")

    status = "clean" if score >= 65 else "fragile" if score <= 35 else "mixed"
    return FactorScore(
        name="risk_quality",
        score=_clamp(score),
        status=status,
        positive_reasons=positive,
        negative_reasons=negative,
        warnings=[],
        raw_values={
            "atr_pct": atr_pct,
            "drawdown_from_60d_high_pct": drawdown_from_high,
            "downside_volatility_pct": downside_vol,
            "gap_risk_pct": gap_risk,
        },
    )


def _validate_weights(weights: dict[str, float]) -> dict[str, float]:
    normalized = {name: float(value) for name, value in weights.items()}
    if any(value < 0 for value in normalized.values()):
        raise ValueError("Factor weights must be non-negative")
    total = sum(normalized.values())
    if not math.isclose(total, 1.0, rel_tol=1e-9, abs_tol=1e-9):
        raise ValueError(f"Factor weights must sum to 1.0, got {total:.6f}")
    return normalized


def _classify_signal(score: float, thresholds: dict[str, float], previous_signal: str | None = None, hysteresis_buffer: float = 2.5) -> str:
    previous = _normalize_signal_text(previous_signal) if previous_signal is not None else ""
    ordered = [
        ("STRONG_BUY", thresholds["strong_buy"]),
        ("BUY", thresholds["buy"]),
        ("HOLD", thresholds["hold"]),
        ("REDUCE", thresholds["reduce"]),
        ("EXIT", 0.0),
    ]
    current = "EXIT"
    for label, minimum in ordered:
        if score >= minimum:
            current = label
            break

    if not previous or previous == current:
        return current

    boundary_map = {
        "STRONG_BUY": thresholds["strong_buy"],
        "BUY": thresholds["buy"],
        "HOLD": thresholds["hold"],
        "REDUCE": thresholds["reduce"],
        "EXIT": thresholds["reduce"],
    }
    current_boundary = boundary_map.get(current, thresholds["hold"])
    previous_boundary = boundary_map.get(previous, thresholds["hold"])
    distance = min(abs(score - current_boundary), abs(score - previous_boundary))
    if distance < hysteresis_buffer:
        return previous
    return current


def _confidence_score(
    overall_score: float,
    factor_scores: dict[str, FactorScore],
    thresholds: dict[str, float],
    signal: str,
) -> float:
    available_scores = [factor.score for factor in factor_scores.values() if factor.available]
    if not available_scores:
        return 25.0
    mean_score = sum(available_scores) / len(available_scores)
    variance = sum((score - mean_score) ** 2 for score in available_scores) / len(available_scores)
    disagreement_penalty = min(math.sqrt(variance) * 0.9, 25.0)
    availability_penalty = (len(factor_scores) - len(available_scores)) * 6.0
    bullish_votes = len([score for score in available_scores if score >= 60.0])
    bearish_votes = len([score for score in available_scores if score <= 40.0])
    neutral_votes = len(available_scores) - bullish_votes - bearish_votes
    dominant_votes = max(bullish_votes, bearish_votes, neutral_votes)
    agreement_ratio = dominant_votes / max(len(available_scores), 1)
    agreement_bonus = agreement_ratio * 12.0
    disagreement_penalty += (1.0 - agreement_ratio) * 14.0
    threshold_targets = [thresholds["strong_buy"], thresholds["buy"], thresholds["hold"], thresholds["reduce"]]
    threshold_distance = min(abs(overall_score - threshold) for threshold in threshold_targets)
    threshold_bonus = min(threshold_distance * 0.8, 12.0)
    stability_bonus = 0.0
    regime = factor_scores.get("market_regime")
    if regime and regime.available and regime.status in {"strong_bull", "weak_bull", "weak_bear", "strong_bear"}:
        stability_bonus += 8.0
    momentum = factor_scores.get("momentum")
    if momentum and momentum.available and abs(_safe_float(momentum.raw_values.get("macd_histogram"), 0.0)) > 0.05:
        stability_bonus += 5.0
    base = 60.0 + threshold_bonus + stability_bonus + agreement_bonus
    if signal == "HOLD":
        base -= 5.0
    confidence = base - disagreement_penalty - availability_penalty
    return _clamp(confidence)


def _compose_result(
    symbol: str,
    factor_scores: dict[str, FactorScore],
    weights: dict[str, float],
    thresholds: dict[str, float],
    previous_signal: str | None,
    timestamp: str | None = None,
    history_sufficient: bool = True,
) -> dict[str, Any]:
    validated_weights = _validate_weights(weights)
    available_weights = {name: validated_weights[name] for name, factor in factor_scores.items() if factor.available and name in validated_weights}
    if not available_weights:
        available_weights = {"trend": 1.0}
        factor_scores = dict(factor_scores)
        factor_scores["trend"] = FactorScore(
            name="trend",
            score=50.0,
            status="unavailable",
            positive_reasons=[],
            negative_reasons=["No factors available for composite scoring"],
            warnings=["Composite engine fell back to neutral score"],
            raw_values={},
            available=False,
        )
    weight_total = sum(available_weights.values())
    normalized_weights = {name: value / weight_total for name, value in available_weights.items()}
    overall_score = sum(factor_scores[name].score * normalized_weights[name] for name in normalized_weights)
    signal = _classify_signal(overall_score, thresholds, previous_signal=previous_signal)
    confidence = _confidence_score(overall_score, factor_scores, thresholds, signal)
    warnings: list[str] = []
    reasons: list[str] = []
    for factor in factor_scores.values():
        reasons.extend(factor.positive_reasons[:2])
        warnings.extend(factor.warnings)
        if len(reasons) >= 6:
            break
    if not history_sufficient:
        signal = "HOLD"
        confidence = min(confidence, 35.0)
        warnings.append("Insufficient history for a fully qualified research signal")
    return {
        "symbol": symbol,
        "timestamp": timestamp or _utc_now_iso(),
        "overall_score": round(overall_score, 2),
        "confidence": round(confidence, 2),
        "signal": signal,
        "legacy_signal": _legacy_signal_text(signal),
        "regime": factor_scores.get("market_regime").raw_values.get("regime", "unknown") if factor_scores.get("market_regime") else "unknown",
        "component_scores": {name: round(factor.score, 2) for name, factor in factor_scores.items()},
        "factors": {name: factor.as_dict() for name, factor in factor_scores.items()},
        "reasons": reasons[:6],
        "warnings": list(dict.fromkeys(warnings)),
        "data_quality": {
            "volume_available": bool(factor_scores.get("volume") and factor_scores["volume"].available),
            "history_sufficient": history_sufficient,
            "available_factor_count": len([factor for factor in factor_scores.values() if factor.available]),
        },
        "weights_used": normalized_weights,
    }


def generate_explainable_summary(result: dict[str, Any]) -> str:
    positive: list[str] = []
    caution: list[str] = []
    for factor in (result.get("factors") or {}).values():
        positive.extend(list(factor.get("positive_reasons") or [])[:1])
        caution.extend(list(factor.get("negative_reasons") or [])[:1])
    lines = [
        f"Signal: {result.get('signal', 'HOLD')}",
        f"Score: {result.get('overall_score', 0):.1f}",
        f"Confidence: {result.get('confidence', 0):.1f}%",
        "",
        "Positive:",
    ]
    lines.extend(f"- {text}" for text in positive[:4] or ["- No strong positive factors were available"])
    lines.append("")
    lines.append("Caution:")
    lines.extend(f"- {text}" for text in caution[:4] or ["- No major cautions were identified"])
    return "\n".join(lines)


def score_symbol(
    prices: pd.Series | pd.DataFrame,
    benchmark_prices: pd.Series | pd.DataFrame | None,
    symbol: str,
    weights: dict[str, float],
    thresholds: dict[str, float],
    previous_signal: str | None = None,
    hysteresis_buffer: float = 2.5,
) -> dict[str, Any]:
    frame = _as_price_frame(prices)
    benchmark_frame = _as_price_frame(benchmark_prices) if benchmark_prices is not None else frame
    factors = {
        "trend": trend_factor(frame),
        "momentum": momentum_factor(frame),
        "volume": volume_factor(frame),
        "volatility": volatility_factor(frame),
        "market_regime": market_regime_factor(benchmark_frame),
        "risk_quality": risk_quality_factor(frame),
    }
    result = _compose_result(
        symbol=symbol,
        factor_scores=factors,
        weights=weights,
        thresholds=thresholds,
        previous_signal=previous_signal,
        timestamp=str(frame.index[-1]) if len(frame.index) else _utc_now_iso(),
        history_sufficient=_history_sufficient(frame),
    )
    result["signal"] = _classify_signal(result["overall_score"], thresholds, previous_signal=previous_signal, hysteresis_buffer=hysteresis_buffer)
    result["legacy_signal"] = _legacy_signal_text(result["signal"])
    result["summary_text"] = generate_explainable_summary(result)
    return result