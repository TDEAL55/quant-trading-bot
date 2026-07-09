import pandas as pd

from backtest import run_backtest
from market_data import download_price_data
from strategy_parameters import StrategyParameters


def run_walk_forward(ticker="SPY", start_date="2020-01-01", end_date="2025-01-01", window_size=252, step=126):
    """Run a simple walk-forward analysis with sequential train/test periods."""
    prices = download_price_data(ticker, start_date, end_date).dropna()

    if len(prices) < window_size * 2:
        raise ValueError("Not enough data for walk-forward analysis")

    results = []
    for start_idx in range(0, len(prices) - window_size * 2 + 1, step):
        train_data = prices.iloc[start_idx:start_idx + window_size]
        test_data = prices.iloc[start_idx + window_size:start_idx + window_size * 2]

        if len(train_data) < window_size or len(test_data) < 1:
            continue

        train_start = train_data.index[0].strftime("%Y-%m-%d")
        train_end = train_data.index[-1].strftime("%Y-%m-%d")
        test_start = test_data.index[0].strftime("%Y-%m-%d")
        test_end = test_data.index[-1].strftime("%Y-%m-%d")

        params = StrategyParameters(short_window=20, long_window=50)
        train_result = run_backtest(ticker, train_start, train_end, strategy_parameters=params)
        test_result = run_backtest(ticker, test_start, test_end, strategy_parameters=params)

        results.append(
            {
                "train_period": (train_start, train_end),
                "test_period": (test_start, test_end),
                "train_return": train_result["total_return"],
                "test_return": test_result["total_return"],
                "test_drawdown": test_result["max_drawdown"],
                "test_trades": test_result["number_of_trades"],
            }
        )

    return results


if __name__ == "__main__":
    for result in run_walk_forward():
        print(result)
