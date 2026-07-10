import argparse
import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from overnight_hold_strategy import (
    OvernightConfig,
    latest_complete_trading_day,
    run_overnight_hold_backtest,
)


REPORT_PATH = Path(__file__).resolve().parent / "OVERNIGHT_BACKTEST_REPORT.md"


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
    estimated_total_costs = sum(trade.get("costs", 0.0) for trade in result.get("trades", []))
    strategy_beats_buy_hold = result["total_return"] > result["benchmark"]["buy_and_hold_return"]
    strategy_beats_official = result["total_return"] > result["benchmark"]["official_close_to_next_open_return"]

    print(f"total net return: {result['total_return']:.4%}")
    print(f"annualized return: {result['annualized_return']:.4%}")
    print(f"win rate: {result['win_rate']:.2%}")
    print(f"average net return per trade: {result['average_trade']:.4%}")
    print(f"worst trade: {result['worst_trade']:.4%}")
    print(f"maximum drawdown: {result['maximum_drawdown']:.4%}")
    print(f"Sharpe ratio: {result['sharpe_ratio']:.4f}")
    print(f"estimated total costs: {estimated_total_costs:.6f}")
    print(f"buy-and-hold return: {result['benchmark']['buy_and_hold_return']:.4%}")
    print(f"official close-to-next-open result: {result['benchmark']['official_close_to_next_open_return']:.4%}")
    print(f"3:58 PM-to-9:32 AM result: {result['benchmark']['timed_358_to_932_return']:.4%}")
    print(f"3:58 PM-to-9:32 AM strategy beat buy-and-hold: {strategy_beats_buy_hold}")
    print(f"3:58 PM-to-9:32 AM strategy beat official close-to-next-open: {strategy_beats_official}")


def _print_sample_trades(result, sample_count=5):
    print("sample trades:")
    trades = result.get("trades", [])[:sample_count]
    if not trades:
        print("(none)")
        return
    for idx, trade in enumerate(trades, start=1):
        print(
            f"{idx}. entry_price={trade['entry_price']:.6f} "
            f"exit_price={trade['exit_price']:.6f} "
            f"gross_return={trade['gross_return']:.6%} "
            f"costs={trade['costs']:.6%} "
            f"net_return={trade['net_return']:.6%}"
        )


def _report_section_lines(label, requested_start, requested_end, result):
    estimated_total_costs = sum(trade.get("costs", 0.0) for trade in result.get("trades", []))
    strategy_beats_buy_hold = result["total_return"] > result["benchmark"]["buy_and_hold_return"]
    strategy_beats_official = result["total_return"] > result["benchmark"]["official_close_to_next_open_return"]

    lines = [
        f"## {label}",
        "",
        f"- requested dates: {requested_start} to {requested_end}",
        f"- feed used: {result['data_source']['feed_used']}",
    ]
    if result["data_source"].get("iex_only"):
        lines.append("- IEX-only result: True (not full-market results)")

    lines.extend(
        [
            f"- actual earliest timestamp: {result['earliest_bar']}",
            f"- actual latest timestamp: {result['latest_bar']}",
            f"- complete trades: {result['complete_trades']}",
            f"- missing-entry trades: {result['missing_entry_trades']}",
            f"- missing-exit trades: {result['missing_exit_trades']}",
            f"- skipped trades: {result['skipped_trades']}",
            f"- total net return: {result['total_return']:.4%}",
            f"- annualized return: {result['annualized_return']:.4%}",
            f"- win rate: {result['win_rate']:.2%}",
            f"- average net return per trade: {result['average_trade']:.4%}",
            f"- worst trade: {result['worst_trade']:.4%}",
            f"- maximum drawdown: {result['maximum_drawdown']:.4%}",
            f"- Sharpe ratio: {result['sharpe_ratio']:.4f}",
            f"- estimated total costs: {estimated_total_costs:.6f}",
            f"- buy-and-hold return: {result['benchmark']['buy_and_hold_return']:.4%}",
            f"- official-close-to-next-open result: {result['benchmark']['official_close_to_next_open_return']:.4%}",
            f"- 3:58 PM-to-9:32 AM result: {result['benchmark']['timed_358_to_932_return']:.4%}",
            f"- 3:58 PM-to-9:32 AM strategy beat buy-and-hold: {strategy_beats_buy_hold}",
            f"- 3:58 PM-to-9:32 AM strategy beat official close-to-next-open: {strategy_beats_official}",
            "",
        ]
    )
    return lines


def _run_period(label, start_date, end_date, config):
    try:
        result = run_overnight_hold_backtest(
            start_date=start_date,
            end_date=end_date,
            config=config,
            strict_data=True,
        )
        _print_result_block(label, start_date, end_date, result)
        return {
            "label": label,
            "requested_start": start_date,
            "requested_end": end_date,
            "result": result,
            "error": None,
        }
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
        return {
            "label": label,
            "requested_start": start_date,
            "requested_end": end_date,
            "result": None,
            "error": str(exc),
        }


