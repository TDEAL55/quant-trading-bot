import pandas as pd

from config import BENCHMARK_SYMBOL, STRATEGY_MODE
from market_data import download_price_data
from strategy import generate_signal, generate_strategy_result


def _annualized_return(total_return, periods):
    if periods <= 0 or total_return <= -1:
        return None
    years = periods / 252.0
    if years <= 0:
        return None
    return (1 + total_return) ** (1 / years) - 1


def _sharpe_ratio(daily_returns: pd.Series):
    cleaned = daily_returns.dropna()
    if cleaned.empty or cleaned.std() == 0:
        return 0.0
    return float((cleaned.mean() / cleaned.std()) * (252 ** 0.5))


def _trade_summary(trade_log):
    if not trade_log:
        return 0.0, 0.0
    completed_returns = []
    current_buy_price = None
    for trade in trade_log:
        if trade["signal"] == "buy":
            current_buy_price = trade["price"]
        elif trade["signal"] == "sell" and current_buy_price:
            completed_returns.append((trade["price"] / current_buy_price) - 1.0)
            current_buy_price = None
    if not completed_returns:
        return 0.0, 0.0
    wins = len([value for value in completed_returns if value > 0])
    return wins / len(completed_returns), sum(completed_returns) / len(completed_returns)


def _benchmark_history(ticker, start_date, end_date):
    return download_price_data(ticker, start_date, end_date).dropna(subset=["close"])


def _signal_for_history(history, strategy_mode, short_window, long_window, ticker, benchmark_history, previous_signal):
    selected_mode = str(strategy_mode or STRATEGY_MODE).upper()
    if selected_mode == "LEGACY_MA":
        if len(history) < long_window + 1:
            return "hold", None
        return generate_signal(history["close"], short_window, long_window), None
    result = generate_strategy_result(
        history,
        short_window=short_window,
        long_window=long_window,
        strategy_mode=selected_mode,
        symbol=ticker,
        benchmark_prices=benchmark_history,
        previous_signal=previous_signal,
    )
    return result.get("legacy_signal", "hold"), result


