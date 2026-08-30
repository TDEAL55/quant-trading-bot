from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd


QUANTUM_SCORE_VERSION = os.getenv("QUANTUM_SCORE_VERSION", "quantum_v1")

DEFAULT_COMPONENT_WEIGHTS = {
    "trend_strength": 20.0,
    "relative_strength": 15.0,
    "momentum_quality": 15.0,
    "volume_confirmation": 10.0,
    "volatility_quality": 10.0,
    "liquidity_quality": 10.0,
    "risk_reward_quality": 10.0,
    "market_regime_alignment": 10.0,
}

ENV_WEIGHT_MAP = {
    "trend_strength": "QUANTUM_WEIGHT_TREND_STRENGTH",
    "relative_strength": "QUANTUM_WEIGHT_RELATIVE_STRENGTH",
    "momentum_quality": "QUANTUM_WEIGHT_MOMENTUM_QUALITY",
    "volume_confirmation": "QUANTUM_WEIGHT_VOLUME_CONFIRMATION",
    "volatility_quality": "QUANTUM_WEIGHT_VOLATILITY_QUALITY",
    "liquidity_quality": "QUANTUM_WEIGHT_LIQUIDITY_QUALITY",
    "risk_reward_quality": "QUANTUM_WEIGHT_RISK_REWARD_QUALITY",
    "market_regime_alignment": "QUANTUM_WEIGHT_MARKET_REGIME_ALIGNMENT",
}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, float(value)))


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        parsed = float(value)
        if math.isnan(parsed):
            return default
        return parsed
    except (TypeError, ValueError):
        return default


def _as_price_frame(prices: pd.Series | pd.DataFrame | None) -> pd.DataFrame:
    if prices is None:
        return pd.DataFrame(columns=["close"])
    if isinstance(prices, pd.Series):
        frame = pd.DataFrame({"close": prices})
    else:
        frame = prices.copy()
    frame.columns = [str(col).lower() for col in frame.columns]
    if "close" not in frame.columns and len(frame.columns) == 1:
        frame.columns = ["close"]
    if "close" not in frame.columns:
        return pd.DataFrame(columns=["close"])
    return frame.sort_index()


def _ema(series: pd.Series, window: int) -> pd.Series:
    return series.ewm(span=window, adjust=False, min_periods=window).mean()


def _rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    result = 100 - (100 / (1 + rs))
    return result.fillna(50.0)


