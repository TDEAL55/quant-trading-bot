from datetime import date
from datetime import datetime

import pandas as pd

import two_week_paper_runner


class MockClock:
    def __init__(self, is_open=True, timestamp=None):
        self.is_open = is_open
        self.timestamp = timestamp
        self.next_open = "2026-07-02T09:30:00-04:00"
        self.next_close = "2026-07-01T16:00:00-04:00"


class MockAccount:
    def __init__(self):
        self.status = "ACTIVE"
        self.cash = "1000"
        self.buying_power = "1000"
        self.portfolio_value = "1000"


class MockTradingClient:
    def __init__(self, api_key=None, secret_key=None, paper=None, is_open=True, clock_timestamp=None):
        self.is_open = is_open
        self.clock_timestamp = clock_timestamp

    def get_clock(self):
        return MockClock(is_open=self.is_open, timestamp=self.clock_timestamp)

    def get_account(self):
        return MockAccount()

    def get_all_positions(self):
        return []

    def get_orders(self):
        return []


class MockOrderManager:
    def __init__(self, mode=None, dry_run=None, submit_enabled=None, trading_client=None):
        self.submit_enabled = submit_enabled
        self.dry_run = dry_run

    def place_order(self, command=None, order_type=None):
        if self.submit_enabled and not self.dry_run:
            return {
                "approved": True,
                "reason": "submitted",
                "submitted": True,
                "status": "accepted",
                "order_id": "paper-order-1",
            }
        return {
            "approved": True,
            "reason": "approved",
            "submitted": False,
            "status": "simulated",
        }


def fake_prices(start="2026-01-01", periods=220):
    idx = pd.date_range(start=start, periods=periods, freq="D")
    return pd.DataFrame({"close": pd.Series(range(periods), index=idx, dtype=float)})


def fake_prices_with_last_close(last_close, start="2026-01-01", periods=220):
    frame = fake_prices(start=start, periods=periods)
    eval_day = pd.Timestamp("2026-07-01")
    eligible = frame.index[frame.index <= eval_day]
    target_index = eligible[-1] if len(eligible) else frame.index[-1]
    frame.loc[target_index, "close"] = float(last_close)
    return frame


class AlwaysFailRecorder:
    def __init__(self, print_fn=print):
        self.run_id = "run-fail"

    def ensure_schema(self):
        raise RuntimeError("db schema failed")

    def record_signal_snapshot(self, payload):
        raise RuntimeError("db signal failed")

    def record_account_snapshot(self, payload):
        raise RuntimeError("db account failed")

    def record_order_event(self, payload):
        raise RuntimeError("db order failed")

    def finalize_run(self, payload):
        raise RuntimeError("db finalize failed")


class CountingOrderManager(MockOrderManager):
    submit_call_count = 0
    place_order_call_count = 0

    def place_order(self, command=None, order_type=None):
        CountingOrderManager.place_order_call_count += 1
        result = super().place_order(command=command, order_type=order_type)
        if result.get("submitted"):
            CountingOrderManager.submit_call_count += 1
        return result


def test_database_failures_do_not_trigger_extra_orders(monkeypatch, tmp_path, capsys):
    CountingOrderManager.submit_call_count = 0
    CountingOrderManager.place_order_call_count = 0
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("ALPACA_API_KEY", "demo-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo-secret")

    result = two_week_paper_runner.run_two_week_paper_runner(
        start_day=date(2026, 7, 1),
        days=1,
        output_dir=tmp_path / "summaries",
        report_path=tmp_path / "report.md",
        dry_run=False,
        submit_enabled=True,
        trading_client_factory=lambda **kwargs: MockTradingClient(is_open=True, clock_timestamp=datetime(2026, 7, 1, 10, 0, 0)),
        market_data_loader=lambda *args, **kwargs: fake_prices_with_last_close(100.0),
        signal_generator=lambda *args, **kwargs: "buy",
        order_manager_factory=lambda **kwargs: CountingOrderManager(**kwargs),
        monitoring_recorder_factory=lambda print_fn=print: AlwaysFailRecorder(print_fn=print_fn),
    )

    output = capsys.readouterr().out
    assert result["days_processed"] == 1
    assert CountingOrderManager.place_order_call_count <= 1
    assert CountingOrderManager.submit_call_count <= 1
    assert "MONITORING_DB_WARNING" in output
