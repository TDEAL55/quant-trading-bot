import pandas as pd
import yfinance as yf
from contextlib import redirect_stderr
from io import StringIO

from error_handler import MarketDataError


def _chunked(values, size):
    chunk_size = max(int(size), 1)
    for index in range(0, len(values), chunk_size):
        yield values[index : index + chunk_size]


def download_price_data(ticker, start_date, end_date, timeout_seconds=20):
    """Download historical price data for a ticker between two dates."""
    try:
        # yfinance can emit verbose warnings for unavailable tickers; keep scanner output concise.
        with redirect_stderr(StringIO()):
            data = yf.download(
                ticker,
                start=start_date,
                end=end_date,
                progress=False,
                timeout=max(float(timeout_seconds), 1.0),
            )
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


def download_price_data_batch(tickers, start_date, end_date, timeout_seconds=20, chunk_size=50):
    """Download historical price data for multiple tickers in one request when possible."""
    symbols = [str(ticker).upper().strip() for ticker in list(tickers or []) if str(ticker).strip()]
    if not symbols:
        return {}

    unique_symbols = list(dict.fromkeys(symbols))
    result = {}
    timeout = max(float(timeout_seconds), 1.0)

    for symbol_chunk in _chunked(unique_symbols, chunk_size):
        joined = " ".join(symbol_chunk)
        try:
            with redirect_stderr(StringIO()):
                raw = yf.download(
                    joined,
                    start=start_date,
                    end=end_date,
                    progress=False,
                    group_by="ticker",
                    threads=False,
                    timeout=timeout,
                )
        except Exception as exc:
            raise MarketDataError(f"Unable to download batch data for {len(symbol_chunk)} tickers: {exc}") from exc

        if raw is None or raw.empty:
            continue

        if isinstance(raw.columns, pd.MultiIndex):
            for symbol in symbol_chunk:
                if symbol not in raw.columns.get_level_values(0):
                    continue
                frame = raw[symbol].copy()
                if frame.empty:
                    continue
                frame.columns = [str(column).lower().replace(" ", "_") for column in frame.columns]
                if "adj_close" in frame.columns and "close" not in frame.columns:
                    frame["close"] = frame["adj_close"]
                if "close" not in frame.columns:
                    continue
                result[symbol] = frame.sort_index()
            continue

        # yfinance may return a flat frame when only one symbol has data.
        single_symbol = symbol_chunk[0]
        frame = raw.copy()
        if isinstance(frame.columns, pd.MultiIndex):
            flattened = []
            for column in frame.columns:
                parts = [str(part) for part in column if str(part) and str(part) != single_symbol]
                flattened.append(parts[0] if parts else str(column[0]))
            frame.columns = flattened
        frame.columns = [str(column).lower().replace(" ", "_") for column in frame.columns]
        if "adj_close" in frame.columns and "close" not in frame.columns:
            frame["close"] = frame["adj_close"]
        if "close" in frame.columns and not frame.empty:
            result[single_symbol] = frame.sort_index()

    return result
