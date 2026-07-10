import math
import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

try:
    import pandas_market_calendars as mcal
except ImportError as exc:  # pragma: no cover - validated in runtime checks
    raise RuntimeError("pandas_market_calendars is required for overnight_hold_strategy") from exc


EASTERN_TZ = "America/New_York"
DEFAULT_CACHE_DIR = Path(__file__).resolve().parent / ".overnight_cache" / "alpaca_1m"


@dataclass(frozen=True)
class OvernightConfig:
    symbol: str = "SPY"
    entry_minutes_before_close: int = 2
    exit_minutes_after_open: int = 2
    slippage_rate: float = 0.0
    transaction_cost_rate: float = 0.0
    slippage_bps: float | None = None
    transaction_cost_bps: float | None = None
    initial_capital: float = 1.0
    intraday_feed: str = "sip"
    allow_iex: bool = False
    cache_dir: str = str(DEFAULT_CACHE_DIR)
    chunk_days: int = 7


def _validate_config(config):
    if config.symbol != "SPY":
        raise ValueError("overnight_hold_strategy supports SPY only")
    if config.initial_capital <= 0:
        raise ValueError("initial_capital must be greater than 0")
    if config.slippage_rate < 0 or config.transaction_cost_rate < 0:
        raise ValueError("slippage and transaction costs must be non-negative")
    if config.slippage_bps is not None and config.slippage_bps < 0:
        raise ValueError("slippage_bps must be non-negative")
    if config.transaction_cost_bps is not None and config.transaction_cost_bps < 0:
        raise ValueError("transaction_cost_bps must be non-negative")
    if config.slippage_bps is not None and config.slippage_rate != 0.0:
        raise ValueError("set either slippage_rate or slippage_bps, not both")
    if config.transaction_cost_bps is not None and config.transaction_cost_rate != 0.0:
        raise ValueError("set either transaction_cost_rate or transaction_cost_bps, not both")
    if int(config.chunk_days) < 1:
        raise ValueError("chunk_days must be >= 1")


def _resolve_cost_rates(config):
    # Rates are expressed as decimal percentages, e.g. 0.001 = 0.1%.
    slippage_rate = float(config.slippage_rate)
    transaction_cost_rate = float(config.transaction_cost_rate)

    if config.slippage_bps is not None:
        slippage_rate = float(config.slippage_bps) / 10000.0
    if config.transaction_cost_bps is not None:
        transaction_cost_rate = float(config.transaction_cost_bps) / 10000.0

    return slippage_rate, transaction_cost_rate


def _normalize_timestamp_index(dataframe, target_tz=EASTERN_TZ):
    if dataframe is None or dataframe.empty:
        return pd.DataFrame(columns=["close"])

    output = dataframe.copy()
    if "close" not in output.columns and "Close" in output.columns:
        output = output[["Close"]].rename(columns={"Close": "close"})
    elif "close" in output.columns:
        output = output[["close"]]
    else:
        raise ValueError("price dataframe must include a close column")

    index = pd.DatetimeIndex(output.index)
    if index.tz is None:
        index = index.tz_localize("UTC")
    output.index = index.tz_convert(target_tz)
    return output.sort_index()


def _to_utc_datetime(value):
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts.to_pydatetime()


def _cache_file(cache_dir, symbol, feed, chunk_start, chunk_end):
    cache_root = Path(cache_dir)
    cache_root.mkdir(parents=True, exist_ok=True)
    name = (
        f"{symbol}_{feed}_"
        f"{chunk_start.strftime('%Y%m%dT%H%M%S')}_"
        f"{chunk_end.strftime('%Y%m%dT%H%M%S')}.csv"
    )
    return cache_root / name


