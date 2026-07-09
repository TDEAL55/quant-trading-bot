import numpy as np
import pandas as pd


def calculate_metrics(result, annualization_periods=252):
    """Calculate basic performance metrics from a backtest result dictionary."""
    results = result.get("results", pd.DataFrame())
    if results.empty:
        return {
            "annualized_return": 0.0,
            "volatility": 0.0,
            "sharpe_ratio": 0.0,
            "win_rate": 0.0,
            "average_winning_trade": 0.0,
            "average_losing_trade": 0.0,
            "profit_factor": 0.0,
        }

    portfolio_values = results["portfolio_value"].astype(float)
    returns = portfolio_values.pct_change().dropna()

    annualized_return = (1 + result.get("total_return", 0.0)) ** (annualization_periods / len(returns)) - 1 if len(returns) else 0.0
    volatility = returns.std() * np.sqrt(annualization_periods) if len(returns) else 0.0
    sharpe_ratio = annualized_return / volatility if volatility else 0.0

    trade_log = result.get("trade_log", [])
    if trade_log:
        trade_values = [entry.get("portfolio_value", 0.0) for entry in trade_log]
        winning_trades = [value for value in trade_values if value > 0]
        losing_trades = [value for value in trade_values if value < 0]
        win_rate = len(winning_trades) / len(trade_values) if trade_values else 0.0
        average_winning_trade = sum(winning_trades) / len(winning_trades) if winning_trades else 0.0
        average_losing_trade = sum(losing_trades) / len(losing_trades) if losing_trades else 0.0
        gross_profit = sum(max(value, 0.0) for value in trade_values)
        gross_loss = abs(sum(min(value, 0.0) for value in trade_values))
        profit_factor = gross_profit / gross_loss if gross_loss else 0.0
    else:
        win_rate = 0.0
        average_winning_trade = 0.0
        average_losing_trade = 0.0
        profit_factor = 0.0

    return {
        "annualized_return": annualized_return,
        "volatility": volatility,
        "sharpe_ratio": sharpe_ratio,
        "win_rate": win_rate,
        "average_winning_trade": average_winning_trade,
        "average_losing_trade": average_losing_trade,
        "profit_factor": profit_factor,
    }
