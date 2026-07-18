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
SCANNER_VERSION = os.getenv("SCANNER_VERSION", "sprint2-scanner-v1")
STRATEGY_VERSION = os.getenv("STRATEGY_VERSION", "multi_factor-v1")
RESEARCH_JOURNAL_VERSION = os.getenv("RESEARCH_JOURNAL_VERSION", "sprint3-research-journal-v1")
FORWARD_RETURN_HORIZONS = tuple(
    int(value)
    for value in os.getenv("FORWARD_RETURN_HORIZONS", "1,5,10,20").split(",")
    if str(value).strip()
)
FORWARD_RETURN_MAX_LABEL_BATCH_SIZE = int(os.getenv("FORWARD_RETURN_MAX_LABEL_BATCH_SIZE", "100"))
FORWARD_RETURN_RETRY_LIMIT = int(os.getenv("FORWARD_RETURN_RETRY_LIMIT", "2"))
FORWARD_RETURN_MIN_CORRELATION_SAMPLE_SIZE = int(os.getenv("FORWARD_RETURN_MIN_CORRELATION_SAMPLE_SIZE", "5"))
FORWARD_RETURN_PRICE_LOOKBACK_DAYS = int(os.getenv("FORWARD_RETURN_PRICE_LOOKBACK_DAYS", "30"))
FACTOR_ATTRIBUTION_MIN_SAMPLE_SIZE = int(os.getenv("FACTOR_ATTRIBUTION_MIN_SAMPLE_SIZE", "5"))
FACTOR_ATTRIBUTION_COMBINATION_MIN_SAMPLE_SIZE = int(os.getenv("FACTOR_ATTRIBUTION_COMBINATION_MIN_SAMPLE_SIZE", "3"))

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


def _parse_csv_env(value: str, default: str = "") -> list[str]:
    raw = str(value if value is not None else default)
    return [part.strip() for part in raw.split(",") if part.strip()]


def _parse_bool_env(value: str, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "on"}


SCANNER_UNIVERSES = _parse_csv_env(
    os.getenv(
        "SCANNER_UNIVERSES",
        "sp500,nasdaq100,midcap_liquid,ai_software,semiconductors,data_center_infra,utilities_power,data_center_reits,benchmarks",
    )
)
SCANNER_INCLUDE_ETFS = _parse_bool_env(os.getenv("SCANNER_INCLUDE_ETFS", "true"), default=True)
SCANNER_MAX_UNIVERSE_SIZE = int(os.getenv("SCANNER_MAX_UNIVERSE_SIZE", "400"))
SCANNER_EXCLUDED_SYMBOLS = _parse_csv_env(os.getenv("SCANNER_EXCLUDED_SYMBOLS", ""))
SCANNER_ADDITIONAL_SYMBOLS = _parse_csv_env(os.getenv("SCANNER_ADDITIONAL_SYMBOLS", ""))

SCANNER_MIN_PRICE = float(os.getenv("SCANNER_MIN_PRICE", "5"))
SCANNER_MIN_AVG_DOLLAR_VOLUME = float(os.getenv("SCANNER_MIN_AVG_DOLLAR_VOLUME", "20000000"))
SCANNER_MIN_HISTORY_DAYS = int(os.getenv("SCANNER_MIN_HISTORY_DAYS", "220"))
SCANNER_MAX_MISSING_PERCENT = float(os.getenv("SCANNER_MAX_MISSING_PERCENT", "2"))
SCANNER_MAX_STALE_BUSINESS_DAYS = int(os.getenv("SCANNER_MAX_STALE_BUSINESS_DAYS", "5"))

SCANNER_MIN_SCORE = float(os.getenv("SCANNER_MIN_SCORE", "70"))
SCANNER_MIN_CONFIDENCE = float(os.getenv("SCANNER_MIN_CONFIDENCE", "60"))
SCANNER_MIN_RISK_QUALITY = float(os.getenv("SCANNER_MIN_RISK_QUALITY", "45"))
SCANNER_MIN_VOLATILITY_SCORE = float(os.getenv("SCANNER_MIN_VOLATILITY_SCORE", "35"))
SCANNER_ALLOWED_SIGNALS = _parse_csv_env(os.getenv("SCANNER_ALLOWED_SIGNALS", "BUY,STRONG_BUY"))
SCANNER_BLOCKED_REGIMES = _parse_csv_env(os.getenv("SCANNER_BLOCKED_REGIMES", "strong_bear,high_volatility_risk_off"))

SCANNER_MAX_WORKERS = int(os.getenv("SCANNER_MAX_WORKERS", "5"))
SCANNER_SYMBOL_TIMEOUT_SECONDS = int(os.getenv("SCANNER_SYMBOL_TIMEOUT_SECONDS", "45"))
SCANNER_MAX_RETRIES = int(os.getenv("SCANNER_MAX_RETRIES", "2"))
SCANNER_BATCH_SIZE = int(os.getenv("SCANNER_BATCH_SIZE", "25"))

SCANNER_RANK_WEIGHT_OVERALL = float(os.getenv("SCANNER_RANK_WEIGHT_OVERALL", "0.45"))
SCANNER_RANK_WEIGHT_CONFIDENCE = float(os.getenv("SCANNER_RANK_WEIGHT_CONFIDENCE", "0.20"))
SCANNER_RANK_WEIGHT_RISK_QUALITY = float(os.getenv("SCANNER_RANK_WEIGHT_RISK_QUALITY", "0.15"))
SCANNER_RANK_WEIGHT_TREND = float(os.getenv("SCANNER_RANK_WEIGHT_TREND", "0.10"))
SCANNER_RANK_WEIGHT_LIQUIDITY = float(os.getenv("SCANNER_RANK_WEIGHT_LIQUIDITY", "0.10"))

PORTFOLIO_MAX_CANDIDATES = int(os.getenv("PORTFOLIO_MAX_CANDIDATES", "10"))
PORTFOLIO_MAX_POSITIONS = int(os.getenv("PORTFOLIO_MAX_POSITIONS", "5"))
PORTFOLIO_MAX_SYMBOLS_PER_SECTOR = int(os.getenv("PORTFOLIO_MAX_SYMBOLS_PER_SECTOR", "2"))
PORTFOLIO_MAX_SECTOR_PERCENT = float(os.getenv("PORTFOLIO_MAX_SECTOR_PERCENT", "20"))
PORTFOLIO_MAX_SYMBOL_PERCENT = float(os.getenv("PORTFOLIO_MAX_SYMBOL_PERCENT", "5"))
PORTFOLIO_MIN_CASH_RESERVE_PERCENT = float(os.getenv("PORTFOLIO_MIN_CASH_RESERVE_PERCENT", "25"))

POSITION_REVIEW_MIN_HOLD_SCORE = float(os.getenv("POSITION_REVIEW_MIN_HOLD_SCORE", "58"))
POSITION_REVIEW_MIN_WATCH_SCORE = float(os.getenv("POSITION_REVIEW_MIN_WATCH_SCORE", "45"))
POSITION_REVIEW_MAX_HOLD_DAYS = int(os.getenv("POSITION_REVIEW_MAX_HOLD_DAYS", "90"))
POSITION_REVIEW_RISK_OFF_REGIMES = _parse_csv_env(
    os.getenv("POSITION_REVIEW_RISK_OFF_REGIMES", "strong_bear,high_volatility_risk_off")
)