def _probe_periods(config):
    probe_one_start = "2020-01-06"
    probe_one_end = "2020-01-10"

    latest_complete_day = latest_complete_trading_day()
    recent_friday = _last_complete_trading_week_end(latest_complete_day)
    recent_monday = recent_friday - timedelta(days=4)

    outcomes = []
    outcomes.append(_run_period("probe: 2020 trading week", probe_one_start, probe_one_end, config))
    outcomes.append(
        _run_period(
        "probe: recent complete trading week",
        recent_monday.isoformat(),
        recent_friday.isoformat(),
        config,
        )
    )
    return outcomes


def _single_month_2023_period(config):
    return _run_period("validation: completed month 2023-06", "2023-06-01", "2023-06-30", config)


def _multiyear_periods(config):
    latest_complete_day = latest_complete_trading_day()

    cache_root = Path(config.cache_dir)
    earliest_available = None
    if cache_root.exists():
        for file_path in cache_root.glob("*.csv"):
            try:
                frame = pd.read_csv(file_path, usecols=["timestamp"])
                if frame.empty:
                    continue
                ts = pd.to_datetime(frame["timestamp"], utc=True).min().tz_convert("America/New_York")
                day = ts.date()
                if earliest_available is None or day < earliest_available:
                    earliest_available = day
            except Exception:
                continue

    if earliest_available is None:
        earliest_available = date(2020, 1, 1)

    periods = []
    if earliest_available <= date(2019, 12, 31):
        periods.append(
            (
                "earliest available through 2019",
                earliest_available.isoformat(),
                "2019-12-31",
            )
        )
    else:
        periods.append(
            (
                "earliest available through 2019",
                earliest_available.isoformat(),
                "2019-12-31",
                "unsupported: earliest cached Alpaca minute data starts after 2019",
            )
        )

    periods.append(("2020-2022", "2020-01-01", "2022-12-31"))
    periods.append(("2023-latest complete", "2023-01-01", latest_complete_day.isoformat()))
    periods.append(
        (
            "full available Alpaca period",
            earliest_available.isoformat(),
            latest_complete_day.isoformat(),
        )
    )

    outcomes = []
    for period in periods:
        if len(period) == 4:
            label, start_date, end_date, reason = period
            print(f"\n{label}")
            print("-" * len(label))
            print(f"requested period: {start_date} to {end_date}")
            print(f"feed used: {config.intraday_feed}")
            print("earliest received timestamp: None")
            print("latest received timestamp: None")
            print("complete trades: 0")
            print("missing-entry trades: 0")
            print("missing-exit trades: 0")
            print("skipped trades: 0")
            print(f"FAILED: {reason}")
            outcomes.append(
                {
                    "label": label,
                    "requested_start": start_date,
                    "requested_end": end_date,
                    "result": None,
                    "error": reason,
                }
            )
            continue

        label, start_date, end_date = period
        outcomes.append(_run_period(label, start_date, end_date, config))
    return outcomes


def _write_report(probe_outcomes, multiyear_outcomes):
    lines = [
        "# OVERNIGHT_BACKTEST_REPORT",
        "",
        "Research-only overnight SPY backtest using Alpaca minute data.",
        "",
        "## Probe Periods",
        "",
    ]

    for outcome in probe_outcomes:
        if outcome["error"] is not None:
            lines.extend(
                [
                    f"### {outcome['label']}",
                    "",
                    f"- requested dates: {outcome['requested_start']} to {outcome['requested_end']}",
                    f"- failed: {outcome['error']}",
                    "",
                ]
            )
            continue
        lines.extend(_report_section_lines(outcome["label"], outcome["requested_start"], outcome["requested_end"], outcome["result"]))

    lines.extend(["## Multiyear Periods", ""])
    for outcome in multiyear_outcomes:
        if outcome["error"] is not None:
            lines.extend(
                [
                    f"### {outcome['label']}",
                    "",
                    f"- requested dates: {outcome['requested_start']} to {outcome['requested_end']}",
                    f"- failed: {outcome['error']}",
                    "",
                ]
            )
            continue
        lines.extend(_report_section_lines(outcome["label"], outcome["requested_start"], outcome["requested_end"], outcome["result"]))

    _write_report_atomic(REPORT_PATH, "\n".join(lines))


def _write_report_atomic(path, content):
    temp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        temp_path.write_text(content, encoding="utf-8")
        temp_path.replace(path)
    except Exception:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except Exception:
            pass
        raise


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
        intraday_feed="sip",
        allow_iex=False,
        cache_dir=os.getenv("OVERNIGHT_CACHE_DIR", OvernightConfig().cache_dir),
        chunk_days=int(os.getenv("OVERNIGHT_CHUNK_DAYS", "3")),
    )

    month_outcome = _single_month_2023_period(config)
    if month_outcome["result"] is not None:
        _print_sample_trades(month_outcome["result"], sample_count=5)

    if not args.run_multiyear:
        print("\nSingle-month validation completed. Multiyear run not started.")
        return

    probe_outcomes = _probe_periods(config)

    multiyear_outcomes = _multiyear_periods(config)
    _write_report(probe_outcomes, multiyear_outcomes)


if __name__ == "__main__":
    main()