def run_backtest(
    ticker,
    start_date,
    end_date,
    initial_cash=10000,
    short_window=20,
    long_window=50,
    transaction_fee=0.001,
    slippage=0.001,
    strategy_parameters=None,
    strategy_mode=None,
    benchmark_ticker=None,
):
    """Run a paper-trading backtest using the selected research signal mode."""
    prices = download_price_data(ticker, start_date, end_date)
    prices = prices.dropna(subset=["close"])
    benchmark_symbol = benchmark_ticker or BENCHMARK_SYMBOL or ticker
    benchmark_prices = _benchmark_history(benchmark_symbol, start_date, end_date)

    if strategy_parameters is not None:
        short_window = strategy_parameters.short_window
        long_window = strategy_parameters.long_window

    cash = initial_cash
    shares = 0.0
    portfolio_values = []
    position_flags = []
    trade_count = 0
    trade_log = []
    previous_research_signal = None
    research_results = []

    for i, (date, row) in enumerate(prices.iterrows()):
        close_price = float(row["close"])
        history = prices.iloc[:i]
        benchmark_history = benchmark_prices.loc[:date].iloc[:-1] if not benchmark_prices.empty else benchmark_prices
        signal, research_result = _signal_for_history(
            history,
            strategy_mode=strategy_mode,
            short_window=short_window,
            long_window=long_window,
            ticker=ticker,
            benchmark_history=benchmark_history,
            previous_signal=previous_research_signal,
        )
        if research_result is not None:
            previous_research_signal = research_result.get("signal")
            research_results.append({"date": date, **research_result})

        if signal == "buy" and cash > 0:
            buy_cost = close_price * (1 + transaction_fee + slippage)
            shares_to_buy = cash / buy_cost
            cash -= shares_to_buy * buy_cost
            shares += shares_to_buy
            trade_count += 1
            trade_log.append(
                {
                    "date": date,
                    "signal": signal,
                    "price": close_price,
                    "shares": shares_to_buy,
                    "cash": cash,
                    "portfolio_value": cash + (shares * close_price),
                }
            )
        elif signal == "sell" and shares > 0:
            sale_proceeds = shares * close_price * (1 - transaction_fee - slippage)
            cash += sale_proceeds
            shares = 0.0
            trade_count += 1
            trade_log.append(
                {
                    "date": date,
                    "signal": signal,
                    "price": close_price,
                    "shares": shares,
                    "cash": cash,
                    "portfolio_value": cash + (shares * close_price),
                }
            )

        portfolio_value = cash + (shares * close_price)
        portfolio_values.append(portfolio_value)
        position_flags.append(1 if shares > 0 else 0)

    results = pd.DataFrame(
        {
            "date": prices.index,
            "close": prices["close"],
            "portfolio_value": portfolio_values,
            "in_market": position_flags,
        }
    )
    results["daily_return"] = results["portfolio_value"].pct_change()
    results["drawdown"] = 1 - (results["portfolio_value"] / results["portfolio_value"].cummax())

    final_value = results["portfolio_value"].iloc[-1]
    total_return = (final_value / initial_cash) - 1
    max_drawdown = results["drawdown"].max()
    annualized_return = _annualized_return(total_return, len(results))
    sharpe_ratio = _sharpe_ratio(results["daily_return"])
    exposure = float(results["in_market"].mean()) if not results.empty else 0.0
    win_rate, average_trade = _trade_summary(trade_log)

    first_price = float(prices.iloc[0]["close"])
    buy_and_hold_shares = initial_cash / (first_price * (1 + transaction_fee + slippage))
    buy_and_hold_value = buy_and_hold_shares * float(prices.iloc[-1]["close"])
    buy_and_hold_return = (buy_and_hold_value / initial_cash) - 1
    benchmark_first_price = float(benchmark_prices.iloc[0]["close"])
    benchmark_last_price = float(benchmark_prices.iloc[-1]["close"])
    benchmark_return = (benchmark_last_price / benchmark_first_price) - 1

    return {
        "ticker": ticker,
        "strategy_mode": str(strategy_mode or STRATEGY_MODE).upper(),
        "initial_cash": initial_cash,
        "final_portfolio_value": final_value,
        "total_return": total_return,
        "annualized_return": annualized_return,
        "number_of_trades": trade_count,
        "max_drawdown": max_drawdown,
        "sharpe_ratio": sharpe_ratio,
        "win_rate": win_rate,
        "average_trade": average_trade,
        "exposure": exposure,
        "buy_and_hold_final_value": buy_and_hold_value,
        "buy_and_hold_return": buy_and_hold_return,
        "benchmark_return": benchmark_return,
        "strategy_vs_buy_and_hold": total_return - buy_and_hold_return,
        "transaction_fee": transaction_fee,
        "slippage": slippage,
        "trade_log": trade_log,
        "research_results": research_results,
        "results": results,
    }


def compare_strategy_modes(
    ticker,
    start_date,
    end_date,
    initial_cash=10000,
    short_window=20,
    long_window=50,
    transaction_fee=0.001,
    slippage=0.001,
):
    legacy = run_backtest(
        ticker,
        start_date,
        end_date,
        initial_cash=initial_cash,
        short_window=short_window,
        long_window=long_window,
        transaction_fee=transaction_fee,
        slippage=slippage,
        strategy_mode="LEGACY_MA",
    )
    multi_factor = run_backtest(
        ticker,
        start_date,
        end_date,
        initial_cash=initial_cash,
        short_window=short_window,
        long_window=long_window,
        transaction_fee=transaction_fee,
        slippage=slippage,
        strategy_mode="MULTI_FACTOR",
    )
    return {
        "legacy_ma": legacy,
        "multi_factor": multi_factor,
        "buy_and_hold": {
            "ticker": ticker,
            "total_return": multi_factor["buy_and_hold_return"],
            "benchmark_return": multi_factor["benchmark_return"],
            "max_drawdown": multi_factor["max_drawdown"],
            "number_of_trades": 1,
        },
    }
