from backtest import run_backtest


def format_report(result):
    """Format backtest results into a readable terminal summary."""
    trade_log = result.get("trade_log", [])

    if trade_log:
        trade_results = [entry.get("portfolio_value", 0) for entry in trade_log]
        average_trade_result = sum(trade_results) / len(trade_results)
        best_trade = max(trade_results)
        worst_trade = min(trade_results)
    else:
        average_trade_result = 0
        best_trade = 0
        worst_trade = 0

    lines = [
        "Performance Report",
        "===================",
        f"Ticker: {result.get('ticker', 'N/A')}",
        f"Total Return: {result.get('total_return', 0):.2%}",
        f"Buy-and-Hold Return: {result.get('buy_and_hold_return', 0):.2%}",
        f"Maximum Drawdown: {result.get('max_drawdown', 0):.2%}",
        f"Number of Trades: {result.get('number_of_trades', 0)}",
        f"Average Trade Result: {average_trade_result:.2f}",
        f"Best Trade: {best_trade:.2f}",
        f"Worst Trade: {worst_trade:.2f}",
    ]

    return "\n".join(lines)


def print_report(result):
    """Print a formatted performance report to the terminal."""
    print(format_report(result))


if __name__ == "__main__":
    sample_result = run_backtest("SPY", "2020-01-01", "2025-01-01")
    print_report(sample_result)
