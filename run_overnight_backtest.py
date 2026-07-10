from datetime import date

from overnight_hold_strategy import OvernightConfig, run_overnight_hold_backtest


def _print_section(title, result):
    print(f"symbol: {result['symbol']}")
    print(f"period: {result['start_date']} to {result['end_date']}")
    print(f"intraday data source: {result['data_source']['intraday']}")
    print(f"daily data source: {result['data_source']['daily']}")
    print(f"earliest timestamp received: {result['earliest_bar']}")
    print(f"latest timestamp received: {result['latest_bar']}")
    print(f"required earliest timestamp: {result['required_earliest']}")
    print(f"required latest timestamp: {result['required_latest']}")
    print(f"number of trades: {result['number_of_trades']}")
    print(f"skipped incomplete trades: {result['skipped_trades']}")
    print(f"total return: {result['total_return']:.4%}")
    print(f"annualized return: {result['annualized_return']:.4%}")
    print(f"win rate: {result['win_rate']:.2%}")
    print(f"average trade: {result['average_trade']:.4%}")
    print(f"worst trade: {result['worst_trade']:.4%}")
    print(f"maximum drawdown: {result['maximum_drawdown']:.4%}")
    print(f"sharpe ratio: {result['sharpe_ratio']:.4f}")
    print(f"benchmark official close->next open: {result['benchmark']['official_close_to_next_open_return']:.4%}")
    print(f"benchmark 3:58pm->9:32am: {result['benchmark']['timed_358_to_932_return']:.4%}")
    print(f"benchmark buy and hold: {result['benchmark']['buy_and_hold_return']:.4%}")
    print(f"vs buy and hold: {result['benchmark_comparison']['vs_buy_and_hold']:.4%}")


def main():
    config = OvernightConfig(
        symbol="SPY",
        entry_minutes_before_close=2,
        exit_minutes_after_open=2,
        slippage_rate=0.0005,
        transaction_cost_rate=0.0005,
    )

    periods = [
        ("2010-2019", "2010-01-01", "2019-12-31"),
        ("2020-2022", "2020-01-01", "2022-12-31"),
        ("2023-latest", "2023-01-01", date.today().isoformat()),
        ("full available", "2010-01-01", date.today().isoformat()),
    ]

    for label, start_date, end_date in periods:
        print(f"\n{label}")
        print("-" * len(label))
        try:
            result = run_overnight_hold_backtest(
                start_date=start_date,
                end_date=end_date,
                config=config,
                strict_data=True,
            )
            _print_section(label, result)
        except Exception as exc:
            print("number of complete overnight trades: 0")
            print("skipped incomplete trades: 0")
            print(f"FAILED: {exc}")


if __name__ == "__main__":
    main()