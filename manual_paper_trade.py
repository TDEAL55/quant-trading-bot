import os
from pathlib import Path

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest
from dotenv import load_dotenv

from logger_setup import logger


def run_manual_paper_trade(input_fn=input, print_fn=print, env_path=None, client_factory=TradingClient):
    """Run one guarded manual paper order flow for BUY $10 SPY market only."""
    dotenv_path = Path(env_path) if env_path else Path(__file__).resolve().parent / ".env"
    load_dotenv(dotenv_path=dotenv_path, override=False)

    symbol = "SPY"
    notional = 10.0
    order_type = "market"

    result = {
        "submitted_or_canceled": "canceled",
        "symbol": symbol,
        "notional_amount": notional,
        "order_id": "N/A",
        "status": "canceled",
        "error": "",
    }

    try:
        mode = os.getenv("TRADING_MODE", "SIMULATION").upper()
        if mode == "LIVE":
            raise RuntimeError("LIVE mode is blocked")
        if mode != "PAPER":
            raise RuntimeError("manual_paper_trade requires TRADING_MODE=PAPER")

        api_key = os.getenv("ALPACA_API_KEY", "")
        api_secret = os.getenv("ALPACA_API_SECRET", "")
        if not api_key or not api_secret:
            raise RuntimeError("Missing required Alpaca credentials")

        client = client_factory(api_key=api_key, secret_key=api_secret, paper=True)

        clock = client.get_clock()
        if not bool(getattr(clock, "is_open", False)):
            result["status"] = "market_closed"
            logger.info("manual_paper_trade canceled: market is closed")
            return result

        proposed_order = {
            "symbol": symbol,
            "notional": notional,
            "side": "buy",
            "type": order_type,
            "time_in_force": "day",
        }
        print_fn(f"Proposed order: {proposed_order}")

        confirmation = input_fn("Type YES to submit this paper order: ")
        if confirmation != "YES":
            result["status"] = "canceled"
            logger.info("manual_paper_trade canceled: confirmation was not YES")
            return result

        order_request = MarketOrderRequest(
            symbol=symbol,
            notional=notional,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
        order = client.submit_order(order_data=order_request)

        result["submitted_or_canceled"] = "submitted"
        result["order_id"] = str(getattr(order, "id", "N/A"))
        result["status"] = str(getattr(order, "status", "submitted"))
        logger.info(
            "manual_paper_trade submitted symbol=%s notional=%s order_id=%s status=%s",
            symbol,
            notional,
            result["order_id"],
            result["status"],
        )
        return result

    except Exception as exc:
        result["error"] = str(exc)
        result["status"] = "error"
        logger.error("manual_paper_trade failed: %s", exc)
        return result


def main():
    result = run_manual_paper_trade()
    print(f"submitted or canceled: {result['submitted_or_canceled']}")
    print(f"symbol: {result['symbol']}")
    print(f"notional amount: {result['notional_amount']}")
    print(f"order id if submitted: {result['order_id']}")
    print(f"status: {result['status']}")


if __name__ == "__main__":
    main()