from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd

try:
    from alpaca.data.enums import DataFeed, OptionsFeed
    from alpaca.data.historical.option import OptionHistoricalDataClient
    from alpaca.data.historical.stock import StockHistoricalDataClient
    from alpaca.data.requests import OptionSnapshotRequest, StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
except Exception:  # pragma: no cover - handled at runtime
    DataFeed = None
    OptionsFeed = None
    OptionHistoricalDataClient = None
    StockHistoricalDataClient = None
    OptionSnapshotRequest = None
    StockBarsRequest = None
    TimeFrame = None
    TimeFrameUnit = None


OPTION_SYMBOL_PATTERN = re.compile(r"^([A-Z0-9.]+)(\d{6})([CP])(\d{8})$")


class OptionsMarketDataError(RuntimeError):
    pass


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _enum_text(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip().lower()


def _clip(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, float(value)))


def _rsi(close: pd.Series, period: int = 14) -> float:
    delta = close.diff()
    gains = delta.clip(lower=0.0).rolling(period).mean()
    losses = (-delta.clip(upper=0.0)).rolling(period).mean()
    average_gain = _float(gains.iloc[-1], 0.0)
    average_loss = _float(losses.iloc[-1], 0.0)
    if average_loss <= 0:
        return 100.0 if average_gain > 0 else 50.0
    return 100.0 - (100.0 / (1.0 + (average_gain / average_loss)))


def parse_option_symbol(symbol: str) -> dict[str, Any]:
    normalized = str(symbol or "").strip().upper()
    match = OPTION_SYMBOL_PATTERN.fullmatch(normalized)
    if not match:
        return {}
    underlying, expiration_text, type_code, strike_text = match.groups()
    try:
        expiration = datetime.strptime(expiration_text, "%y%m%d").date()
    except ValueError:
        return {}
    return {
        "symbol": normalized,
        "underlying_symbol": underlying,
        "expiration_date": expiration.isoformat(),
        "contract_type": "call" if type_code == "C" else "put",
        "strike_price": int(strike_text) / 1000.0,
    }


def analyze_underlying_bars(
    symbol: str,
    bars: pd.DataFrame,
    *,
    call_score: float = 60.0,
    put_score: float = 40.0,
    now: datetime | None = None,
    maximum_age_minutes: int = 45,
) -> dict[str, Any]:
    frame = bars.copy() if isinstance(bars, pd.DataFrame) else pd.DataFrame(bars or {})
    normalized = str(symbol or "").strip().upper()
    if "close" not in frame.columns or len(frame) < 60:
        return {"symbol": normalized, "signal": "HOLD", "eligible": False, "score": 50.0, "reason": "insufficient_underlying_history", "bar_count": len(frame)}
    frame = frame.sort_index()
    close = pd.to_numeric(frame["close"], errors="coerce").dropna()
    if len(close) < 60:
        return {"symbol": normalized, "signal": "HOLD", "eligible": False, "score": 50.0, "reason": "insufficient_valid_underlying_history", "bar_count": len(close)}

    latest_price = _float(close.iloc[-1], 0.0)
    ema_fast = _float(close.ewm(span=20, adjust=False).mean().iloc[-1], latest_price)
    ema_slow = _float(close.ewm(span=50, adjust=False).mean().iloc[-1], latest_price)
    return_90m = _float(close.pct_change(6).iloc[-1], 0.0)
    return_6h = _float(close.pct_change(24).iloc[-1], 0.0)
    rsi = _rsi(close)
    trend = ((ema_fast / ema_slow) - 1.0) if ema_slow > 0 else 0.0
    trend_score = _clip(50.0 + (trend * 1000.0))
    momentum_score = _clip(50.0 + (return_90m * 800.0) + (return_6h * 300.0))
    rsi_direction = _clip(50.0 + ((rsi - 50.0) * 1.2))
    score = _clip((trend_score * 0.45) + (momentum_score * 0.4) + (rsi_direction * 0.15))

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    latest_timestamp = pd.Timestamp(frame.index[-1])
    if latest_timestamp.tzinfo is None:
        latest_timestamp = latest_timestamp.tz_localize("UTC")
    age_minutes = max((current - latest_timestamp.to_pydatetime()).total_seconds() / 60.0, 0.0)
    stale = age_minutes > max(float(maximum_age_minutes), 1.0)

    if not stale and score >= float(call_score) and ema_fast > ema_slow and return_90m > 0:
        signal = "CALL"
        reason = "bullish_underlying_trend"
    elif not stale and score <= float(put_score) and ema_fast < ema_slow and return_90m < 0:
        signal = "PUT"
        reason = "bearish_underlying_trend"
    else:
        signal = "HOLD"
        reason = "underlying_signal_inside_hold_band"
    if stale:
        reason = "stale_underlying_market_data"

    return {
        "symbol": normalized,
        "signal": signal,
        "eligible": bool(not stale and latest_price > 0),
        "score": round(score, 4),
        "confidence": round(abs(score - 50.0) * 2.0, 4),
        "reason": reason,
        "latest_price": round(latest_price, 6),
        "ema_fast": round(ema_fast, 6),
        "ema_slow": round(ema_slow, 6),
        "return_90m": round(return_90m * 100.0, 6),
        "return_6h": round(return_6h * 100.0, 6),
        "rsi_14": round(rsi, 4),
        "latest_bar_timestamp": latest_timestamp.isoformat(),
        "data_age_minutes": round(age_minutes, 3),
        "bar_count": int(len(close)),
    }


