from itertools import product

from backtest import run_backtest
from performance_metrics import calculate_metrics
from strategy_parameters import StrategyParameters


def run_experiment(ticker="SPY", start_date="2020-01-01", end_date="2025-01-01"):
    """Run a small parameter sweep for the moving-average strategy and report results."""
    parameter_sets = [
        StrategyParameters(short_window=10, long_window=20),
        StrategyParameters(short_window=20, long_window=50),
        StrategyParameters(short_window=5, long_window=20),
    ]

    results = []
    for params in parameter_sets:
        backtest_result = run_backtest(
            ticker,
            start_date,
            end_date,
            strategy_parameters=params,
        )
        metrics = calculate_metrics(backtest_result)
        results.append(
            {
                "params": params.to_dict(),
                "total_return": backtest_result["total_return"],
                "sharpe_ratio": metrics["sharpe_ratio"],
                "max_drawdown": backtest_result["max_drawdown"],
                "number_of_trades": backtest_result["number_of_trades"],
            }
        )

    ranked_results = sorted(
        results,
        key=lambda item: (
            -item["total_return"],
            -item["sharpe_ratio"],
            item["max_drawdown"],
        ),
    )

    return ranked_results


if __name__ == "__main__":
    for item in run_experiment():
        print(item)
