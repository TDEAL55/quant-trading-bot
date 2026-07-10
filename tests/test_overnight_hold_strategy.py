import pandas as pd
import pytest

from overnight_hold_strategy import EASTERN_TZ, OvernightConfig, run_overnight_hold_backtest


def _schedule_from_eastern(rows):
    index = []
    opens = []
    closes = []
    for day, open_hm, close_hm in rows:
        index.append(pd.Timestamp(day))
        open_ts = pd.Timestamp(f"{day} {open_hm}", tz=EASTERN_TZ).tz_convert("UTC")
        close_ts = pd.Timestamp(f"{day} {close_hm}", tz=EASTERN_TZ).tz_convert("UTC")
        opens.append(open_ts)
        closes.append(close_ts)

    return pd.DataFrame({"market_open": opens, "market_close": closes}, index=index)


def _intraday_loader_from_points(points, tz="America/New_York"):
    def _loader(symbol, start_date, end_date):
        idx = []
        vals = []
        for ts, price in points:
            idx.append(pd.Timestamp(ts, tz=tz))
            vals.append(float(price))
        return pd.DataFrame({"close": vals}, index=pd.DatetimeIndex(idx))

    return _loader


def _daily_loader_from_rows(rows):
    def _loader(symbol, start_date, end_date):
        idx = []
        opens = []
        closes = []
        for day, open_px, close_px in rows:
            idx.append(pd.Timestamp(day))
            opens.append(float(open_px))
            closes.append(float(close_px))
        return pd.DataFrame({"open": opens, "close": closes}, index=pd.DatetimeIndex(idx))

    return _loader


def test_uses_next_trading_day_not_calendar_day_weekend_and_holiday():
    schedule = _schedule_from_eastern(
        [
            ("2022-07-01", "09:30", "16:00"),
            ("2022-07-05", "09:30", "16:00"),
        ]
    )
    intraday_loader = _intraday_loader_from_points(
        [
            ("2022-07-01 15:58", 100),
            ("2022-07-05 09:32", 101),
        ]
    )
    daily_loader = _daily_loader_from_rows(
        [
            ("2022-07-01", 99, 100),
            ("2022-07-05", 101, 102),
        ]
    )

    result = run_overnight_hold_backtest(
        start_date="2022-07-01",
        end_date="2022-07-05",
        config=OvernightConfig(),
        calendar_provider=lambda start, end: schedule,
        intraday_loader=intraday_loader,
        daily_loader=daily_loader,
    )

    assert result["number_of_trades"] == 1
    assert result["trades"][0]["entry_date_time"].startswith("2022-07-01T15:58:00")
    assert result["trades"][0]["exit_date_time"].startswith("2022-07-05T09:32:00")


def test_early_close_uses_1258_entry():
    schedule = _schedule_from_eastern(
        [
            ("2019-07-03", "09:30", "13:00"),
            ("2019-07-05", "09:30", "16:00"),
        ]
    )
    intraday_loader = _intraday_loader_from_points(
        [
            ("2019-07-03 12:58", 100),
            ("2019-07-05 09:32", 99),
        ]
    )
    daily_loader = _daily_loader_from_rows(
        [
            ("2019-07-03", 100, 100),
            ("2019-07-05", 99, 99),
        ]
    )

    result = run_overnight_hold_backtest(
        start_date="2019-07-03",
        end_date="2019-07-05",
        config=OvernightConfig(),
        calendar_provider=lambda start, end: schedule,
        intraday_loader=intraday_loader,
        daily_loader=daily_loader,
    )

    assert result["number_of_trades"] == 1
    assert result["trades"][0]["entry_date_time"].startswith("2019-07-03T12:58:00")


def test_skips_incomplete_trade_when_exit_bar_missing():
    schedule = _schedule_from_eastern(
        [
            ("2023-01-03", "09:30", "16:00"),
            ("2023-01-04", "09:30", "16:00"),
        ]
    )
    intraday_loader = _intraday_loader_from_points(
        [
            ("2023-01-03 15:58", 100),
        ]
    )
    daily_loader = _daily_loader_from_rows(
        [
            ("2023-01-03", 100, 100),
            ("2023-01-04", 101, 101),
        ]
    )

    result = run_overnight_hold_backtest(
        start_date="2023-01-03",
        end_date="2023-01-04",
        config=OvernightConfig(),
        calendar_provider=lambda start, end: schedule,
        intraday_loader=intraday_loader,
        daily_loader=daily_loader,
    )

    assert result["number_of_trades"] == 0
    assert result["skipped_trades"] == 1


