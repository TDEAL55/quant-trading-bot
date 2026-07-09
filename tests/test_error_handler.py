import logging

import pandas as pd
import pytest

import main
from error_handler import (
    CalculationError,
    ConfigurationError,
    ErrorHandler,
    MarketDataError,
)
from market_data import download_price_data
from strategy import generate_signal


class DummyHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def test_error_handler_logs_and_returns_recovery_message():
    log_handler = DummyHandler()
    logger = logging.getLogger("test_error_handler")
    logger.handlers = [log_handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    error_handler = ErrorHandler(logger=logger)
    result = error_handler.handle_error(
        MarketDataError("missing market data"),
        context="downloading prices",
        shutdown=False,
    )

    assert result["status"] == "handled"
    assert "recovery" in result["message"].lower()
    assert log_handler.records


def test_error_handler_safe_shutdown_invokes_hook():
    shutdown_calls = []

    def hook(reason):
        shutdown_calls.append(reason)

    error_handler = ErrorHandler(shutdown_hook=hook)
    result = error_handler.safe_shutdown("simulated shutdown")

    assert result["shutdown"] is True
    assert shutdown_calls == ["simulated shutdown"]


def test_download_price_data_raises_market_data_error_for_empty_payload(monkeypatch):
    monkeypatch.setattr("market_data.yf.download", lambda *args, **kwargs: pd.DataFrame())

    with pytest.raises(MarketDataError):
        download_price_data("SPY", "2020-01-01", "2025-01-01")


def test_generate_signal_raises_calculation_error_for_short_series():
    with pytest.raises(CalculationError):
        generate_signal(pd.Series([1.0, 2.0]))


def test_main_run_analysis_returns_error_summary_for_market_data_failure(monkeypatch):
    def fail_download(*args, **kwargs):
        raise MarketDataError("missing market data")

    monkeypatch.setattr(main, "download_price_data", fail_download)

    result = main.run_analysis()

    assert result["status"] == "error"
    assert "market data" in result["message"].lower()
