from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

try:
    from alpaca.data.enums import CryptoFeed
    from alpaca.data.historical import CryptoHistoricalDataClient
    from alpaca.data.requests import CryptoBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
except Exception:  # pragma: no cover - handled explicitly at runtime
    CryptoFeed = None
    CryptoHistoricalDataClient = None
    CryptoBarsRequest = None
    TimeFrame = None
    TimeFrameUnit = None

from crypto_universe import canonical_crypto_symbol


class CryptoMarketDataError(RuntimeError):
    pass


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clip(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, float(value)))


def _rsi(close: pd.Series, period: int = 14) -> float:
    delta = close.diff()
    gains = delta.clip(lower=0.0).rolling(period).mean()
    losses = (-delta.clip(upper=0.0)).rolling(period).mean()
    average_gain = _safe_float(gains.iloc[-1], 0.0)
    average_loss = _safe_float(losses.iloc[-1], 0.0)
    if average_loss <= 0:
        return 100.0 if average_gain > 0 else 50.0
    return 100.0 - (100.0 / (1.0 + (average_gain / average_loss)))


def analyze_crypto_bars(
    symbol: str,
    bars: pd.DataFrame,
    *,
    buy_score: float = 60.0,
    exit_score: float = 40.0,
    now: datetime | None = None,
    maximum_age_minutes: int = 45,
) -> dict[str, Any]:
    frame = pd.DataFrame(bars or {}).copy() if not isinstance(bars, pd.DataFrame) else bars.copy()
    if "close" not in frame.columns or len(frame) < 60:
        return {
            "symbol": canonical_crypto_symbol(symbol),
            "signal": "HOLD",
            "eligible": False,
            "score": 0.0,
            "reason": "insufficient_crypto_history",
            "bar_count": int(len(frame)),
        }
    frame = frame.sort_index()
    close = pd.to_numeric(frame["close"], errors="coerce").dropna()
    if len(close) < 60:
        return {
            "symbol": canonical_crypto_symbol(symbol),
            "signal": "HOLD",
            "eligible": False,
            "score": 0.0,
            "reason": "insufficient_valid_close_history",
            "bar_count": int(len(close)),
        }

    latest_price = _safe_float(close.iloc[-1], 0.0)
    ema_fast = _safe_float(close.ewm(span=20, adjust=False).mean().iloc[-1], latest_price)
    ema_slow = _safe_float(close.ewm(span=50, adjust=False).mean().iloc[-1], latest_price)
    ret_6 = _safe_float(close.pct_change(6).iloc[-1], 0.0)
    ret_24 = _safe_float(close.pct_change(24).iloc[-1], 0.0)
    rsi = _rsi(close)
    returns = close.pct_change().dropna().tail(96)
    realized_volatility = _safe_float(returns.std(), 0.0) * (96.0 * 365.0) ** 0.5 * 100.0

    volume_ratio = 1.0
    if "volume" in frame.columns:
        volume = pd.to_numeric(frame["volume"], errors="coerce").fillna(0.0)
        baseline = _safe_float(volume.tail(48).mean(), 0.0)
        volume_ratio = _safe_float(volume.iloc[-1], 0.0) / baseline if baseline > 0 else 1.0

    trend_distance = ((ema_fast / ema_slow) - 1.0) if ema_slow > 0 else 0.0
    trend_score = _clip(50.0 + (trend_distance * 900.0))
    momentum_score = _clip(50.0 + (ret_6 * 700.0) + (ret_24 * 300.0))
    if 45.0 <= rsi <= 68.0:
        rsi_quality = 85.0
    elif 35.0 <= rsi < 45.0:
        rsi_quality = 60.0
    elif 68.0 < rsi <= 78.0:
        rsi_quality = 55.0
    else:
        rsi_quality = 25.0
    volume_score = _clip(50.0 + ((volume_ratio - 1.0) * 30.0))
    score = _clip(
        (trend_score * 0.35)
        + (momentum_score * 0.35)
        + (rsi_quality * 0.15)
        + (volume_score * 0.15)
    )

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    latest_timestamp = pd.Timestamp(frame.index[-1])
    if latest_timestamp.tzinfo is None:
        latest_timestamp = latest_timestamp.tz_localize("UTC")
    age_minutes = max((current_time - latest_timestamp.to_pydatetime()).total_seconds() / 60.0, 0.0)
    stale = age_minutes > float(maximum_age_minutes)

    bullish = bool(ema_fast > ema_slow and ret_6 > 0)
    bearish = bool(ema_fast < ema_slow and ret_6 < 0)
    if not stale and score >= float(buy_score) and bullish:
        signal = "BUY"
        reason = "crypto_trend_momentum_entry"
    elif score <= float(exit_score) or bearish:
        signal = "SELL"
        reason = "crypto_trend_momentum_exit"
    else:
        signal = "HOLD"
        reason = "crypto_signal_inside_hold_band"

    return {
        "symbol": canonical_crypto_symbol(symbol),
        "signal": signal,
        "eligible": bool(not stale and latest_price > 0),
        "score": round(score, 4),
        "confidence": round(abs(score - 50.0) * 2.0, 4),
        "reason": "stale_crypto_market_data" if stale else reason,
        "latest_price": round(latest_price, 10),
        "ema_fast": round(ema_fast, 10),
        "ema_slow": round(ema_slow, 10),
        "return_90m": round(ret_6 * 100.0, 6),
        "return_6h": round(ret_24 * 100.0, 6),
        "rsi_14": round(rsi, 4),
        "volume_ratio": round(volume_ratio, 6),
        "annualized_volatility_percent": round(realized_volatility, 4),
        "latest_bar_timestamp": latest_timestamp.isoformat(),
        "data_age_minutes": round(age_minutes, 3),
        "bar_count": int(len(close)),
    }