def test_timezone_conversion_from_utc_intraday_bars():
    schedule = _schedule_from_eastern(
        [
            ("2023-06-01", "09:30", "16:00"),
            ("2023-06-02", "09:30", "16:00"),
        ]
    )
    # 15:58 ET -> 19:58 UTC and 09:32 ET -> 13:32 UTC during EDT.
    intraday_loader = _intraday_loader_from_points(
        [
            ("2023-06-01 19:58", 100),
            ("2023-06-02 13:32", 101),
        ],
        tz="UTC",
    )
    daily_loader = _daily_loader_from_rows(
        [
            ("2023-06-01", 100, 100),
            ("2023-06-02", 101, 101),
        ]
    )

    result = run_overnight_hold_backtest(
        start_date="2023-06-01",
        end_date="2023-06-02",
        config=OvernightConfig(),
        calendar_provider=lambda start, end: schedule,
        intraday_loader=intraday_loader,
        daily_loader=daily_loader,
    )

    assert result["number_of_trades"] == 1


def test_transaction_costs_reduce_net_return():
    schedule = _schedule_from_eastern(
        [
            ("2023-01-03", "09:30", "16:00"),
            ("2023-01-04", "09:30", "16:00"),
        ]
    )
    intraday_loader = _intraday_loader_from_points(
        [
            ("2023-01-03 15:58", 100),
            ("2023-01-04 09:32", 110),
        ]
    )
    daily_loader = _daily_loader_from_rows(
        [
            ("2023-01-03", 100, 100),
            ("2023-01-04", 110, 110),
        ]
    )
    config = OvernightConfig(slippage_rate=0.001, transaction_cost_rate=0.001)

    result = run_overnight_hold_backtest(
        start_date="2023-01-03",
        end_date="2023-01-04",
        config=config,
        calendar_provider=lambda start, end: schedule,
        intraday_loader=intraday_loader,
        daily_loader=daily_loader,
    )

    trade = result["trades"][0]
    assert trade["gross_return"] == pytest.approx(0.1)
    assert trade["net_return"] < trade["gross_return"]
    assert trade["costs"] > 0


def test_reports_required_metrics_and_benchmark_comparisons():
    schedule = _schedule_from_eastern(
        [
            ("2023-01-03", "09:30", "16:00"),
            ("2023-01-04", "09:30", "16:00"),
            ("2023-01-05", "09:30", "16:00"),
        ]
    )
    intraday_loader = _intraday_loader_from_points(
        [
            ("2023-01-03 15:58", 100),
            ("2023-01-04 09:32", 102),
            ("2023-01-04 15:58", 100),
            ("2023-01-05 09:32", 98),
        ]
    )
    daily_loader = _daily_loader_from_rows(
        [
            ("2023-01-03", 99, 100),
            ("2023-01-04", 102, 100),
            ("2023-01-05", 98, 99),
        ]
    )

    result = run_overnight_hold_backtest(
        start_date="2023-01-03",
        end_date="2023-01-05",
        config=OvernightConfig(),
        calendar_provider=lambda start, end: schedule,
        intraday_loader=intraday_loader,
        daily_loader=daily_loader,
    )

    for key in [
        "total_return",
        "annualized_return",
        "win_rate",
        "average_trade",
        "worst_trade",
        "maximum_drawdown",
        "sharpe_ratio",
        "number_of_trades",
    ]:
        assert key in result

    assert "benchmark" in result
    assert "benchmark_comparison" in result
    assert "official_close_to_next_open_return" in result["benchmark"]
    assert "timed_358_to_932_return" in result["benchmark"]
    assert "buy_and_hold_return" in result["benchmark"]


def test_live_mode_is_blocked_for_research_backtest():
    schedule = _schedule_from_eastern(
        [
            ("2023-01-03", "09:30", "16:00"),
            ("2023-01-04", "09:30", "16:00"),
        ]
    )
    intraday_loader = _intraday_loader_from_points([("2023-01-03 15:58", 100), ("2023-01-04 09:32", 101)])
    daily_loader = _daily_loader_from_rows([("2023-01-03", 100, 100), ("2023-01-04", 101, 101)])

    with pytest.raises(RuntimeError, match="LIVE mode is blocked"):
        run_overnight_hold_backtest(
            start_date="2023-01-03",
            end_date="2023-01-04",
            config=OvernightConfig(),
            mode="LIVE",
            calendar_provider=lambda start, end: schedule,
            intraday_loader=intraday_loader,
            daily_loader=daily_loader,
        )