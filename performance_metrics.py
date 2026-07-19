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


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def build_equity_curve_metrics(daily_rows, annualization_periods=252):
    if not daily_rows:
        return {
            "portfolio_value": 0.0,
            "cash": 0.0,
            "buying_power": 0.0,
            "daily_return": 0.0,
            "total_return": 0.0,
            "cumulative_return": 0.0,
            "maximum_drawdown": 0.0,
            "current_drawdown": 0.0,
            "volatility": 0.0,
            "daily_returns": [],
            "drawdown_series": [],
        }

    values = [_safe_float(item.get("portfolio_value"), 0.0) for item in daily_rows]
    cash = _safe_float(daily_rows[-1].get("cash"), 0.0)
    buying_power = _safe_float(daily_rows[-1].get("buying_power"), 0.0)

    daily_returns = []
    for idx, value in enumerate(values):
        if idx == 0:
            daily_returns.append(0.0)
            continue
        prev = values[idx - 1]
        daily_returns.append((value / prev) - 1.0 if prev > 0 else 0.0)

    running_max = []
    drawdown_series = []
    maximum = values[0]
    for value in values:
        maximum = max(maximum, value)
        running_max.append(maximum)
        drawdown_series.append(((value / maximum) - 1.0) if maximum > 0 else 0.0)

    total_return = ((values[-1] / values[0]) - 1.0) if values[0] > 0 else 0.0
    volatility = float(np.std(daily_returns[1:], ddof=1) * np.sqrt(annualization_periods)) if len(daily_returns) > 2 else 0.0

    return {
        "portfolio_value": values[-1],
        "cash": cash,
        "buying_power": buying_power,
        "daily_return": daily_returns[-1],
        "total_return": total_return,
        "cumulative_return": total_return,
        "maximum_drawdown": min(drawdown_series),
        "current_drawdown": drawdown_series[-1],
        "volatility": volatility,
        "daily_returns": daily_returns,
        "drawdown_series": drawdown_series,
    }


def build_trade_statistics(trade_pnl, hold_times_days=None):
    pnl = [_safe_float(item, 0.0) for item in trade_pnl]
    holds = [_safe_float(item, 0.0) for item in (hold_times_days or [])]
    if not pnl:
        return {
            "win_rate": 0.0,
            "loss_rate": 0.0,
            "average_winner": 0.0,
            "average_loser": 0.0,
            "profit_factor": 0.0,
            "largest_winner": 0.0,
            "largest_loser": 0.0,
            "average_hold_time": float(np.mean(holds)) if holds else 0.0,
        }

    winners = [item for item in pnl if item > 0]
    losers = [item for item in pnl if item < 0]
    gross_profit = sum(winners)
    gross_loss = abs(sum(losers))

    return {
        "win_rate": len(winners) / len(pnl),
        "loss_rate": len(losers) / len(pnl),
        "average_winner": float(np.mean(winners)) if winners else 0.0,
        "average_loser": float(np.mean(losers)) if losers else 0.0,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else 0.0,
        "largest_winner": max(winners) if winners else 0.0,
        "largest_loser": min(losers) if losers else 0.0,
        "average_hold_time": float(np.mean(holds)) if holds else 0.0,
    }


def build_exposure_metrics(positions, portfolio_value):
    total_value = _safe_float(portfolio_value, 0.0)
    if total_value <= 0:
        return {
            "exposure_pct": 0.0,
            "sector_allocation": {},
            "position_concentration": 0.0,
        }

    sector_alloc = {}
    position_weights = []
    gross_exposure = 0.0
    for row in positions or []:
        value = _safe_float(row.get("market_value"), 0.0)
        sector = str(row.get("sector") or "Unknown")
        gross_exposure += abs(value)
        sector_alloc[sector] = sector_alloc.get(sector, 0.0) + value
        position_weights.append(abs(value) / total_value)

    normalized_sector = {key: (value / total_value) for key, value in sorted(sector_alloc.items())}
    concentration = max(position_weights) if position_weights else 0.0
    return {
        "exposure_pct": gross_exposure / total_value,
        "sector_allocation": normalized_sector,
        "position_concentration": concentration,
    }


def build_benchmark_metrics(portfolio_returns, benchmark_returns, annualization_periods=252):
    if not portfolio_returns or not benchmark_returns:
        return {
            "alpha": 0.0,
            "beta": 0.0,
            "tracking_error": 0.0,
            "excess_return": 0.0,
            "information_ratio": 0.0,
        }

    count = min(len(portfolio_returns), len(benchmark_returns))
    p = np.array(portfolio_returns[-count:], dtype=float)
    b = np.array(benchmark_returns[-count:], dtype=float)
    if len(p) < 2:
        return {
            "alpha": 0.0,
            "beta": 0.0,
            "tracking_error": 0.0,
            "excess_return": 0.0,
            "information_ratio": 0.0,
        }

    cov = float(np.cov(p, b, ddof=1)[0, 1]) if len(p) > 1 else 0.0
    var_b = float(np.var(b, ddof=1)) if len(b) > 1 else 0.0
    beta = cov / var_b if var_b > 0 else 0.0

    mean_p = float(np.mean(p))
    mean_b = float(np.mean(b))
    alpha = (mean_p - beta * mean_b) * annualization_periods
    excess = p - b
    tracking_error = float(np.std(excess, ddof=1) * np.sqrt(annualization_periods)) if len(excess) > 1 else 0.0
    excess_return = float(np.prod(1.0 + p) - np.prod(1.0 + b))
    info_ratio = (float(np.mean(excess)) * np.sqrt(annualization_periods) / float(np.std(excess, ddof=1))) if len(excess) > 1 and float(np.std(excess, ddof=1)) > 0 else 0.0

    return {
        "alpha": alpha,
        "beta": beta,
        "tracking_error": tracking_error,
        "excess_return": excess_return,
        "information_ratio": info_ratio,
    }


def build_risk_ratios(portfolio_returns, max_drawdown, annualization_periods=252):
    if not portfolio_returns:
        return {
            "sharpe_ratio": 0.0,
            "sortino_ratio": 0.0,
            "calmar_ratio": 0.0,
        }

    returns = np.array(portfolio_returns, dtype=float)
    mean_return = float(np.mean(returns))
    std_return = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.0
    downside = returns[returns < 0]
    downside_std = float(np.std(downside, ddof=1)) if len(downside) > 1 else 0.0

    annualized_return = (mean_return * annualization_periods)
    sharpe = (mean_return * np.sqrt(annualization_periods) / std_return) if std_return > 0 else 0.0
    sortino = (mean_return * np.sqrt(annualization_periods) / downside_std) if downside_std > 0 else 0.0
    calmar = (annualized_return / abs(max_drawdown)) if max_drawdown < 0 else 0.0

    return {
        "sharpe_ratio": float(sharpe),
        "sortino_ratio": float(sortino),
        "calmar_ratio": float(calmar),
    }
