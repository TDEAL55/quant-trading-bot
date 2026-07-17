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

    if isinstance(data.columns, pd.MultiIndex):
        flattened = []
        for column in data.columns:
            parts = [str(part) for part in column if str(part) and str(part) != ticker]
            flattened.append(parts[0] if parts else str(column[0]))
        data = data.copy()
        data.columns = flattened
    wanted_columns = [column for column in ["Open", "High", "Low", "Close", "Adj Close", "Volume"] if column in data.columns]
    cleaned = data[wanted_columns].copy()
    cleaned.columns = [str(column).lower().replace(" ", "_") for column in cleaned.columns]
    if "adj_close" in cleaned.columns and "close" not in cleaned.columns:
        cleaned["close"] = cleaned["adj_close"]
    if "close" not in cleaned.columns:
        raise MarketDataError(f"Ticker {ticker} did not return a close series")
    cleaned = cleaned.sort_index()
    return cleaned
