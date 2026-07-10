import argparse
import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from overnight_hold_strategy import (
    OvernightConfig,
    find_earliest_available_alpaca_date,
    get_nyse_schedule,
    latest_complete_trading_day,
    run_overnight_hold_backtest,
)


def _last_complete_trading_week_end(latest_day):
    day = pd.Timestamp(latest_day).date()
    while day.weekday() != 4:
        day -= timedelta(days=1)
    return day


def _print_result_block(label, requested_start, requested_end, result):
    print(f"\n{label}")
    print("-" * len(label))
    print(f"requested period: {requested_start} to {requested_end}")
    print(f"feed used: {result['data_source']['feed_used']}")
    if result["data_source"].get("iex_only"):
        print("IEX-only result: True (not full-market results)")
    print(f"earliest received timestamp: {result['earliest_bar']}")
    print(f"latest received timestamp: {result['latest_bar']}")
    print(f"complete trades: {result['complete_trades']}")
    print(f"missing-entry trades: {result['missing_entry_trades']}")
    print(f"missing-exit trades: {result['missing_exit_trades']}")
    print(f"skipped trades: {result['skipped_trades']}")


def _run_period(label, start_date, end_date, config):
    try:
        result = run_overnight_hold_backtest(
            start_date=start_date,
            end_date=end_date,
            config=config,
            strict_data=True,
        )
        _print_result_block(label, start_date, end_date, result)
    except Exception as exc:
        print(f"\n{label}")
        print("-" * len(label))
        print(f"requested period: {start_date} to {end_date}")
        print(f"feed used: {config.intraday_feed}")
        if config.intraday_feed.lower() == "iex" and config.allow_iex:
            print("IEX-only result: True (not full-market results)")
        print("earliest received timestamp: None")
        print("latest received timestamp: None")
        print("complete trades: 0")
        print("missing-entry trades: 0")
        print("missing-exit trades: 0")
        print("skipped trades: 0")
        print(f"FAILED: {exc}")


def _probe_periods(config):
    probe_one_start = "2020-01-06"
    probe_one_end = "2020-01-10"

    latest_complete_day = latest_complete_trading_day()
    recent_friday = _last_complete_trading_week_end(latest_complete_day)
    recent_monday = recent_friday - timedelta(days=4)

    _run_period("probe: 2020 trading week", probe_one_start, probe_one_end, config)
    _run_period(
        "probe: recent complete trading week",
        recent_monday.isoformat(),
        recent_friday.isoformat(),
        config,
    )


def _multiyear_periods(config):
    latest_complete_day = latest_complete_trading_day()
    earliest_available = find_earliest_available_alpaca_date(through_date="2019-12-31", config=config)

    periods = [
        (
            "earliest available through 2019",
            earliest_available.isoformat(),
            "2019-12-31",
        ),
        ("2020-2022", "2020-01-01", "2022-12-31"),
        ("2023-latest complete", "2023-01-01", latest_complete_day.isoformat()),
        (
            "full available Alpaca period",
            earliest_available.isoformat(),
            latest_complete_day.isoformat(),
        ),
    ]

    for label, start_date, end_date in periods:
        _run_period(label, start_date, end_date, config)


def main():
    dotenv_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(dotenv_path=dotenv_path, override=False)

    parser = argparse.ArgumentParser(description="Run overnight SPY backtest with Alpaca minute bars")
    parser.add_argument(
        "--run-multiyear",
        action="store_true",
        help="Run full multiyear periods after probes.",
    )
    args = parser.parse_args()

    config = OvernightConfig(
        symbol="SPY",
        entry_minutes_before_close=2,
        exit_minutes_after_open=2,
        slippage_rate=0.0005,
        transaction_cost_rate=0.0005,
        intraday_feed=os.getenv("OVERNIGHT_DATA_FEED", "sip"),
        allow_iex=os.getenv("OVERNIGHT_ALLOW_IEX", "false").strip().lower() in {"1", "true", "yes"},
        cache_dir=os.getenv("OVERNIGHT_CACHE_DIR", OvernightConfig().cache_dir),
        chunk_days=int(os.getenv("OVERNIGHT_CHUNK_DAYS", str(OvernightConfig().chunk_days))),
    )

    _probe_periods(config)

    if not args.run_multiyear:
        print("\nProbe run completed. Re-run with --run-multiyear to execute full period backtests.")
        return

    _multiyear_periods(config)


if __name__ == "__main__":
    main()