def _extract_bar_df(bar_set, symbol):
    df = getattr(bar_set, "df", None)
    if df is None or df.empty:
        return pd.DataFrame(columns=["close"])

    if isinstance(df.index, pd.MultiIndex):
        if "symbol" in df.index.names:
            df = df.xs(symbol, level="symbol")
        else:
            df = df.xs(symbol)

    df = df.copy()
    if "close" not in df.columns and "Close" in df.columns:
        df = df.rename(columns={"Close": "close"})
    if "close" not in df.columns:
        raise RuntimeError("Alpaca bar response did not include close prices")

    df = df[["close"]]
    index = pd.DatetimeIndex(df.index)
    if index.tz is None:
        index = index.tz_localize("UTC")
    else:
        index = index.tz_convert("UTC")
    df.index = index
    return df.sort_index()


def _load_cached_chunk(cache_path):
    if not cache_path.exists():
        return None
    try:
        cached = pd.read_csv(cache_path)
    except Exception:
        try:
            cache_path.unlink()
        except Exception:
            pass
        return None
    if cached.empty:
        try:
            cache_path.unlink()
        except Exception:
            pass
        return None
    if "timestamp" not in cached.columns or "close" not in cached.columns:
        try:
            cache_path.unlink()
        except Exception:
            pass
        return None
    cached["timestamp"] = pd.to_datetime(cached["timestamp"], utc=True)
    return pd.DataFrame({"close": cached["close"].astype(float)}, index=pd.DatetimeIndex(cached["timestamp"]))


def _save_cached_chunk(cache_path, dataframe):
    output = dataframe.copy()
    output = output.reset_index().rename(columns={"index": "timestamp"})
    output["timestamp"] = pd.to_datetime(output["timestamp"], utc=True)
    output.to_csv(cache_path, index=False)


def _is_sip_unauthorized(exc):
    text = str(exc).lower()
    patterns = [
        "sip",
        "unauthorized",
        "not authorized",
        "not entitled",
        "insufficient",
        "subscription",
        "forbidden",
        "403",
    ]
    return "sip" in text and any(token in text for token in patterns[1:])


def _resolve_feed(feed_name, allow_iex):
    normalized = str(feed_name or "sip").strip().lower()
    if normalized not in {"sip", "iex"}:
        raise ValueError("intraday feed must be 'sip' or 'iex'")
    if normalized == "iex" and not allow_iex:
        raise RuntimeError("IEX feed requires explicit opt-in via allow_iex setting")
    return normalized


def _alpaca_client_from_env(client_factory=StockHistoricalDataClient):
    api_key = os.getenv("ALPACA_API_KEY", "")
    api_secret = os.getenv("ALPACA_API_SECRET", "")
    if not api_key or not api_secret:
        raise RuntimeError("Missing required Alpaca credentials: ALPACA_API_KEY, ALPACA_API_SECRET")
    return client_factory(api_key=api_key, secret_key=api_secret)


def _download_alpaca_bars(
    symbol,
    start_date,
    end_date,
    feed,
    cache_dir,
    chunk_days,
    client_factory=StockHistoricalDataClient,
):
    client = _alpaca_client_from_env(client_factory=client_factory)

    start_utc = pd.Timestamp(start_date, tz="UTC")
    end_utc = pd.Timestamp(end_date, tz="UTC") + pd.Timedelta(days=1)
    current = start_utc
    chunks = []

    while current < end_utc:
        chunk_end = min(current + pd.Timedelta(days=chunk_days), end_utc)
        cache_path = _cache_file(cache_dir, symbol, feed, current, chunk_end)
        cached = _load_cached_chunk(cache_path)
        if cached is not None:
            chunks.append(cached)
            current = chunk_end
            continue

        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            start=_to_utc_datetime(current),
            end=_to_utc_datetime(chunk_end),
            timeframe=TimeFrame.Minute,
            feed=DataFeed(feed),
            limit=10000,
        )
        bar_set = client.get_stock_bars(request)
        frame = _extract_bar_df(bar_set, symbol)
        _save_cached_chunk(cache_path, frame)
        chunks.append(frame)
        current = chunk_end

    if not chunks:
        return pd.DataFrame(columns=["close"])

    combined = pd.concat(chunks).sort_index()
    if combined.empty:
        return combined
    combined = combined[~combined.index.duplicated(keep="last")]
    return combined


