import pandas as pd
import yfinance as yf

from error_handler import MarketDataError


def download_price_data(ticker, start_date, end_date):
    """Download historical price data for a ticker between two dates."""
    try:
        data = yf.download(ticker, start=start_date, end=end_date, progress=False)
    except Exception as exc:
        raise MarketDataError(f"Unable to download data for {ticker}: {exc}") from exc

    if data is None or data.empty:
        raise MarketDataError(f"No data returned for ticker {ticker}")

    cleaned = data[["Close"]].copy()
    cleaned.columns = ["close"]
    cleaned = cleaned.sort_index()
    return cleaned
