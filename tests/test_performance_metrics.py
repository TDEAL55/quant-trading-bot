from backtest import run_backtest
from performance_metrics import calculate_metrics


def test_calculate_metrics_returns_expected_keys():
    result = run_backtest("SPY", "2020-01-01", "2020-01-10")
    metrics = calculate_metrics(result)
    assert set(metrics.keys()) == {
        "annualized_return",
        "volatility",
        "sharpe_ratio",
        "win_rate",
        "average_winning_trade",
        "average_losing_trade",
        "profit_factor",
    }
