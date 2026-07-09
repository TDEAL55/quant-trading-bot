from market_data import download_price_data
from strategy import generate_signal
from trading_engine import create_simulated_trade_decision
from logger_setup import logger
from paper_broker import create_paper_broker
from risk_manager import RiskManager
from trade_journal import save_trade_decision


def run_simulation(ticker="SPY", start_date="2020-01-01", end_date="2025-01-01"):
    """Run a simple simulation workflow using paper-only modules."""
    logger.info("starting simulation")

    # 1. Download market data.
    prices = download_price_data(ticker, start_date, end_date)
    logger.info(f"downloaded market data for {ticker}")

    # 2. Generate a signal from the latest closing price.
    latest_close = prices["close"].iloc[-1]
    signal = generate_signal(prices["close"], 20, 50)
    logger.info(f"generated signal: {signal}")

    # 3. Evaluate risk.
    risk_manager = RiskManager()
    risk_ok = risk_manager.approve_trade(10000.0, 1000.0)
    logger.info(f"risk decision: {risk_ok}")

    # 4. Create a simulated trade decision.
    broker = create_paper_broker()
    account = broker.get_account()
    buying_power = broker.get_buying_power()
    positions = broker.get_positions()
    logger.info(f"broker account check: {account}")
    logger.info(f"broker buying power check: {buying_power}")
    logger.info(f"broker positions check: {positions}")
    decision = create_simulated_trade_decision(signal, 10000.0, 1000.0)
    logger.info(f"simulated trade decision: {decision}")
    journal_record = save_trade_decision(
        symbol=ticker,
        signal=signal,
        decision=decision,
        price=latest_close,
        reason="simulation signal",
        risk_result=risk_ok,
        portfolio_value=10000.0,
    )
    logger.info(f"saved simulation journal entry: {journal_record}")

    # 5. Log the result and print a simple summary.
    summary = {
        "ticker": ticker,
        "signal": signal,
        "decision": decision,
        "risk_ok": risk_ok,
        "latest_close": latest_close,
        "buying_power": buying_power,
        "positions": positions,
    }
    logger.info(f"simulation summary: {summary}")
    print(summary)
    return summary


if __name__ == "__main__":
    run_simulation()