def load_intraday_prices(
    symbol,
    start_date,
    end_date,
    feed="sip",
    allow_iex=False,
    cache_dir=DEFAULT_CACHE_DIR,
    chunk_days=7,
    client_factory=StockHistoricalDataClient,
):
    primary_feed = _resolve_feed(feed, allow_iex)

    try:
        frame = _download_alpaca_bars(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            feed=primary_feed,
            cache_dir=cache_dir,
            chunk_days=chunk_days,
            client_factory=client_factory,
        )
        return frame, {
            "source": "alpaca",
            "feed_used": primary_feed,
            "iex_only": primary_feed == "iex",
        }
    except Exception as exc:
        if primary_feed == "sip" and _is_sip_unauthorized(exc):
            if not allow_iex:
                raise RuntimeError(
                    "SIP historical minute data access is unavailable for this account. "
                    "Enable IEX explicitly (allow_iex=true) to run IEX-only backtests."
                ) from exc

            frame = _download_alpaca_bars(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                feed="iex",
                cache_dir=cache_dir,
                chunk_days=chunk_days,
                client_factory=client_factory,
            )
            return frame, {
                "source": "alpaca",
                "feed_used": "iex",
                "iex_only": True,
                "note": "IEX-only results are not full-market results",
            }
        raise


def load_daily_prices(symbol, start_date, end_date):
    start_dt = pd.Timestamp(start_date)
    end_dt = pd.Timestamp(end_date) + timedelta(days=1)
    raw = yf.download(symbol, start=start_dt, end=end_dt, interval="1d", progress=False, auto_adjust=False)
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["open", "close"])
    daily = raw[["Open", "Close"]].copy()
    daily.columns = ["open", "close"]
    daily.index = pd.to_datetime(daily.index).tz_localize(None)
    return daily.sort_index()


def get_nyse_schedule(start_date, end_date, calendar_name="NYSE", calendar_provider=None):
    if calendar_provider is not None:
        schedule = calendar_provider(start_date, end_date)
    else:
        calendar = mcal.get_calendar(calendar_name)
        schedule = calendar.schedule(start_date=start_date, end_date=end_date)

    if schedule is None or schedule.empty:
        return pd.DataFrame(columns=["market_open", "market_close"])

    output = schedule[["market_open", "market_close"]].copy()
    output["market_open"] = pd.to_datetime(output["market_open"], utc=True).dt.tz_convert(EASTERN_TZ)
    output["market_close"] = pd.to_datetime(output["market_close"], utc=True).dt.tz_convert(EASTERN_TZ)
    return output


def latest_complete_trading_day(reference_date=None):
    today = pd.Timestamp(reference_date or date.today()).date()
    calendar = mcal.get_calendar("NYSE")
    end = today - timedelta(days=1)
    start = end - timedelta(days=14)
    schedule = calendar.schedule(start_date=start.isoformat(), end_date=end.isoformat())
    if schedule.empty:
        raise RuntimeError("Unable to determine latest complete trading day")
    return pd.Timestamp(schedule.index[-1]).date()


def find_earliest_available_alpaca_date(
    through_date,
    config,
    start_search_date="2010-01-01",
    intraday_loader=load_intraday_prices,
):
    start = pd.Timestamp(start_search_date).date()
    end = pd.Timestamp(through_date).date()
    cursor = start
    while cursor <= end:
        window_end = min(cursor + timedelta(days=13), end)
        payload = intraday_loader(
            config.symbol,
            cursor.isoformat(),
            window_end.isoformat(),
            feed=config.intraday_feed,
            allow_iex=config.allow_iex,
            cache_dir=config.cache_dir,
            chunk_days=config.chunk_days,
        )
        frame = payload[0] if isinstance(payload, tuple) else payload
        frame = _normalize_timestamp_index(frame)
        if not frame.empty:
            return frame.index.min().date()
        cursor = window_end + timedelta(days=1)
    raise RuntimeError("Unable to locate earliest available Alpaca 1-minute SPY bar in search range")


