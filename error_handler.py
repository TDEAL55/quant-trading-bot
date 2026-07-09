import logging

from logger_setup import setup_logger


class TradingBotError(Exception):
    """Base class for trading-bot-specific exceptions."""


class MarketDataError(TradingBotError):
    """Raised when market data cannot be loaded or is incomplete."""


class CalculationError(TradingBotError):
    """Raised when a strategy or metric calculation fails."""


class ConfigurationError(TradingBotError):
    """Raised when configuration or runtime settings are invalid."""


class ErrorHandler:
    """Centralize error handling, logging, and controlled shutdown behavior."""

    def __init__(self, logger=None, shutdown_hook=None):
        self.logger = logger or setup_logger("trading_bot.errors")
        self.shutdown_hook = shutdown_hook

    def handle_error(self, error, context=None, shutdown=False):
        context_text = f" while {context}" if context else ""

        if isinstance(error, MarketDataError):
            message = f"Market data issue{context_text}: {error}"
            recovery = (
                "Recovery: verify the ticker, date range, and network access, "
                "then rerun the analysis."
            )
        elif isinstance(error, CalculationError):
            message = f"Calculation failed{context_text}: {error}"
            recovery = (
                "Recovery: ensure enough historical data and valid inputs before "
                "rerunning the strategy."
            )
        elif isinstance(error, ConfigurationError):
            message = f"Configuration error{context_text}: {error}"
            recovery = "Recovery: review the environment variables and defaults before rerunning."
        else:
            message = f"Unexpected exception{context_text}: {error}"
            recovery = "Recovery: inspect the logs and retry after fixing the underlying issue."

        full_message = f"{message} | {recovery}"
        if isinstance(error, Exception) and not isinstance(error, (MarketDataError, CalculationError, ConfigurationError)):
            self.logger.exception(full_message)
        else:
            self.logger.error(full_message)

        if shutdown:
            self.safe_shutdown(full_message)

        return {
            "status": "handled",
            "message": full_message,
            "recovery": recovery,
            "shutdown": shutdown,
            "error_type": error.__class__.__name__,
        }

    def safe_shutdown(self, reason):
        """Perform a safe shutdown without placing any trades or touching brokers."""
        self.logger.warning("Safe shutdown requested: %s", reason)
        if self.shutdown_hook is not None:
            self.shutdown_hook(reason)
        return {"shutdown": True, "reason": reason}
