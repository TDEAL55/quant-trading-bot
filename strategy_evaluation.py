import pandas as pd

from backtest import run_backtest
from market_data import download_price_data


def evaluate_strategy(ticker="SPY", start_date="2020-01-01", end_date="2025-01-01"):
    """Evaluate a strategy with a simple train/test split and out-of-sample comparison."""
    prices = download_price_data(ticker, start_date, end_date).dropna()

    # Split into an in-sample training period and an out-of-sample test period.
    split_index = int(len(prices) * 0.7)
    train_data = prices.iloc[:split_index]
    test_data = prices.iloc[split_index:]

    # Use the same simple backtest logic for both periods.
    train_start = train_data.index[0].strftime("%Y-%m-%d")
    train_end = train_data.index[-1].strftime("%Y-%m-%d")
    test_start = test_data.index[0].strftime("%Y-%m-%d")
    test_end = test_data.index[-1].strftime("%Y-%m-%d")

    train_result = run_backtest(ticker, train_start, train_end)
    test_result = run_backtest(ticker, test_start, test_end)

    # Overfitting is reduced by checking whether the strategy performs well on unseen data.
    overfit_risk = test_result["total_return"] < train_result["total_return"]

    return {
        "ticker": ticker,
        "train_period": (train_start, train_end),
        "test_period": (test_start, test_end),
        "train_return": train_result["total_return"],
        "test_return": test_result["total_return"],
        "train_drawdown": train_result["max_drawdown"],
        "test_drawdown": test_result["max_drawdown"],
        "train_trades": train_result["number_of_trades"],
        "test_trades": test_result["number_of_trades"],
        "overfit_risk": overfit_risk,
    }


if __name__ == "__main__":
    evaluation = evaluate_strategy()
    print(evaluation)