def normalize_option_snapshot(symbol: str, snapshot: Any) -> dict[str, Any]:
    if isinstance(snapshot, dict):
        quote = snapshot.get("latest_quote") or snapshot.get("latestQuote") or {}
        greeks = snapshot.get("greeks") or {}
        iv = snapshot.get("implied_volatility", snapshot.get("impliedVolatility"))
    else:
        quote = getattr(snapshot, "latest_quote", None) or {}
        greeks = getattr(snapshot, "greeks", None) or {}
        iv = getattr(snapshot, "implied_volatility", 0.0)
    getter = lambda source, key, fallback="": source.get(key, fallback) if isinstance(source, dict) else getattr(source, key, fallback)
    bid = _float(getter(quote, "bid_price", getter(quote, "bp", 0.0)), 0.0)
    ask = _float(getter(quote, "ask_price", getter(quote, "ap", 0.0)), 0.0)
    mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else max(bid, ask)
    spread_percent = ((ask - bid) / mid) * 100.0 if mid > 0 and ask >= bid else 999.0
    parsed = parse_option_symbol(symbol)
    return {
        **parsed,
        "symbol": str(symbol or parsed.get("symbol") or "").upper(),
        "bid": round(bid, 4),
        "ask": round(ask, 4),
        "mid": round(mid, 4),
        "spread_percent": round(spread_percent, 4),
        "implied_volatility": round(_float(iv, 0.0), 6),
        "delta": round(_float(getter(greeks, "delta", 0.0), 0.0), 6),
        "gamma": round(_float(getter(greeks, "gamma", 0.0), 0.0), 6),
        "theta": round(_float(getter(greeks, "theta", 0.0), 0.0), 6),
        "vega": round(_float(getter(greeks, "vega", 0.0), 0.0), 6),
        "rho": round(_float(getter(greeks, "rho", 0.0), 0.0), 6),
    }


def select_option_contract(
    contracts: list[dict[str, Any]],
    snapshots: dict[str, dict[str, Any]],
    *,
    underlying_price: float,
    target_delta: float = 0.55,
    maximum_spread_percent: float = 35.0,
    minimum_open_interest: int = 0,
    today: date | None = None,
) -> dict[str, Any]:
    current_date = today or datetime.now(timezone.utc).date()
    candidates: list[dict[str, Any]] = []
    for raw in contracts:
        contract = dict(raw or {})
        symbol = str(contract.get("symbol") or "").upper()
        snapshot = dict(snapshots.get(symbol) or {})
        if not snapshot:
            continue
        bid = _float(snapshot.get("bid"), 0.0)
        ask = _float(snapshot.get("ask"), 0.0)
        spread = _float(snapshot.get("spread_percent"), 999.0)
        if bid <= 0 or ask <= 0 or ask < bid or spread > float(maximum_spread_percent):
            continue
        if int(contract.get("open_interest") or 0) < max(int(minimum_open_interest), 0):
            continue
        try:
            expiration = date.fromisoformat(str(contract.get("expiration_date") or ""))
        except ValueError:
            continue
        days_to_expiration = (expiration - current_date).days
        strike = _float(contract.get("strike_price"), 0.0)
        if strike <= 0 or underlying_price <= 0:
            continue
        delta = abs(_float(snapshot.get("delta"), 0.0))
        delta_penalty = abs(delta - float(target_delta)) if delta > 0 else 0.5
        moneyness_penalty = abs((strike / underlying_price) - 1.0)
        dte_penalty = abs(days_to_expiration - 28) / 100.0
        liquidity_penalty = spread / 100.0
        selection_score = delta_penalty + moneyness_penalty + dte_penalty + liquidity_penalty
        candidates.append(
            {
                **contract,
                **snapshot,
                "days_to_expiration": days_to_expiration,
                "selection_score": round(selection_score, 6),
                "contract_multiplier": int(contract.get("size") or 100),
            }
        )
    return min(candidates, key=lambda row: (_float(row.get("selection_score"), 999.0), str(row.get("symbol") or ""))) if candidates else {}