def _safe_sharpe(returns):
    if len(returns) < 2:
        return 0.0
    series = pd.Series(returns, dtype=float)
    std = float(series.std(ddof=1))
    if std == 0:
        return 0.0
    return float((series.mean() / std) * math.sqrt(252.0))


def _max_drawdown(equity_curve):
    if equity_curve.empty:
        return 0.0
    rolling_peak = equity_curve.cummax()
    drawdown = (equity_curve / rolling_peak) - 1.0
    return float(drawdown.min())


def _annualized_return(total_return, start_ts, end_ts):
    if start_ts is None or end_ts is None:
        return 0.0
    years = (end_ts - start_ts).total_seconds() / (365.25 * 24 * 3600)
    if years <= 0:
        return 0.0
    return float((1.0 + total_return) ** (1.0 / years) - 1.0)


def _price_at(minute_prices, timestamp):
    if timestamp in minute_prices.index:
        value = minute_prices.loc[timestamp, "close"]
        if pd.isna(value):
            return None
        return float(value)
    return None


def _compute_comparisons(schedule, daily_prices, minute_prices):
    official_returns = []
    schedule_rows = list(schedule.itertuples())
    daily_by_date = daily_prices.copy()
    daily_by_date["trade_date"] = daily_by_date.index.date
    daily_by_date = daily_by_date.set_index("trade_date")

    for idx in range(len(schedule_rows) - 1):
        current = schedule_rows[idx]
        nxt = schedule_rows[idx + 1]
        current_day = pd.Timestamp(current.Index).date()
        next_day = pd.Timestamp(nxt.Index).date()

        if current_day in daily_by_date.index and next_day in daily_by_date.index:
            close_px = float(daily_by_date.loc[current_day, "close"])
            open_px = float(daily_by_date.loc[next_day, "open"])
            if close_px > 0 and math.isfinite(close_px) and math.isfinite(open_px):
                official_returns.append((open_px / close_px) - 1.0)

    official_total = float((pd.Series(official_returns) + 1.0).prod() - 1.0) if official_returns else 0.0

    buy_hold_return = 0.0
    if not daily_prices.empty:
        first_open = float(daily_prices.iloc[0]["open"])
        last_close = float(daily_prices.iloc[-1]["close"])
        if first_open > 0:
            buy_hold_return = (last_close / first_open) - 1.0

    return {
        "official_close_to_next_open_return": official_total,
        "buy_and_hold_return": buy_hold_return,
    }


def _validate_trade_values(trade):
    for field in ("entry_price", "exit_price", "gross_return", "costs", "net_return"):
        value = float(trade[field])
        if not math.isfinite(value):
            raise RuntimeError(f"Invalid completed trade value: {field} is not finite")
    if trade["entry_price"] <= 0 or trade["exit_price"] <= 0:
        raise RuntimeError("Invalid completed trade value: entry/exit price must be positive")


def _assert_finite_metrics(result):
    for key in [
        "total_return",
        "annualized_return",
        "win_rate",
        "average_trade",
        "worst_trade",
        "maximum_drawdown",
        "sharpe_ratio",
    ]:
        if not math.isfinite(float(result[key])):
            raise RuntimeError(f"Invalid metric generated: {key} is not finite")


