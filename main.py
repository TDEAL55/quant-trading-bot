from error_handler import ErrorHandler, MarketDataError
from market_data import download_price_data
from strategy import generate_signal
from backtest import run_backtest
from risk_manager import RiskManager
from report import print_report


def run_analysis(ticker="SPY", start_date="2020-01-01", end_date="2025-01-01"):
    """Run the full paper-trading analysis workflow using the project modules."""
    error_handler = ErrorHandler()

    try:
        prices = download_price_data(ticker, start_date, end_date)

        latest_close = prices["close"].iloc[-1]
        signal = generate_signal(prices["close"], 20, 50)

        risk_manager = RiskManager()
        portfolio_value = 10000.0
        trade_value = portfolio_value * 0.1
        risk_ok = risk_manager.approve_trade(portfolio_value, trade_value)

        backtest_result = run_backtest(ticker, start_date, end_date)

        print(f"Latest signal: {signal}")
        print(f"Risk check passed: {risk_ok}")
        print(f"Latest close price: {latest_close:.2f}")
        print_report(backtest_result)

        return {
            "status": "ok",
            "signal": signal,
            "risk_ok": risk_ok,
            "latest_close": latest_close,
            "backtest_result": backtest_result,
        }
    except MarketDataError as exc:
        result = error_handler.handle_error(exc, context="downloading market data", shutdown=False)
        return {"status": "error", "message": result["message"]}
    except Exception as exc:
        result = error_handler.handle_error(exc, context="running analysis", shutdown=True)
        return {"status": "error", "message": result["message"]}


if __name__ == "__main__":
    run_analysis()