class AlpacaCryptoMarketData:
    def __init__(self, client: Any | None = None):
        if client is None and CryptoHistoricalDataClient is None:
            raise CryptoMarketDataError("alpaca-py crypto market data support is required")
        self.client = client or CryptoHistoricalDataClient(
            api_key=str(os.getenv("ALPACA_API_KEY", "")).strip() or None,
            secret_key=str(os.getenv("ALPACA_API_SECRET", "")).strip() or None,
        )

    def fetch_bars(
        self,
        symbols: list[str],
        *,
        now: datetime | None = None,
        timeframe_minutes: int = 15,
        lookback_bars: int = 240,
    ) -> dict[str, pd.DataFrame]:
        normalized = [canonical_crypto_symbol(symbol) for symbol in symbols if canonical_crypto_symbol(symbol)]
        if not normalized:
            return {}
        if CryptoBarsRequest is None or TimeFrame is None or TimeFrameUnit is None:
            raise CryptoMarketDataError("alpaca-py crypto request models are unavailable")
        interval = max(1, min(int(timeframe_minutes), 59))
        end = now or datetime.now(timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        feed = CryptoFeed.US if CryptoFeed is not None else None
        result: dict[str, pd.DataFrame] = {}
        bars_needed = max(int(lookback_bars), 60)
        request_bars = bars_needed + 5
        batch_size = max(1, 9000 // request_bars)
        start = end - timedelta(minutes=interval * request_bars)
        for offset in range(0, len(normalized), batch_size):
            batch = normalized[offset : offset + batch_size]
            request = CryptoBarsRequest(
                symbol_or_symbols=batch,
                timeframe=TimeFrame(interval, TimeFrameUnit.Minute),
                start=start,
                end=end,
                limit=min(request_bars * len(batch), 10000),
            )
            response = self.client.get_crypto_bars(request, feed=feed) if feed is not None else self.client.get_crypto_bars(request)
            frame = getattr(response, "df", response)
            if not isinstance(frame, pd.DataFrame) or frame.empty:
                continue
            if isinstance(frame.index, pd.MultiIndex):
                symbol_level = frame.index.names.index("symbol") if "symbol" in frame.index.names else 0
                for symbol in batch:
                    try:
                        rows = frame.xs(symbol, level=symbol_level).copy()
                    except KeyError:
                        continue
                    result[symbol] = rows.sort_index().tail(bars_needed)
            elif len(batch) == 1:
                result[batch[0]] = frame.sort_index().tail(bars_needed)
        if not result:
            raise CryptoMarketDataError("Alpaca returned no crypto bars")
        return result