def run_overnight_hold_backtest(
    start_date,
    end_date,
    config=None,
    mode=None,
    calendar_provider=None,
    intraday_loader=load_intraday_prices,
    daily_loader=load_daily_prices,
    strict_data=False,
):
    """Backtest an overnight hold strategy using official NYSE open/close times."""
    selected_mode = (mode or os.getenv("TRADING_MODE", "SIMULATION")).upper()
    if selected_mode == "LIVE":
        raise RuntimeError("LIVE mode is blocked for overnight backtesting")

    strategy_config = config or OvernightConfig()
    _validate_config(strategy_config)
    slippage_rate, transaction_cost_rate = _resolve_cost_rates(strategy_config)

    schedule = get_nyse_schedule(start_date, end_date, calendar_provider=calendar_provider)
    if schedule.empty:
        raise RuntimeError("No official NYSE sessions found for the requested period")

    try:
        intraday_payload = intraday_loader(
            strategy_config.symbol,
            start_date,
            end_date,
            feed=strategy_config.intraday_feed,
            allow_iex=strategy_config.allow_iex,
            cache_dir=strategy_config.cache_dir,
            chunk_days=strategy_config.chunk_days,
        )
    except TypeError:
        intraday_payload = intraday_loader(strategy_config.symbol, start_date, end_date)
    if isinstance(intraday_payload, tuple):
        intraday_raw, intraday_meta = intraday_payload
    else:
        intraday_raw, intraday_meta = intraday_payload, {"source": "custom", "feed_used": "custom", "iex_only": False}

    intraday_prices = _normalize_timestamp_index(intraday_raw)
    daily_prices = daily_loader(strategy_config.symbol, start_date, end_date).sort_index()

    schedule_rows = list(schedule.itertuples())
    if len(schedule_rows) < 2:
        raise RuntimeError("Requested period has fewer than two trading sessions")

    first_required_entry = schedule_rows[0].market_close - pd.Timedelta(minutes=strategy_config.entry_minutes_before_close)
    last_required_exit = schedule_rows[-1].market_open + pd.Timedelta(minutes=strategy_config.exit_minutes_after_open)
    actual_earliest_bar = intraday_prices.index.min() if not intraday_prices.empty else None
    actual_latest_bar = intraday_prices.index.max() if not intraday_prices.empty else None

    if strict_data:
        if intraday_prices.empty:
            raise RuntimeError(
                "No 1-minute intraday bars returned for requested period "
                f"{start_date} to {end_date}. "
                f"expected_range=[{first_required_entry.isoformat()} to {last_required_exit.isoformat()}] "
                "received_range=[None to None]"
            )
        if actual_earliest_bar > first_required_entry or actual_latest_bar < last_required_exit:
            raise RuntimeError(
                "Requested 1-minute data coverage unavailable. "
                f"expected_range=[{first_required_entry.isoformat()} to {last_required_exit.isoformat()}] "
                f"received_range=[{actual_earliest_bar.isoformat()} to {actual_latest_bar.isoformat()}]"
            )

    trades = []
    skipped = 0
    missing_entry = 0
    missing_exit = 0
    equity = strategy_config.initial_capital
    equity_points = []

    for idx in range(len(schedule_rows) - 1):
        current = schedule_rows[idx]
        nxt = schedule_rows[idx + 1]

        entry_ts = current.market_close - pd.Timedelta(minutes=strategy_config.entry_minutes_before_close)
        exit_ts = nxt.market_open + pd.Timedelta(minutes=strategy_config.exit_minutes_after_open)

        entry_price = _price_at(intraday_prices, entry_ts)
        exit_price = _price_at(intraday_prices, exit_ts)
        missing = False
        if entry_price is None or entry_price <= 0 or not math.isfinite(entry_price):
            missing_entry += 1
            missing = True
        if exit_price is None or not math.isfinite(exit_price):
            missing_exit += 1
            missing = True
        if missing:
            skipped += 1
            continue

        gross_return = (exit_price / entry_price) - 1.0

        # Costs are applied once on entry and once on exit as decimal rates.
        buy_multiplier = 1.0 + slippage_rate + transaction_cost_rate
        sell_multiplier = 1.0 - slippage_rate - transaction_cost_rate
        effective_entry = entry_price * buy_multiplier
        effective_exit = exit_price * sell_multiplier
        net_return = (effective_exit / effective_entry) - 1.0
        costs = gross_return - net_return

        equity *= 1.0 + net_return
        equity_points.append((exit_ts, equity))

        trades.append(
            {
                "entry_date_time": entry_ts.isoformat(),
                "entry_price": entry_price,
                "exit_date_time": exit_ts.isoformat(),
                "exit_price": exit_price,
                "gross_return": gross_return,
                "costs": costs,
                "net_return": net_return,
            }
        )
        _validate_trade_values(trades[-1])

    trade_returns = [item["net_return"] for item in trades]
    total_return = float((pd.Series(trade_returns) + 1.0).prod() - 1.0) if trade_returns else 0.0
    win_rate = (sum(1 for value in trade_returns if value > 0) / len(trade_returns)) if trade_returns else 0.0
    average_trade = float(pd.Series(trade_returns).mean()) if trade_returns else 0.0
    worst_trade = float(pd.Series(trade_returns).min()) if trade_returns else 0.0

    if equity_points:
        equity_curve = pd.Series([val for _, val in equity_points], index=[ts for ts, _ in equity_points])
        max_dd = _max_drawdown(equity_curve)
        annualized = _annualized_return(total_return, equity_curve.index[0], equity_curve.index[-1])
    else:
        max_dd = 0.0
        annualized = 0.0

    comparisons = _compute_comparisons(schedule, daily_prices, intraday_prices)
    timed_result = total_return

    if strict_data and len(trades) == 0:
        raise RuntimeError(
            "No complete overnight trades could be formed from available 1-minute bars. "
            f"received_range=[{actual_earliest_bar.isoformat() if actual_earliest_bar is not None else 'None'} "
            f"to {actual_latest_bar.isoformat() if actual_latest_bar is not None else 'None'}]"
        )

    if not math.isclose(total_return, timed_result, rel_tol=1e-12, abs_tol=1e-12):
        raise RuntimeError("Strategy return mismatch: total_return must equal 3:58-to-9:32 result")

    output = {
        "symbol": strategy_config.symbol,
        "start_date": str(start_date),
        "end_date": str(end_date),
        "data_source": {
            "intraday": intraday_meta.get("source", "custom"),
            "daily": "yfinance",
            "feed_used": intraday_meta.get("feed_used", "custom"),
            "iex_only": bool(intraday_meta.get("iex_only", False)),
            "note": intraday_meta.get("note", ""),
        },
        "earliest_bar": actual_earliest_bar.isoformat() if actual_earliest_bar is not None else None,
        "latest_bar": actual_latest_bar.isoformat() if actual_latest_bar is not None else None,
        "required_earliest": first_required_entry.isoformat(),
        "required_latest": last_required_exit.isoformat(),
        "trades": trades,
        "skipped_trades": skipped,
        "missing_entry_trades": missing_entry,
        "missing_exit_trades": missing_exit,
        "complete_trades": len(trades),
        "number_of_trades": len(trades),
        "total_return": float(total_return),
        "annualized_return": float(annualized),
        "win_rate": float(win_rate),
        "average_trade": float(average_trade),
        "worst_trade": float(worst_trade),
        "maximum_drawdown": float(max_dd),
        "sharpe_ratio": float(_safe_sharpe(trade_returns)),
        "benchmark": {
            "official_close_to_next_open_return": comparisons["official_close_to_next_open_return"],
            "timed_358_to_932_return": timed_result,
            "buy_and_hold_return": comparisons["buy_and_hold_return"],
        },
        "timed_358_to_932_result": timed_result,
        "benchmark_comparison": {
            "vs_official_close_to_next_open": float(total_return - comparisons["official_close_to_next_open_return"]),
            "vs_timed_358_to_932": float(total_return - timed_result),
            "vs_buy_and_hold": float(total_return - comparisons["buy_and_hold_return"]),
        },
    }
    _assert_finite_metrics(output)
    return output
