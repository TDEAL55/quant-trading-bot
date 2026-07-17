import pandas as pd

from config import BENCHMARK_SYMBOL, FACTOR_WEIGHTS, SIGNAL_HYSTERESIS_BUFFER, SIGNAL_THRESHOLDS, STRATEGY_MODE, validate_factor_weights, validate_signal_thresholds
from error_handler import CalculationError
from factor_engine import score_symbol


def calculate_moving_average(series, window):
    """Return a rolling mean for the given price series."""
    return series.rolling(window=window).mean()


def legacy_ma_strategy(prices, short_window=20, long_window=50):
    """Generate a simple buy/sell/hold signal using moving average crossover."""
    if not isinstance(prices, pd.Series):
        if isinstance(prices, pd.DataFrame) and "close" in prices.columns:
            prices = prices["close"]
        else:
            prices = pd.Series(prices)

    if len(prices) < max(short_window, long_window) + 1:
        raise CalculationError("Not enough data points for moving-average crossover")

    short_ma = calculate_moving_average(prices, short_window)
    long_ma = calculate_moving_average(prices, long_window)

    if short_ma.iloc[-1] > long_ma.iloc[-1] and short_ma.iloc[-2] <= long_ma.iloc[-2]:
        return "buy"
    if short_ma.iloc[-1] < long_ma.iloc[-1] and short_ma.iloc[-2] >= long_ma.iloc[-2]:
        return "sell"
    return "hold"


def generate_strategy_result(
    prices,
    short_window=20,
    long_window=50,
    strategy_mode=None,
    symbol="SPY",
    benchmark_prices=None,
    previous_signal=None,
):
    selected_mode = str(strategy_mode or STRATEGY_MODE or "LEGACY_MA").upper()
    if selected_mode == "LEGACY_MA":
        signal = legacy_ma_strategy(prices, short_window, long_window)
        series = prices["close"] if isinstance(prices, pd.DataFrame) and "close" in prices.columns else pd.Series(prices)
        short_ma = calculate_moving_average(series, short_window)
        long_ma = calculate_moving_average(series, long_window)
        return {
            "symbol": symbol,
            "timestamp": str(series.index[-1]) if hasattr(series, "index") and len(series.index) else None,
            "overall_score": 100.0 if signal == "buy" else 0.0 if signal == "sell" else 50.0,
            "confidence": 60.0,
            "signal": "BUY" if signal == "buy" else "EXIT" if signal == "sell" else "HOLD",
            "legacy_signal": signal,
            "regime": "unknown",
            "component_scores": {"trend": 50.0},
            "reasons": ["Legacy moving-average crossover strategy"],
            "warnings": [],
            "data_quality": {"volume_available": False, "history_sufficient": len(series) >= max(short_window, long_window) + 1},
            "factors": {
                "trend": {
                    "score": 50.0,
                    "status": "legacy",
                    "positive_reasons": ["Short and long moving averages are available"],
                    "negative_reasons": [],
                    "warnings": [],
                    "raw_values": {
                        "short_moving_average": float(short_ma.iloc[-1]),
                        "long_moving_average": float(long_ma.iloc[-1]),
                    },
                    "available": True,
                }
            },
            "summary_text": "Signal: HOLD\nScore: 50.0\nConfidence: 60.0%",
        }
    if selected_mode != "MULTI_FACTOR":
        raise CalculationError(f"Unsupported strategy mode: {selected_mode}")
    weights = validate_factor_weights(FACTOR_WEIGHTS)
    thresholds = validate_signal_thresholds(SIGNAL_THRESHOLDS)
    return score_symbol(
        prices=prices,
        benchmark_prices=benchmark_prices,
        symbol=symbol or BENCHMARK_SYMBOL,
        weights=weights,
        thresholds=thresholds,
        previous_signal=previous_signal,
        hysteresis_buffer=SIGNAL_HYSTERESIS_BUFFER,
    )


def generate_signal(prices, short_window=20, long_window=50):
    """Compatibility entry point returning a legacy buy/sell/hold string."""
    if isinstance(prices, pd.DataFrame) and "close" in prices.columns:
        series = prices["close"]
    else:
        series = pd.Series(prices)
    if len(series) < max(short_window, long_window) + 1:
        raise CalculationError("Not enough data points for moving-average crossover")
    result = generate_strategy_result(prices, short_window=short_window, long_window=long_window)
    return result.get("legacy_signal", "hold")
