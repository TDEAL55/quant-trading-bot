import os

from dotenv import load_dotenv


load_dotenv()


ROBINHOOD_USERNAME = os.getenv("ROBINHOOD_USERNAME", "")
ROBINHOOD_PASSWORD = os.getenv("ROBINHOOD_PASSWORD", "")
ROBINHOOD_TOTP_SECRET = os.getenv("ROBINHOOD_TOTP_SECRET", "")
API_KEY = os.getenv("API_KEY", "")
API_SECRET = os.getenv("API_SECRET", "")
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_API_SECRET = os.getenv("ALPACA_API_SECRET", "")


TICKER_SYMBOL = os.getenv("TICKER_SYMBOL", "SPY")
BACKTEST_START_DATE = os.getenv("BACKTEST_START_DATE", "2020-01-01")
BACKTEST_END_DATE = os.getenv("BACKTEST_END_DATE", "2025-01-01")
STARTING_CASH = float(os.getenv("STARTING_CASH", "10000"))
SHORT_MOVING_AVERAGE = int(os.getenv("SHORT_MOVING_AVERAGE", "20"))
LONG_MOVING_AVERAGE = int(os.getenv("LONG_MOVING_AVERAGE", "50"))
MAX_POSITION_SIZE = float(os.getenv("MAX_POSITION_SIZE", "0.25"))
MAX_DAILY_LOSS = float(os.getenv("MAX_DAILY_LOSS", "500"))
DAILY_LOSS_LIMIT = float(os.getenv("DAILY_LOSS_LIMIT", "500"))
TRADING_MODE = os.getenv("TRADING_MODE", "SIMULATION").upper()
PAPER_API_BASE_URL = os.getenv("PAPER_API_BASE_URL", "")
PAPER_API_USERNAME = os.getenv("PAPER_API_USERNAME", "")
PAPER_API_PASSWORD = os.getenv("PAPER_API_PASSWORD", "")
PAPER_API_TOKEN = os.getenv("PAPER_API_TOKEN", "")


def is_safe_mode(mode=None):
    """Return True only for non-live trading modes for safety."""
    # This guard prevents live trading from being enabled in the current research-only build.
    selected_mode = (mode or TRADING_MODE).upper()
    allowed_modes = {"SIMULATION", "PAPER"}
    return selected_mode in allowed_modes
