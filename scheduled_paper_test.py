import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from alpaca.trading.client import TradingClient
from dotenv import load_dotenv

from logger_setup import logger
from paper_order import create_paper_order_manager


EASTERN_TZ = ZoneInfo("America/New_York")


def get_next_weekday_10am_eastern(now=None):
    """Return the next weekday at 10:00 AM Eastern."""
    current = (now or datetime.now(EASTERN_TZ)).astimezone(EASTERN_TZ)
    target = current.replace(hour=10, minute=0, second=0, microsecond=0)

    while target <= current or target.weekday() >= 5:
        target = (target + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)

    return target


def run_scheduled_paper_test_once(
    now=None,
    sleep_fn=time.sleep,
    env_path=None,
    trading_client_factory=TradingClient,
    order_manager_factory=create_paper_order_manager,
):
    """Run one scheduled dry-run paper test and return a summary."""
    dotenv_path = Path(env_path) if env_path else Path(__file__).resolve().parent / ".env"
    load_dotenv(dotenv_path=dotenv_path, override=False)

    mode = os.getenv("TRADING_MODE", "SIMULATION").upper()
    if mode == "LIVE":
        raise RuntimeError("LIVE mode is blocked for scheduled_paper_test.")
    if mode != "PAPER":
        raise RuntimeError("scheduled_paper_test requires TRADING_MODE=PAPER")

    current = (now or datetime.now(EASTERN_TZ)).astimezone(EASTERN_TZ)
    scheduled_for = get_next_weekday_10am_eastern(current)
    wait_seconds = max(0.0, (scheduled_for - current).total_seconds())
    if wait_seconds > 0:
        sleep_fn(wait_seconds)

    summary = {
        "scheduled_for": scheduled_for.isoformat(),
        "account_status": "unavailable",
        "market_open": False,
        "order_result": {
            "approved": False,
            "reason": "not executed",
            "submitted": False,
            "simulated_order": None,
        },
    }

    try:
        api_key = os.getenv("ALPACA_API_KEY", "")
        api_secret = os.getenv("ALPACA_API_SECRET", "")
        if not api_key or not api_secret:
            raise ValueError("missing credentials: ALPACA_API_KEY, ALPACA_API_SECRET")

        trading_client = trading_client_factory(api_key=api_key, secret_key=api_secret, paper=True)
        account = trading_client.get_account()
        summary["account_status"] = str(getattr(account, "status", "unknown"))
        clock = trading_client.get_clock()
        summary["market_open"] = bool(getattr(clock, "is_open", False))

        order_manager = order_manager_factory(
            mode="PAPER",
            dry_run=True,
            submit_enabled=False,
            trading_client=trading_client,
        )
        summary["order_result"] = order_manager.place_order(command="BUY $10 of SPY")
        logger.info(
            "scheduled_paper_test completed account_status=%s market_open=%s order_approved=%s reason=%s",
            summary["account_status"],
            summary["market_open"],
            summary["order_result"].get("approved"),
            summary["order_result"].get("reason"),
        )
    except Exception as exc:
        summary["order_result"] = {
            "approved": False,
            "reason": str(exc),
            "submitted": False,
            "simulated_order": None,
        }
        logger.error("scheduled_paper_test failed: %s", exc)

    return summary


def main():
    run_scheduled_paper_test_once()


if __name__ == "__main__":
    main()