def _macd(series: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    fast = _ema(series, 12)
    slow = _ema(series, 26)
    line = fast - slow
    signal = line.ewm(span=9, adjust=False, min_periods=9).mean()
    hist = line - signal
    return line, signal, hist


def _atr(frame: pd.DataFrame, window: int = 14) -> pd.Series:
    high = frame.get("high", frame["close"])
    low = frame.get("low", frame["close"])
    close = frame["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(window=window, min_periods=window).mean()


def _business_days_stale(index: pd.Index) -> int:
    if len(index) == 0:
        return 9_999
    ts = pd.Timestamp(index[-1])
    if ts.tzinfo is None:
        ts = ts.tz_localize(timezone.utc)
    now = datetime.now(timezone.utc)
    if ts.date() > now.date():
        return 0
    return max(len(pd.bdate_range(start=ts.date(), end=now.date())) - 1, 0)


def load_quantum_component_weights() -> dict[str, float]:
    weights = dict(DEFAULT_COMPONENT_WEIGHTS)
    for key, env_name in ENV_WEIGHT_MAP.items():
        raw = os.getenv(env_name)
        if raw is None or str(raw).strip() == "":
            continue
        parsed = _safe_float(raw, None)
        if parsed is not None:
            weights[key] = float(parsed)
    total = sum(weights.values())
    if not math.isclose(total, 100.0, rel_tol=1e-9, abs_tol=1e-9):
        raise ValueError(f"Quantum component weights must sum to 100, got {total:.6f}")
    if any(value < 0 for value in weights.values()):
        raise ValueError("Quantum component weights must be non-negative")
    return weights


def _trend_strength(frame: pd.DataFrame) -> tuple[float | None, dict[str, Any], list[str]]:
    close = frame.get("close", pd.Series(dtype=float)).astype(float)
    if len(close.dropna()) < 200:
        return None, {}, ["trend strength unavailable: need >= 200 bars"]

    ema20 = _ema(close, 20)
    ema50 = _ema(close, 50)
    ema200 = _ema(close, 200)
    latest = _safe_float(close.iloc[-1], 0.0) or 0.0
    e20 = _safe_float(ema20.iloc[-1], 0.0) or 0.0
    e50 = _safe_float(ema50.iloc[-1], 0.0) or 0.0
    e200 = _safe_float(ema200.iloc[-1], 0.0) or 0.0
    s20 = _safe_float(((ema20.iloc[-1] / ema20.iloc[-6]) - 1.0) * 100.0, 0.0) if len(ema20.dropna()) > 6 else 0.0
    s50 = _safe_float(((ema50.iloc[-1] / ema50.iloc[-6]) - 1.0) * 100.0, 0.0) if len(ema50.dropna()) > 6 else 0.0
    s200 = _safe_float(((ema200.iloc[-1] / ema200.iloc[-6]) - 1.0) * 100.0, 0.0) if len(ema200.dropna()) > 6 else 0.0
    distance = ((latest - e200) / e200 * 100.0) if e200 else 0.0

    score = 50.0
    score += 10.0 if latest > e20 else -10.0
    score += 12.0 if e20 > e50 else -12.0
    score += 14.0 if e50 > e200 else -14.0
    score += _clamp(s20 * 6.0, -6.0, 6.0)
    score += _clamp(s50 * 6.0, -6.0, 6.0)
    score += _clamp(s200 * 5.0, -5.0, 5.0)

    # Penalize overextension so extreme distance does not over-score.
    if abs(distance) > 15:
        score -= min(abs(distance) - 15.0, 18.0)

    return (
        _clamp(score),
        {
            "close": latest,
            "ema20": e20,
            "ema50": e50,
            "ema200": e200,
            "ema20_slope_pct": s20,
            "ema50_slope_pct": s50,
            "ema200_slope_pct": s200,
            "distance_from_ema200_pct": distance,
        },
        [],
    )


def _relative_strength(frame: pd.DataFrame, benchmark: pd.DataFrame) -> tuple[float | None, dict[str, Any], list[str]]:
    close = frame.get("close", pd.Series(dtype=float)).astype(float)
    bench_close = benchmark.get("close", pd.Series(dtype=float)).astype(float)
    if len(close.dropna()) < 80 or len(bench_close.dropna()) < 80:
        return None, {}, ["relative strength unavailable: need >= 80 bars for symbol and benchmark"]

    def _ret(series: pd.Series, days: int) -> float | None:
        if len(series.dropna()) <= days:
            return None
        base = _safe_float(series.iloc[-1 - days], None)
        latest = _safe_float(series.iloc[-1], None)
        if base in {None, 0.0} or latest is None:
            return None
        return ((latest / base) - 1.0) * 100.0

    r5 = (_ret(close, 5) or 0.0) - (_ret(bench_close, 5) or 0.0)
    r20 = (_ret(close, 20) or 0.0) - (_ret(bench_close, 20) or 0.0)
    r60 = (_ret(close, 60) or 0.0) - (_ret(bench_close, 60) or 0.0)

    rs_series = (close / close.shift(20)) - (bench_close / bench_close.shift(20))
    rs_trend = _safe_float((rs_series.iloc[-1] - rs_series.iloc[-11]) * 100.0, 0.0) if len(rs_series.dropna()) > 11 else 0.0

    score = 50.0
    score += _clamp(r5 * 1.8, -14.0, 14.0)
    score += _clamp(r20 * 1.6, -16.0, 16.0)
    score += _clamp(r60 * 1.3, -16.0, 16.0)
    score += _clamp(rs_trend * 0.9, -10.0, 10.0)

    return (
        _clamp(score),
        {
            "relative_return_5d_pct": r5,
            "relative_return_20d_pct": r20,
            "relative_return_60d_pct": r60,
            "relative_strength_trend": rs_trend,
        },
        [],
    )


def _momentum_quality(frame: pd.DataFrame) -> tuple[float | None, dict[str, Any], list[str]]:
    close = frame.get("close", pd.Series(dtype=float)).astype(float)
    if len(close.dropna()) < 60:
        return None, {}, ["momentum quality unavailable: need >= 60 bars"]

    rsi = _rsi(close)
    macd_line, macd_signal, macd_hist = _macd(close)
    roc10 = ((close / close.shift(10)) - 1.0) * 100.0

    latest_rsi = _safe_float(rsi.iloc[-1], 50.0) or 50.0
    latest_macd = _safe_float(macd_line.iloc[-1], 0.0) or 0.0
    latest_signal = _safe_float(macd_signal.iloc[-1], 0.0) or 0.0
    latest_hist = _safe_float(macd_hist.iloc[-1], 0.0) or 0.0
    latest_roc = _safe_float(roc10.iloc[-1], 0.0) or 0.0

    warnings: list[str] = []
    score = 50.0

    if 52 <= latest_rsi <= 68:
        score += 20
    elif 68 < latest_rsi <= 78:
        score += 8
        warnings.append("momentum nearing overextended RSI")
    elif latest_rsi > 78:
        score -= 14
        warnings.append("overextended RSI")
    elif latest_rsi < 35:
        score -= 10

    score += 12 if latest_macd > latest_signal else -12
    score += _clamp(latest_hist * 25.0, -8.0, 8.0)
    score += _clamp(latest_roc * 1.5, -14.0, 14.0)

    if abs(latest_roc) > 12:
        score -= min(abs(latest_roc) - 12.0, 10.0)
        warnings.append("rate-of-change is unstable")

    return (
        _clamp(score),
        {
            "rsi14": latest_rsi,
            "macd_line": latest_macd,
            "macd_signal": latest_signal,
            "macd_histogram": latest_hist,
            "roc10_pct": latest_roc,
        },
        warnings,
    )


def _volume_confirmation(frame: pd.DataFrame) -> tuple[float | None, dict[str, Any], list[str]]:
    if "volume" not in frame.columns:
        return None, {}, ["volume confirmation unavailable: volume column missing"]
    volume = frame["volume"].astype(float)
    close = frame["close"].astype(float)
    if len(volume.dropna()) < 25:
        return None, {}, ["volume confirmation unavailable: need >= 25 bars"]

    avg20 = volume.rolling(20, min_periods=20).mean()
    current = _safe_float(volume.iloc[-1], 0.0) or 0.0
    avg = _safe_float(avg20.iloc[-1], 0.0) or 0.0
    ratio = current / avg if avg > 0 else 0.0
    trend_ratio = (_safe_float(volume.tail(5).mean(), 0.0) or 0.0) / avg if avg > 0 else 0.0
    px_chg = ((_safe_float(close.iloc[-1], 0.0) or 0.0) / (_safe_float(close.iloc[-2], 1.0) or 1.0) - 1.0) * 100.0

    score = 50.0
    warnings: list[str] = []

    if px_chg > 0 and ratio >= 1.1:
        score += 18
    elif px_chg > 0 and ratio < 0.9:
        score -= 14

    # Cap one-day spike reward to avoid poor-quality spike over-rewarding.
    if ratio >= 1.4:
        score += min((ratio - 1.4) * 6.0 + 6.0, 10.0)
    elif ratio <= 0.7:
        score -= 10.0

    score += _clamp((trend_ratio - 1.0) * 25.0, -8.0, 8.0)
    if ratio > 3.5 and trend_ratio < 1.05:
        warnings.append("isolated volume spike detected")
        score -= 6.0

    return (
        _clamp(score),
        {
            "volume_ratio_20d": ratio,
            "volume_trend_ratio": trend_ratio,
            "latest_price_change_pct": px_chg,
        },
        warnings,
    )


def _volatility_quality(frame: pd.DataFrame) -> tuple[float | None, dict[str, Any], list[str]]:
    close = frame.get("close", pd.Series(dtype=float)).astype(float)
    if len(close.dropna()) < 40:
        return None, {}, ["volatility quality unavailable: need >= 40 bars"]

    atr = _atr(frame)
    atr_last = _safe_float(atr.iloc[-1], None)
    close_last = _safe_float(close.iloc[-1], None)
    atr_pct = (atr_last / close_last * 100.0) if atr_last is not None and close_last not in {None, 0.0} else None

    ret = close.pct_change().dropna()
    rv = _safe_float(ret.tail(20).std() * math.sqrt(252) * 100.0, None)
    vol_stability = _safe_float(ret.tail(20).abs().std() * 100.0, 0.0) or 0.0

    score = 72.0
    if atr_pct is None:
        score -= 12.0
    elif 1.0 <= atr_pct <= 4.5:
        score += 8.0
    elif atr_pct < 0.4:
        score -= 16.0
    elif atr_pct > 6.0:
        score -= 20.0
    else:
        score -= abs(atr_pct - 2.5) * 2.0

    if rv is not None:
        if rv < 8.0:
            score -= 10.0
        elif rv > 45.0:
            score -= 16.0
        else:
            score += 6.0

    score -= _clamp(vol_stability * 120.0, 0.0, 10.0)

    return (
        _clamp(score),
        {
            "atr_pct": atr_pct,
            "realized_volatility_pct": rv,
            "volatility_stability": vol_stability,
        },
        [],
    )


def _liquidity_quality(frame: pd.DataFrame) -> tuple[float | None, dict[str, Any], list[str], list[str]]:
    close = frame.get("close", pd.Series(dtype=float)).astype(float)
    if "volume" not in frame.columns or len(close.dropna()) < 20:
        return None, {}, ["liquidity quality unavailable: need close+volume history"], ["liquidity_data_missing"]

    volume = frame["volume"].astype(float)
    adv_shares = _safe_float(volume.tail(20).mean(), 0.0) or 0.0
    adv_dollars = _safe_float((volume.tail(20) * close.tail(20)).mean(), 0.0) or 0.0
    min_price = _safe_float(close.iloc[-1], 0.0) or 0.0

    spread_proxy = None
    if "high" in frame.columns and "low" in frame.columns:
        high = frame["high"].astype(float)
        low = frame["low"].astype(float)
        mid = (high + low) / 2.0
        spread_proxy = _safe_float((((high - low) / mid.replace(0, pd.NA)).tail(20).median()) * 100.0, None)

    warnings: list[str] = []
    rejections: list[str] = []

    score = 40.0
    score += _clamp(math.log10(max(adv_dollars, 1.0)) * 12.0 - 60.0, 0.0, 40.0)
    score += _clamp(math.log10(max(adv_shares, 1.0)) * 8.0 - 32.0, 0.0, 20.0)

    if spread_proxy is not None:
        if spread_proxy <= 0.8:
            score += 20.0
        elif spread_proxy <= 2.0:
            score += 10.0
        else:
            score -= min((spread_proxy - 2.0) * 5.0, 20.0)
    else:
        warnings.append("spread proxy unavailable")
        score -= 5.0

    if min_price < 5.0:
        rejections.append("minimum_price_check_failed")
        score -= 20.0
    if adv_dollars < 20_000_000:
        rejections.append("average_dollar_volume_below_minimum")
        score -= 20.0

    return (
        _clamp(score),
        {
            "average_daily_dollar_volume": adv_dollars,
            "average_share_volume": adv_shares,
            "spread_proxy_pct": spread_proxy,
            "latest_price": min_price,
        },
        warnings,
        rejections,
    )


def _risk_reward_quality(frame: pd.DataFrame) -> tuple[float | None, dict[str, Any], list[str], list[str]]:
    close = frame.get("close", pd.Series(dtype=float)).astype(float)
    if len(close.dropna()) < 30:
        return None, {}, ["risk/reward quality unavailable: need >= 30 bars"], ["risk_reward_data_missing"]

    latest = _safe_float(close.iloc[-1], 0.0) or 0.0
    atr = _atr(frame)
    atr_last = _safe_float(atr.iloc[-1], None)
    if atr_last is None or latest <= 0:
        return None, {}, ["risk/reward quality unavailable: ATR not available"], ["risk_reward_invalid"]

    stop = max(latest - (2.0 * atr_last), latest * 0.93)
    target = latest + (2.5 * atr_last)
    risk = latest - stop
    reward = target - latest

    rejections: list[str] = []
    warnings: list[str] = []

    if risk <= 0 or reward <= 0:
        rejections.append("invalid_reward_risk_structure")
        return 0.0, {
            "entry": latest,
            "stop": stop,
            "target": target,
            "risk_distance": risk,
            "reward_distance": reward,
            "reward_risk_ratio": 0.0,
        }, warnings, rejections

    rr = reward / max(risk, 1e-9)
    max_loss_pct = (risk / latest) * 100.0
    max_risk_per_trade = _safe_float(os.getenv("MAX_RISK_PER_TRADE_PERCENT", "1.0"), 1.0) or 1.0
    max_position_equity_percent = _safe_float(os.getenv("MAX_POSITION_EQUITY_PERCENT", "10"), 10.0) or 10.0

    # Feasibility proxy: risk-per-trade budget should support default position sizing assumptions.
    feasible = max_loss_pct <= max(0.1, max_risk_per_trade * (max_position_equity_percent / 100.0) * 10.0)

    score = 25.0
    score += _clamp((rr - 1.0) * 35.0, -20.0, 45.0)
    if not feasible:
        score -= 18.0
        warnings.append("position-size feasibility is weak")
    if rr < 1.2:
        rejections.append("reward_risk_below_minimum")

    return (
        _clamp(score),
        {
            "entry": latest,
            "stop": stop,
            "target": target,
            "risk_distance": risk,
            "reward_distance": reward,
            "reward_risk_ratio": rr,
            "max_loss_percent": max_loss_pct,
            "position_size_feasible": feasible,
        },
        warnings,
        rejections,
    )


def _market_regime_alignment(
    benchmark: pd.DataFrame,
    strategy_id: str | None = None,
) -> tuple[float | None, dict[str, Any], list[str]]:
    close = benchmark.get("close", pd.Series(dtype=float)).astype(float)
    if len(close.dropna()) < 200:
        return None, {}, ["market regime alignment unavailable: need >= 200 benchmark bars"]

    ema50 = _ema(close, 50)
    ema200 = _ema(close, 200)
    latest = _safe_float(close.iloc[-1], 0.0) or 0.0
    e50 = _safe_float(ema50.iloc[-1], 0.0) or 0.0
    e200 = _safe_float(ema200.iloc[-1], 0.0) or 0.0
    ret20 = ((latest / (_safe_float(close.iloc[-21], latest) or latest)) - 1.0) * 100.0 if len(close.dropna()) > 21 else 0.0
    rv = _safe_float(close.pct_change().dropna().tail(20).std() * math.sqrt(252) * 100.0, 0.0) or 0.0

    score = 50.0
    regime = "neutral"
    if latest > e50 > e200:
        regime = "bull"
        score += 26.0
    elif latest > e200:
        regime = "weak_bull"
        score += 12.0
    elif latest < e50 < e200:
        regime = "bear"
        score -= 24.0
    else:
        regime = "sideways"

    score += _clamp(ret20 * 0.8, -12.0, 12.0)
    if rv > 35.0:
        regime = "high_volatility_risk_off"
        score -= 16.0

    # Strategy-specific compatibility overlay.
    sid = str(strategy_id or "")
    if sid == "stock_mean_reversion_v2" and regime in {"sideways", "weak_bull"}:
        score += 6.0
    if sid == "stock_trend_ensemble_v2" and regime == "bull":
        score += 6.0
    if sid == "stock_trend_ensemble_v2" and regime in {"bear", "high_volatility_risk_off"}:
        score -= 8.0

    return (
        _clamp(score),
        {
            "benchmark_close": latest,
            "benchmark_ema50": e50,
            "benchmark_ema200": e200,
            "benchmark_momentum_20d_pct": ret20,
            "benchmark_volatility_pct": rv,
            "regime": regime,
            "strategy_compatibility_checked_for": sid or "generic",
        },
        [],
    )


def _component_or_penalize(
    name: str,
    value: float | None,
    warnings: list[str],
    penalties: list[dict[str, Any]],
    rejection_reasons: list[str],
    weight: float,
) -> float:
    if value is not None:
        return _clamp(value)
    warnings.append(f"{name}: missing component data")
    penalty_points = round(weight * 0.5, 4)
    penalties.append({"component": name, "penalty_points": penalty_points, "reason": "missing_data"})
    rejection_reasons.append(f"missing_data:{name}")
    return 0.0


def calculate_quantum_score(
    symbol: str,
    prices: pd.Series | pd.DataFrame | None,
    benchmark_prices: pd.Series | pd.DataFrame | None,
    component_weights: dict[str, float] | None = None,
    strategy_id: str | None = None,
) -> dict[str, Any]:
    frame = _as_price_frame(prices)
    benchmark = _as_price_frame(benchmark_prices) if benchmark_prices is not None else frame
    weights = dict(component_weights or load_quantum_component_weights())

    warnings: list[str] = []
    rejection_reasons: list[str] = []
    penalties: list[dict[str, Any]] = []

    stale_limit = int(_safe_float(os.getenv("SCANNER_MAX_STALE_BUSINESS_DAYS", "5"), 5.0) or 5)
    stale_days = _business_days_stale(frame.index)
    if stale_days > stale_limit:
        rejection_reasons.append("stale_data")
        warnings.append(f"market data is stale by {stale_days} business days")

    trend_score, trend_values, trend_warnings = _trend_strength(frame)
    rs_score, rs_values, rs_warnings = _relative_strength(frame, benchmark)
    mom_score, mom_values, mom_warnings = _momentum_quality(frame)
    volconf_score, volconf_values, volconf_warnings = _volume_confirmation(frame)
    volq_score, volq_values, volq_warnings = _volatility_quality(frame)
    liq_score, liq_values, liq_warnings, liq_reject = _liquidity_quality(frame)
    rr_score, rr_values, rr_warnings, rr_reject = _risk_reward_quality(frame)
    regime_score, regime_values, regime_warnings = _market_regime_alignment(benchmark, strategy_id=strategy_id)

    warnings.extend(trend_warnings + rs_warnings + mom_warnings + volconf_warnings + volq_warnings + liq_warnings + rr_warnings + regime_warnings)
    rejection_reasons.extend(liq_reject)
    rejection_reasons.extend(rr_reject)

    normalized = {
        "trend_strength": _component_or_penalize("trend_strength", trend_score, warnings, penalties, rejection_reasons, weights["trend_strength"]),
        "relative_strength": _component_or_penalize("relative_strength", rs_score, warnings, penalties, rejection_reasons, weights["relative_strength"]),
        "momentum_quality": _component_or_penalize("momentum_quality", mom_score, warnings, penalties, rejection_reasons, weights["momentum_quality"]),
        "volume_confirmation": _component_or_penalize("volume_confirmation", volconf_score, warnings, penalties, rejection_reasons, weights["volume_confirmation"]),
        "volatility_quality": _component_or_penalize("volatility_quality", volq_score, warnings, penalties, rejection_reasons, weights["volatility_quality"]),
        "liquidity_quality": _component_or_penalize("liquidity_quality", liq_score, warnings, penalties, rejection_reasons, weights["liquidity_quality"]),
        "risk_reward_quality": _component_or_penalize("risk_reward_quality", rr_score, warnings, penalties, rejection_reasons, weights["risk_reward_quality"]),
        "market_regime_alignment": _component_or_penalize("market_regime_alignment", regime_score, warnings, penalties, rejection_reasons, weights["market_regime_alignment"]),
    }

    weighted_contributions = {
        name: round((score * weights[name]) / 100.0, 6)
        for name, score in normalized.items()
    }
    weighted_total = sum(weighted_contributions.values())
    penalty_total = sum(float(item.get("penalty_points") or 0.0) for item in penalties)
    final_score = _clamp(weighted_total - penalty_total)

    deduped_warnings = list(dict.fromkeys(warnings))
    deduped_rejections = list(dict.fromkeys(rejection_reasons))

    data_quality_status = "ok"
    if stale_days > stale_limit or any(reason.startswith("missing_data:") for reason in deduped_rejections):
        data_quality_status = "warn"
    if any(reason in {"minimum_price_check_failed", "average_dollar_volume_below_minimum", "invalid_reward_risk_structure", "reward_risk_below_minimum", "risk_reward_invalid"} for reason in deduped_rejections):
        data_quality_status = "fail"

    factor_values = {
        "trend_strength": trend_values,
        "relative_strength": rs_values,
        "momentum_quality": mom_values,
        "volume_confirmation": volconf_values,
        "volatility_quality": volq_values,
        "liquidity_quality": liq_values,
        "risk_reward_quality": rr_values,
        "market_regime_alignment": regime_values,
    }

    return {
        "symbol": str(symbol or "").upper(),
        "score_version": QUANTUM_SCORE_VERSION,
        "calculation_timestamp": _utc_iso(),
        "factor_values": factor_values,
        "normalized_component_scores": normalized,
        "component_weights": {key: float(value) for key, value in weights.items()},
        "weighted_contributions": weighted_contributions,
        "weighted_total": round(weighted_total, 6),
        "missing_data_penalties": penalties,
        "missing_data_penalty_total": round(penalty_total, 6),
        "final_score": round(final_score, 4),
        "data_quality_status": data_quality_status,
        "warnings": deduped_warnings,
        "rejection_reasons": deduped_rejections,
        "market_regime": str(regime_values.get("regime") or "unknown"),
        "risk_reward": {
            "entry": rr_values.get("entry"),
            "stop": rr_values.get("stop"),
            "target": rr_values.get("target"),
            "reward_risk_ratio": rr_values.get("reward_risk_ratio"),
            "position_size_feasible": rr_values.get("position_size_feasible"),
            "max_loss_percent": rr_values.get("max_loss_percent"),
        },
        "stale_business_days": stale_days,
    }


def compute_strategy_specific_scores(quantum_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    component_scores = dict(quantum_payload.get("normalized_component_scores") or {})
    regime = str(quantum_payload.get("market_regime") or "unknown")
    rr_ratio = _safe_float(((quantum_payload.get("risk_reward") or {}).get("reward_risk_ratio")), 0.0) or 0.0
    data_quality_status = str(quantum_payload.get("data_quality_status") or "warn")
    factor_values = dict(quantum_payload.get("factor_values") or {})
    momentum_values = dict(factor_values.get("momentum_quality") or {})
    rsi14 = _safe_float(momentum_values.get("rsi14"), None)

    def _component(name: str) -> float:
        return float(component_scores.get(name) or 0.0)

    def _build(strategy_id: str, raw: float, required_checks: list[tuple[bool, str]], *, direction: str, confirmations: dict[str, bool]) -> dict[str, Any]:
        warnings: list[str] = []
        rejections = [reason for passed, reason in required_checks if not passed]
        if data_quality_status == "fail":
            rejections.append("data_quality_failed")
        if data_quality_status == "warn":
            warnings.append("data quality warnings present")
        eligible = len(rejections) == 0
        return {
            "strategy_id": strategy_id,
            "strategy_version": "2.0.0",
            "strategy_score": round(_clamp(raw), 4),
            "required_factors": sorted(confirmations.keys()),
            "rejection_reasons": sorted(set(rejections)),
            "warnings": list(dict.fromkeys(warnings)),
            "eligible": eligible,
            "confidence": round(_clamp((raw * 0.85) + (5.0 if eligible else 0.0)), 4),
            "market_regime": regime,
            "direction": direction,
            "confirmation_count": sum(1 for passed in confirmations.values() if passed),
            "confirmations": dict(confirmations),
        }

    trend_confirmations = {
        "trend_strength": _component("trend_strength") >= 60.0,
        "relative_strength": _component("relative_strength") >= 52.0,
        "momentum_quality": _component("momentum_quality") >= 55.0,
        "volume_breakout_confirmation": _component("volume_confirmation") >= 55.0,
    }
    trend_raw = (
        (_component("trend_strength") * 0.30)
        + (_component("relative_strength") * 0.22)
        + (_component("momentum_quality") * 0.20)
        + (_component("volume_confirmation") * 0.10)
        + (_component("market_regime_alignment") * 0.10)
        + (_component("risk_reward_quality") * 0.08)
    )

    oversold_score = _clamp(((40.0 - float(rsi14)) / 15.0) * 100.0) if rsi14 is not None else 0.0
    mean_reversion_confirmations = {
        "rsi_oversold": bool(rsi14 is not None and 25.0 <= float(rsi14) <= 38.0),
        "range_regime": regime in {"sideways", "weak_bull"},
        "liquidity_quality": _component("liquidity_quality") >= 55.0,
        "risk_reward_quality": rr_ratio >= 1.2,
    }
    mean_reversion_raw = (
        (oversold_score * 0.35)
        + (_component("risk_reward_quality") * 0.20)
        + (_component("liquidity_quality") * 0.20)
        + (_component("volatility_quality") * 0.15)
        + (_component("market_regime_alignment") * 0.10)
    )

    trend_weakness = 100.0 - _component("trend_strength")
    momentum_weakness = 100.0 - _component("momentum_quality")
    relative_weakness = 100.0 - _component("relative_strength")
    bearish_confirmations = {
        "trend_weakness": trend_weakness >= 55.0,
        "momentum_weakness": momentum_weakness >= 55.0,
        "relative_weakness": relative_weakness >= 50.0,
        "liquidity_quality": _component("liquidity_quality") >= 55.0,
    }
    bearish_raw = (
        (trend_weakness * 0.35)
        + (momentum_weakness * 0.25)
        + (relative_weakness * 0.20)
        + (_component("liquidity_quality") * 0.12)
        + (_component("volatility_quality") * 0.08)
    )

    return {
        "stock_trend_ensemble_v2": _build(
            "stock_trend_ensemble_v2",
            trend_raw,
            [
                (
                    regime == "bull"
                    or (regime == "weak_bull" and not mean_reversion_confirmations["rsi_oversold"]),
                    "regime_incompatible",
                ),
                (sum(trend_confirmations.values()) >= 3, "insufficient_factor_confirmation"),
                (_component("liquidity_quality") >= 55.0, "liquidity_too_low"),
                (_component("volatility_quality") >= 40.0, "volatility_quality_too_low"),
                (rr_ratio >= 1.2, "reward_risk_below_minimum"),
            ],
            direction="LONG",
            confirmations=trend_confirmations,
        ),
        "stock_mean_reversion_v2": _build(
            "stock_mean_reversion_v2",
            mean_reversion_raw,
            [
                (mean_reversion_confirmations["range_regime"], "regime_incompatible"),
                (mean_reversion_confirmations["rsi_oversold"], "rsi_not_oversold"),
                (mean_reversion_confirmations["liquidity_quality"], "liquidity_too_low"),
                (mean_reversion_confirmations["risk_reward_quality"], "reward_risk_below_minimum"),
                (_component("trend_strength") >= 35.0, "falling_knife_risk"),
                (_component("volatility_quality") >= 40.0, "volatility_quality_too_low"),
            ],
            direction="LONG",
            confirmations=mean_reversion_confirmations,
        ),
        "stock_bearish_trend_v2": _build(
            "stock_bearish_trend_v2",
            bearish_raw,
            [
                (regime == "bear", "regime_incompatible"),
                (sum(bearish_confirmations.values()) >= 3, "insufficient_factor_confirmation"),
                (bearish_confirmations["liquidity_quality"], "liquidity_too_low"),
                (_component("volatility_quality") >= 40.0, "volatility_quality_too_low"),
            ],
            direction="SHORT",
            confirmations=bearish_confirmations,
        ),
    }


def rank_scored_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for row in candidates:
        item = dict(row)
        strategy_scores = dict(item.get("strategy_specific_scores") or {})
        eligible_strategy_scores = [
            float(payload.get("strategy_score") or 0.0)
            for payload in strategy_scores.values()
            if bool(payload.get("eligible"))
        ]
        best_strategy_score = max(eligible_strategy_scores) if eligible_strategy_scores else 0.0
        has_eligible_strategy = 1 if eligible_strategy_scores else 0

        data_quality_status = str((item.get("quantum_score") or {}).get("data_quality_status") or "warn")
        dq_rank = {"ok": 2, "warn": 1, "fail": 0}.get(data_quality_status, 0)

        item["_rank_key"] = (
            -has_eligible_strategy,
            -dq_rank,
            -float(item.get("overall_score") or 0.0),
            -best_strategy_score,
            -float((item.get("quantum_score") or {}).get("risk_reward", {}).get("reward_risk_ratio") or 0.0),
            -float((item.get("component_scores") or {}).get("liquidity_quality") or 0.0),
            str(item.get("symbol") or ""),
        )
        ranked.append(item)

    ranked.sort(key=lambda item: item["_rank_key"])
    for idx, item in enumerate(ranked, start=1):
        item["rank"] = idx
        item.pop("_rank_key", None)
    return ranked
