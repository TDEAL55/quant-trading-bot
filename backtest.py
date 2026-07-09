import pandas as pd

from market_data import download_price_data
from strategy import generate_signal


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
):
    """Run a simple paper-trading backtest using moving-average crossover signals."""
    prices = download_price_data(ticker, start_date, end_date)
    prices = prices.dropna()

    if strategy_parameters is not None:
        short_window = strategy_parameters.short_window
        long_window = strategy_parameters.long_window

    cash = initial_cash
    shares = 0.0
    portfolio_values = []
    trade_count = 0
    trade_log = []

    # The simulation loops over each day and applies the current signal.
    # Buy and sell actions only change the internal paper portfolio state.
    for i, (date, row) in enumerate(prices.iterrows()):
        close_price = float(row["close"])
        history = prices["close"].iloc[:i]

        if len(history) < long_window + 1:
            signal = "hold"
        else:
            signal = generate_signal(history, short_window, long_window)

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

    results = pd.DataFrame(
        {
            "date": prices.index,
            "close": prices["close"],
            "portfolio_value": portfolio_values,
        }
    )
    results["daily_return"] = results["portfolio_value"].pct_change()
    results["drawdown"] = 1 - (results["portfolio_value"] / results["portfolio_value"].cummax())

    final_value = results["portfolio_value"].iloc[-1]
    total_return = (final_value / initial_cash) - 1
    max_drawdown = results["drawdown"].max()

    # Buy and hold is a simple benchmark that buys once at the first available price.
    # It helps compare the strategy against a passive approach.
    first_price = float(prices.iloc[0]["close"])
    buy_and_hold_shares = initial_cash / (first_price * (1 + transaction_fee + slippage))
    buy_and_hold_value = buy_and_hold_shares * float(prices.iloc[-1]["close"])
    buy_and_hold_return = (buy_and_hold_value / initial_cash) - 1

    return {
        "ticker": ticker,
        "initial_cash": initial_cash,
        "final_portfolio_value": final_value,
        "total_return": total_return,
        "number_of_trades": trade_count,
        "max_drawdown": max_drawdown,
        "buy_and_hold_final_value": buy_and_hold_value,
        "buy_and_hold_return": buy_and_hold_return,
        "strategy_vs_buy_and_hold": total_return - buy_and_hold_return,
        "transaction_fee": transaction_fee,
        "slippage": slippage,
        "trade_log": trade_log,
        "results": results,
    }