class AlpacaOptionsMarketData:
    def __init__(self, stock_client: Any | None = None, option_client: Any | None = None):
        if stock_client is None and StockHistoricalDataClient is None:
            raise OptionsMarketDataError("alpaca-py stock market data support is required")
        if option_client is None and OptionHistoricalDataClient is None:
            raise OptionsMarketDataError("alpaca-py options market data support is required")
        api_key = str(os.getenv("ALPACA_API_KEY", "")).strip()
        api_secret = str(os.getenv("ALPACA_API_SECRET", "")).strip()
        self.stock_client = stock_client or StockHistoricalDataClient(api_key=api_key, secret_key=api_secret)
        self.option_client = option_client or OptionHistoricalDataClient(api_key=api_key, secret_key=api_secret)

    def fetch_underlying_bars(
        self,
        symbols: list[str],
        *,
        now: datetime | None = None,
        timeframe_minutes: int = 15,
        lookback_bars: int = 240,
    ) -> dict[str, pd.DataFrame]:
        normalized = list(dict.fromkeys(str(symbol or "").strip().upper() for symbol in symbols if str(symbol or "").strip()))
        if not normalized:
            return {}
        if StockBarsRequest is None or TimeFrame is None or TimeFrameUnit is None:
            raise OptionsMarketDataError("alpaca-py stock bar request models are unavailable")
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        interval = max(1, min(int(timeframe_minutes), 59))
        bars_needed = max(int(lookback_bars), 60)
        start = current - timedelta(days=max(10, int((bars_needed * interval) / 390 * 2.5)))
        request = StockBarsRequest(
            symbol_or_symbols=normalized,
            timeframe=TimeFrame(interval, TimeFrameUnit.Minute),
            start=start,
            end=current,
            limit=min((bars_needed + 20) * len(normalized), 10000),
            feed=DataFeed.IEX if DataFeed is not None else None,
        )
        response = self.stock_client.get_stock_bars(request)
        frame = getattr(response, "df", response)
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            raise OptionsMarketDataError("Alpaca returned no underlying stock bars")
        result: dict[str, pd.DataFrame] = {}
        if isinstance(frame.index, pd.MultiIndex):
            symbol_level = frame.index.names.index("symbol") if "symbol" in frame.index.names else 0
            for symbol in normalized:
                try:
                    result[symbol] = frame.xs(symbol, level=symbol_level).sort_index().tail(bars_needed)
                except KeyError:
                    continue
        elif len(normalized) == 1:
            result[normalized[0]] = frame.sort_index().tail(bars_needed)
        return result

    def fetch_option_snapshots(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        normalized = list(dict.fromkeys(str(symbol or "").strip().upper() for symbol in symbols if str(symbol or "").strip()))
        if not normalized:
            return {}
        if OptionSnapshotRequest is None:
            raise OptionsMarketDataError("alpaca-py option snapshot requests are unavailable")
        feed_text = str(os.getenv("OPTIONS_DATA_FEED", "indicative")).strip().lower()
        feed = None
        if OptionsFeed is not None:
            feed = OptionsFeed.OPRA if feed_text == "opra" else OptionsFeed.INDICATIVE
        result: dict[str, dict[str, Any]] = {}
        for offset in range(0, len(normalized), 100):
            batch = normalized[offset : offset + 100]
            request = OptionSnapshotRequest(symbol_or_symbols=batch, feed=feed)
            response = self.option_client.get_option_snapshot(request)
            raw_rows = dict(response or {})
            for symbol, snapshot in raw_rows.items():
                result[str(symbol).upper()] = normalize_option_snapshot(str(symbol), snapshot)
        return result
