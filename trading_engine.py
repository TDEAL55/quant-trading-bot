from logger_setup import logger
from paper_broker import create_paper_broker
from risk_manager import RiskManager


def evaluate_signal(signal, portfolio_value=10000.0, trade_value=1000.0):
    """Translate a strategy signal into a simulation-only trade decision."""
    broker = create_paper_broker()
    risk_manager = RiskManager()

    # A real broker would be used here later, but this module stays paper-only.
    if signal == "buy":
        approved = risk_manager.approve_trade(portfolio_value, trade_value)
        logger.info(f"risk decision for buy: {approved}")
        if approved:
            logger.info("simulated decision: BUY")
            return "BUY"
        logger.info("simulated decision: HOLD")
        return "HOLD"

    if signal == "sell":
        approved = risk_manager.approve_trade(portfolio_value, trade_value)
        logger.info(f"risk decision for sell: {approved}")
        if approved:
            logger.info("simulated decision: SELL")
            return "SELL"
        logger.info("simulated decision: HOLD")
        return "HOLD"

    logger.info("simulated decision: HOLD")
    return "HOLD"


def create_simulated_trade_decision(signal, portfolio_value=10000.0, trade_value=1000.0):
    """Alias for evaluate_signal for clarity in the workflow."""
    return evaluate_signal(signal, portfolio_value, trade_value)
