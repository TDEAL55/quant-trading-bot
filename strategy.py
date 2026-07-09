import pandas as pd

from error_handler import CalculationError


def calculate_moving_average(series, window):
    """Return a rolling mean for the given price series."""
    return series.rolling(window=window).mean()


def generate_signal(prices, short_window=20, long_window=50):
    """Generate a simple buy/sell/hold signal using moving average crossover."""
    if not isinstance(prices, pd.Series):
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
