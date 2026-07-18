from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from config import (
    SCANNER_MAX_MISSING_PERCENT,
    SCANNER_MAX_STALE_BUSINESS_DAYS,
    SCANNER_MIN_AVG_DOLLAR_VOLUME,
    SCANNER_MIN_HISTORY_DAYS,
    SCANNER_MIN_PRICE,
)
from stock_universe import is_supported_symbol_format, normalize_symbol


def _to_price_frame(frame: pd.DataFrame | pd.Series) -> pd.DataFrame:
    if isinstance(frame, pd.Series):
        data = pd.DataFrame({"close": frame})
    else:
        data = frame.copy()
    data.columns = [str(column).lower() for column in data.columns]
    if "close" not in data.columns and len(data.columns) == 1:
        data.columns = ["close"]
    return data.sort_index()


def _missing_percent(data: pd.DataFrame) -> float:
    if data.empty:
        return 100.0
    total_cells = max(data.shape[0] * max(data.shape[1], 1), 1)
    return float((data.isna().sum().sum() / total_cells) * 100.0)


def _business_days_stale(index: pd.Index, now: datetime | None = None) -> int:
    if len(index) == 0:
        return 9_999
    timestamp = pd.Timestamp(index[-1])
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(timezone.utc)
    current = now or datetime.now(timezone.utc)
    start = timestamp.date()
    end = current.date()
    if end < start:
        return 0
    business_days = pd.bdate_range(start=start, end=end)
    return max(len(business_days) - 1, 0)


def _has_impossible_ohlc(data: pd.DataFrame) -> bool:
    required = {"open", "high", "low", "close"}
    if not required.issubset(set(data.columns)):
        return False
    checks = (
        (data["high"] < data["low"]).any(),
        (data["high"] < data["open"]).any(),
        (data["high"] < data["close"]).any(),
        (data["low"] > data["open"]).any(),
        (data["low"] > data["close"]).any(),
    )
    return bool(any(checks))


def validate_symbol_data(
    symbol: str,
    history: pd.DataFrame | pd.Series,
    min_price: float | None = None,
    min_avg_dollar_volume: float | None = None,
    min_history_days: int | None = None,
    max_missing_percent: float | None = None,
    max_stale_business_days: int | None = None,
) -> dict[str, Any]:
    normalized_symbol = normalize_symbol(symbol)
    data = _to_price_frame(history)
    reasons: list[str] = []
    warnings: list[str] = []

    min_price = float(min_price if min_price is not None else SCANNER_MIN_PRICE)
    min_avg_dollar_volume = float(
        min_avg_dollar_volume if min_avg_dollar_volume is not None else SCANNER_MIN_AVG_DOLLAR_VOLUME
    )
    min_history_days = int(min_history_days if min_history_days is not None else SCANNER_MIN_HISTORY_DAYS)
    max_missing_percent = float(max_missing_percent if max_missing_percent is not None else SCANNER_MAX_MISSING_PERCENT)
    max_stale_business_days = int(
        max_stale_business_days if max_stale_business_days is not None else SCANNER_MAX_STALE_BUSINESS_DAYS
    )

    if not is_supported_symbol_format(normalized_symbol):
        reasons.append("unsupported symbol formatting")

    if data.empty:
        reasons.append("no market data returned")
        return {
            "passed": False,
            "reasons": reasons,
            "warnings": warnings,
            "metrics": {
                "latest_price": 0.0,
                "average_volume_20": 0.0,
                "average_dollar_volume_20": 0.0,
                "history_rows": 0,
                "missing_percent": 100.0,
            },
        }

    required_columns = {"open", "high", "low", "close", "volume"}
    if not required_columns.issubset(set(data.columns)):
        reasons.append("invalid OHLC data")

    latest_price = float(pd.to_numeric(data.get("close"), errors="coerce").iloc[-1]) if "close" in data.columns else 0.0
    latest_volume = float(pd.to_numeric(data.get("volume"), errors="coerce").iloc[-1]) if "volume" in data.columns else 0.0

    if latest_price <= 0:
        reasons.append("zero or negative latest price")
    if latest_price < min_price:
        reasons.append(f"price below minimum (${min_price:.2f})")

    close_series = pd.to_numeric(data.get("close", pd.Series(dtype=float)), errors="coerce")
    volume_series = pd.to_numeric(data.get("volume", pd.Series(dtype=float)), errors="coerce")
    average_volume_20 = float(volume_series.tail(20).mean()) if len(volume_series) else 0.0
    average_dollar_volume_20 = float((close_series.tail(20) * volume_series.tail(20)).mean()) if len(close_series) else 0.0
    if average_dollar_volume_20 < min_avg_dollar_volume:
        reasons.append(f"average dollar volume below minimum ({min_avg_dollar_volume:,.0f})")

    history_rows = int(len(data))
    if history_rows < min_history_days:
        reasons.append(f"insufficient history ({history_rows} < {min_history_days})")

    missing_pct = _missing_percent(data)
    if missing_pct > max_missing_percent:
        reasons.append(f"missing data exceeds threshold ({missing_pct:.2f}% > {max_missing_percent:.2f}%)")

    stale_days = _business_days_stale(data.index)
    if stale_days > max_stale_business_days:
        reasons.append(f"stale final quote ({stale_days} business days old)")

    if _has_impossible_ohlc(data):
        reasons.append("impossible OHLC relationships detected")

    if latest_volume <= 0:
        warnings.append("latest volume is zero or missing")

    if history_rows >= 10:
        recent_window = data.tail(10)
        if recent_window.isna().any().any():
            reasons.append("severe recent data gaps")

    return {
        "passed": not reasons,
        "reasons": reasons,
        "warnings": warnings,
        "metrics": {
            "latest_price": round(latest_price, 4),
            "average_volume_20": round(average_volume_20, 4),
            "average_dollar_volume_20": round(average_dollar_volume_20, 4),
            "history_rows": history_rows,
            "missing_percent": round(missing_pct, 4),
            "stale_business_days": stale_days,
        },
    }
