import pandas as pd

from backtest import run_backtest
from market_data import download_price_data
from strategy import generate_signal


def calculate_rsi(series, window=14):
    """Calculate RSI values for a price series."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(window=window, min_periods=window).mean()
    avg_loss = loss.rolling(window=window, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def generate_rsi_signal(prices, window=14):
    """Generate a simple buy/sell/hold signal from RSI values."""
    rsi = calculate_rsi(prices, window)
    if rsi.iloc[-1] < 30:
        return "buy"
    if rsi.iloc[-1] > 70:
        return "sell"
    return "hold"


def compare_strategies(ticker="SPY", start_date="2020-01-01", end_date="2025-01-01"):
    """Compare a moving-average crossover strategy, buy-and-hold, and an RSI strategy."""
    prices = download_price_data(ticker, start_date, end_date).dropna()

    moving_average_result = run_backtest(ticker, start_date, end_date)

    # Buy-and-hold is represented by the benchmark from the backtest module.
    buy_and_hold_result = {
        "ticker": ticker,
        "total_return": moving_average_result["buy_and_hold_return"],
        "max_drawdown": moving_average_result["max_drawdown"],
        "number_of_trades": 1,
    }

    # A simple RSI-based simulation uses the same backtest structure but with a different signal function.
    cash = 10000.0
    shares = 0.0
    portfolio_value = 10000.0
    trade_count = 0
    portfolio_values = []

    for i, (date, row) in enumerate(prices.iterrows()):
        close_price = float(row["close"])
        history = prices["close"].iloc[:i]
        if len(history) < 14:
            signal = "hold"
        else:
            signal = generate_rsi_signal(history)

        if signal == "buy" and cash > 0:
            shares = cash // close_price
            cash = cash - (shares * close_price)
            trade_count += 1
        elif signal == "sell" and shares > 0:
            cash = cash + (shares * close_price)
            shares = 0.0
            trade_count += 1

        portfolio_value = cash + (shares * close_price)
        portfolio_values.append(portfolio_value)

    final_value = portfolio_values[-1]
    total_return = (final_value / 10000.0) - 1
    drawdown = 1 - (pd.Series(portfolio_values) / pd.Series(portfolio_values).cummax())

    rsi_result = {
        "ticker": ticker,
        "total_return": total_return,
        "max_drawdown": drawdown.max(),
        "number_of_trades": trade_count,
    }

    return {
        "moving_average_crossover": {
            "return": moving_average_result["total_return"],
            "drawdown": moving_average_result["max_drawdown"],
            "trades": moving_average_result["number_of_trades"],
        },
        "buy_and_hold": buy_and_hold_result,
        "rsi": rsi_result,
    }


if __name__ == "__main__":
    comparison = compare_strategies()
    print(comparison)
