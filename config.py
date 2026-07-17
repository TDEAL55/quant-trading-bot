import os
import math

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
BENCHMARK_SYMBOL = os.getenv("BENCHMARK_SYMBOL", "SPY")
STRATEGY_MODE = os.getenv("STRATEGY_MODE", "MULTI_FACTOR").upper()
SIGNAL_HYSTERESIS_BUFFER = float(os.getenv("SIGNAL_HYSTERESIS_BUFFER", "2.5"))

SIGNAL_THRESHOLDS = {
    "strong_buy": float(os.getenv("SIGNAL_THRESHOLD_STRONG_BUY", "80")),
    "buy": float(os.getenv("SIGNAL_THRESHOLD_BUY", "65")),
    "hold": float(os.getenv("SIGNAL_THRESHOLD_HOLD", "45")),
    "reduce": float(os.getenv("SIGNAL_THRESHOLD_REDUCE", "30")),
}

FACTOR_WEIGHTS = {
    "trend": float(os.getenv("FACTOR_WEIGHT_TREND", "0.30")),
    "momentum": float(os.getenv("FACTOR_WEIGHT_MOMENTUM", "0.20")),
    "volume": float(os.getenv("FACTOR_WEIGHT_VOLUME", "0.15")),
    "volatility": float(os.getenv("FACTOR_WEIGHT_VOLATILITY", "0.10")),
    "market_regime": float(os.getenv("FACTOR_WEIGHT_MARKET_REGIME", "0.15")),
    "risk_quality": float(os.getenv("FACTOR_WEIGHT_RISK_QUALITY", "0.10")),
}


def validate_factor_weights(weights=None):
    selected = dict(weights or FACTOR_WEIGHTS)
    if any(float(value) < 0 for value in selected.values()):
        raise ValueError("Factor weights must be non-negative")
    total = sum(float(value) for value in selected.values())
    if not math.isclose(total, 1.0, rel_tol=1e-9, abs_tol=1e-9):
        raise ValueError(f"Factor weights must sum to 1.0, got {total:.6f}")
    return {name: float(value) for name, value in selected.items()}


def validate_signal_thresholds(thresholds=None):
    selected = dict(thresholds or SIGNAL_THRESHOLDS)
    required = ["strong_buy", "buy", "hold", "reduce"]
    ordered = [float(selected[name]) for name in required]
    if not (100.0 >= ordered[0] > ordered[1] > ordered[2] > ordered[3] >= 0.0):
        raise ValueError("Signal thresholds must satisfy strong_buy > buy > hold > reduce within 0-100")
    return {name: float(selected[name]) for name in required}


def is_safe_mode(mode=None):
    """Return True only for non-live trading modes for safety."""
    # This guard prevents live trading from being enabled in the current research-only build.
    selected_mode = (mode or TRADING_MODE).upper()
    allowed_modes = {"SIMULATION", "PAPER"}
    return selected_mode in allowed_modes
