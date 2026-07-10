import math
import os
from dataclasses import dataclass
from datetime import timedelta

import pandas as pd
import yfinance as yf

try:
    import pandas_market_calendars as mcal
except ImportError as exc:  # pragma: no cover - validated in runtime checks
    raise RuntimeError("pandas_market_calendars is required for overnight_hold_strategy") from exc


EASTERN_TZ = "America/New_York"


@dataclass(frozen=True)
class OvernightConfig:
    symbol: str = "SPY"
    entry_minutes_before_close: int = 2
    exit_minutes_after_open: int = 2
    slippage_rate: float = 0.0
    transaction_cost_rate: float = 0.0
    initial_capital: float = 1.0


def _validate_config(config):
    if config.symbol != "SPY":
        raise ValueError("overnight_hold_strategy supports SPY only")
    if config.initial_capital <= 0:
        raise ValueError("initial_capital must be greater than 0")
    if config.slippage_rate < 0 or config.transaction_cost_rate < 0:
        raise ValueError("slippage and transaction costs must be non-negative")


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


def load_intraday_prices(symbol, start_date, end_date):
    start_dt = pd.Timestamp(start_date)
    end_dt = pd.Timestamp(end_date) + timedelta(days=1)
    raw = yf.download(symbol, start=start_dt, end=end_dt, interval="1m", progress=False, prepost=True)
    return _normalize_timestamp_index(raw)


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
        return float(value)
    return None


def _daily_date(ts):
    return pd.Timestamp(ts).tz_convert(EASTERN_TZ).date()


def _compute_comparisons(schedule, daily_prices, minute_prices):
    official_returns = []
    timed_returns = []
    schedule_rows = list(schedule.itertuples())

    for idx in range(len(schedule_rows) - 1):
        current = schedule_rows[idx]
        nxt = schedule_rows[idx + 1]
        current_day = pd.Timestamp(current.Index).date()
        next_day = pd.Timestamp(nxt.Index).date()

        if current_day in daily_prices.index and next_day in daily_prices.index:
            close_px = float(daily_prices.loc[current_day, "close"])
            open_px = float(daily_prices.loc[next_day, "open"])
            if close_px > 0:
                official_returns.append((open_px / close_px) - 1.0)

        entry_ts = current.market_close - pd.Timedelta(minutes=2)
        exit_ts = nxt.market_open + pd.Timedelta(minutes=2)
        entry_price = _price_at(minute_prices, entry_ts)
        exit_price = _price_at(minute_prices, exit_ts)
        if entry_price is not None and exit_price is not None and entry_price > 0:
            timed_returns.append((exit_price / entry_price) - 1.0)

    official_total = float((pd.Series(official_returns) + 1.0).prod() - 1.0) if official_returns else 0.0
    timed_total = float((pd.Series(timed_returns) + 1.0).prod() - 1.0) if timed_returns else 0.0

    buy_hold_return = 0.0
    if not daily_prices.empty:
        first_open = float(daily_prices.iloc[0]["open"])
        last_close = float(daily_prices.iloc[-1]["close"])
        if first_open > 0:
            buy_hold_return = (last_close / first_open) - 1.0

    return {
        "official_close_to_next_open_return": official_total,
        "timed_358_to_932_return": timed_total,
        "buy_and_hold_return": buy_hold_return,
    }


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

    schedule = get_nyse_schedule(start_date, end_date, calendar_provider=calendar_provider)
    if schedule.empty:
        raise RuntimeError("No official NYSE sessions found for the requested period")

    intraday_prices = _normalize_timestamp_index(intraday_loader(strategy_config.symbol, start_date, end_date))
    daily_prices = daily_loader(strategy_config.symbol, start_date, end_date)
    daily_prices = daily_prices.sort_index()

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
    equity = strategy_config.initial_capital
    equity_points = []

    for idx in range(len(schedule_rows) - 1):
        current = schedule_rows[idx]
        nxt = schedule_rows[idx + 1]

        entry_ts = current.market_close - pd.Timedelta(minutes=strategy_config.entry_minutes_before_close)
        exit_ts = nxt.market_open + pd.Timedelta(minutes=strategy_config.exit_minutes_after_open)

        entry_price = _price_at(intraday_prices, entry_ts)
        exit_price = _price_at(intraday_prices, exit_ts)
        if entry_price is None or exit_price is None or entry_price <= 0:
            skipped += 1
            continue

        gross_return = (exit_price / entry_price) - 1.0

        buy_multiplier = 1.0 + strategy_config.slippage_rate + strategy_config.transaction_cost_rate
        sell_multiplier = 1.0 - strategy_config.slippage_rate - strategy_config.transaction_cost_rate
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

    trade_returns = [item["net_return"] for item in trades]
    total_return = (equity / strategy_config.initial_capital) - 1.0
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

    if strict_data and len(trades) == 0:
        raise RuntimeError(
            "No complete overnight trades could be formed from available 1-minute bars. "
            f"received_range=[{actual_earliest_bar.isoformat() if actual_earliest_bar is not None else 'None'} "
            f"to {actual_latest_bar.isoformat() if actual_latest_bar is not None else 'None'}]"
        )

    return {
        "symbol": strategy_config.symbol,
        "start_date": str(start_date),
        "end_date": str(end_date),
        "data_source": {
            "intraday": "yfinance",
            "daily": "yfinance",
        },
        "earliest_bar": actual_earliest_bar.isoformat() if actual_earliest_bar is not None else None,
        "latest_bar": actual_latest_bar.isoformat() if actual_latest_bar is not None else None,
        "required_earliest": first_required_entry.isoformat(),
        "required_latest": last_required_exit.isoformat(),
        "trades": trades,
        "skipped_trades": skipped,
        "number_of_trades": len(trades),
        "total_return": float(total_return),
        "annualized_return": float(annualized),
        "win_rate": float(win_rate),
        "average_trade": float(average_trade),
        "worst_trade": float(worst_trade),
        "maximum_drawdown": float(max_dd),
        "sharpe_ratio": float(_safe_sharpe(trade_returns)),
        "benchmark": comparisons,
        "benchmark_comparison": {
            "vs_official_close_to_next_open": float(total_return - comparisons["official_close_to_next_open_return"]),
            "vs_timed_358_to_932": float(total_return - comparisons["timed_358_to_932_return"]),
            "vs_buy_and_hold": float(total_return - comparisons["buy_and_hold_return"]),
        },
    }