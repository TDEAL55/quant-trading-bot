from datetime import date
from datetime import datetime
import os
import json

import pandas as pd
import pytest

import two_week_paper_runner


@pytest.fixture(autouse=True)
def _isolate_daily_state_file(monkeypatch, tmp_path):
    monkeypatch.setenv("PAPER_DAILY_STATE_PATH", str(tmp_path / "daily_state.json"))


class MockClock:
    def __init__(self, is_open=True, timestamp=None, next_open=None, next_close=None):
        self.is_open = is_open
        self.timestamp = timestamp
        self.next_open = next_open
        self.next_close = next_close


class MockAccount:
    def __init__(self, status="ACTIVE", cash="1000", buying_power="1000", portfolio_value="1000"):
        self.status = status
        self.cash = cash
        self.buying_power = buying_power
        self.portfolio_value = portfolio_value


class MockPosition:
    def __init__(self, symbol="SPY", qty="0"):
        self.symbol = symbol
        self.qty = qty


class MockTradingClient:
    def __init__(
        self,
        api_key=None,
        secret_key=None,
        paper=None,
        is_open=True,
        portfolio_values=None,
        clock_timestamp=None,
        open_orders=None,
    ):
        self.api_key = api_key
        self.secret_key = secret_key
        self.paper = paper
        self.is_open = is_open
        self.portfolio_values = portfolio_values or [1000.0] * 20
        self.account_calls = 0
        self.clock_timestamp = clock_timestamp
        self.open_orders = open_orders or []
        self.submit_calls = 0

    def get_clock(self):
        return MockClock(
            is_open=self.is_open,
            timestamp=self.clock_timestamp,
            next_open="2026-07-02T09:30:00-04:00",
            next_close="2026-07-01T16:00:00-04:00",
        )

    def get_account(self):
        idx = min(self.account_calls, len(self.portfolio_values) - 1)
        value = self.portfolio_values[idx]
        self.account_calls += 1
        return MockAccount(portfolio_value=str(value), cash="1000", buying_power="1000")

    def get_all_positions(self):
        return [MockPosition()]

    def get_orders(self):
        return self.open_orders

    def submit_order(self, *args, **kwargs):
        self.submit_calls += 1
        raise AssertionError("submit_order should not be called in runner tests")


class MockOrderManager:
    _counter = 0

    def __init__(self, mode=None, dry_run=None, submit_enabled=None, trading_client=None):
        self.mode = mode
        self.dry_run = dry_run
        self.submit_enabled = submit_enabled
        self.trading_client = trading_client

    def place_order(self, command=None, order_type=None):
        if self.submit_enabled and not self.dry_run:
            MockOrderManager._counter += 1
            return {
                "approved": True,
                "reason": "submitted",
                "submitted": True,
                "status": "accepted",
                "order_id": f"paper-order-{MockOrderManager._counter}",
                "simulated_order": {
                    "symbol": "SPY",
                    "notional": 10.0,
                    "type": "market",
                },
            }
        return {
            "approved": True,
            "reason": "approved",
            "submitted": False,
            "simulated_order": {
                "symbol": "SPY",
                "notional": 10.0,
                "type": "market",
            },
        }


def fake_prices(start="2025-01-01", periods=220):
    idx = pd.date_range(start=start, periods=periods, freq="D")
    return pd.DataFrame({"close": pd.Series(range(periods), index=idx, dtype=float)})


def fake_prices_with_last_close(last_close, start="2025-01-01", periods=220):
    frame = fake_prices(start=start, periods=periods)
    frame.iloc[-1, frame.columns.get_loc("close")] = float(last_close)
    return frame


def test_live_mode_stops_immediately(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "LIVE")
    monkeypatch.setenv("ALPACA_API_KEY", "demo-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo-secret")

    with pytest.raises(RuntimeError, match="LIVE mode detected"):
        two_week_paper_runner.run_two_week_paper_runner()


def test_missing_credentials_stops_safely(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)

    isolated_env = tmp_path / ".env"
    isolated_env.write_text("TRADING_MODE=PAPER\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Missing required Alpaca credentials"):
        two_week_paper_runner.run_two_week_paper_runner(env_path=isolated_env)


def test_market_closed_skips_all_days(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("ALPACA_API_KEY", "demo-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo-secret")

    output_dir = tmp_path / "daily_summaries"
    report_path = tmp_path / "TWO_WEEK_REPORT.md"
    result = two_week_paper_runner.run_two_week_paper_runner(
        start_day=date(2026, 7, 1),
        output_dir=output_dir,
        report_path=report_path,
        trading_client_factory=lambda **kwargs: MockTradingClient(is_open=False),
        market_data_loader=lambda *args, **kwargs: fake_prices(),
        signal_generator=lambda *args, **kwargs: "buy",
        order_manager_factory=lambda **kwargs: MockOrderManager(**kwargs),
    )

    assert result["days_processed"] == 14
    assert report_path.exists()
    assert len(list(output_dir.glob("*.md"))) == 14
    assert "review required: False" in report_path.read_text(encoding="utf-8")


def test_daily_loss_limit_sets_review_required(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("ALPACA_API_KEY", "demo-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo-secret")
    monkeypatch.delenv("REVIEW_REQUIRED", raising=False)

    output_dir = tmp_path / "daily_summaries"
    report_path = tmp_path / "TWO_WEEK_REPORT.md"
    values = [1000.0, 970.0] + [970.0] * 20
    result = two_week_paper_runner.run_two_week_paper_runner(
        start_day=date(2026, 7, 1),
        output_dir=output_dir,
        report_path=report_path,
        trading_client_factory=lambda **kwargs: MockTradingClient(is_open=True, portfolio_values=values),
        market_data_loader=lambda *args, **kwargs: fake_prices(),
        signal_generator=lambda *args, **kwargs: "hold",
        order_manager_factory=lambda **kwargs: MockOrderManager(**kwargs),
    )

    assert result["review_required"] is True
    assert result["stop_reason"] == "daily loss limit hit"
    assert os.environ.get("REVIEW_REQUIRED") == "true"
    report_text = report_path.read_text(encoding="utf-8")
    assert "review required: True" in report_text


def test_data_missing_skips_safely(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("ALPACA_API_KEY", "demo-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo-secret")

    output_dir = tmp_path / "daily_summaries"
    report_path = tmp_path / "TWO_WEEK_REPORT.md"
    result = two_week_paper_runner.run_two_week_paper_runner(
        start_day=date(2026, 7, 1),
        output_dir=output_dir,
        report_path=report_path,
        trading_client_factory=lambda **kwargs: MockTradingClient(is_open=True),
        market_data_loader=lambda *args, **kwargs: pd.DataFrame({"close": []}),
        signal_generator=lambda *args, **kwargs: "buy",
        order_manager_factory=lambda **kwargs: MockOrderManager(**kwargs),
    )

    assert result["days_processed"] == 14
    sample_summary = sorted(output_dir.glob("*.md"))[0].read_text(encoding="utf-8")
    assert "reason: data missing" in sample_summary


def test_buy_signal_uses_existing_strategy_and_submits_at_most_one_per_day(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("ALPACA_API_KEY", "demo-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo-secret")

    output_dir = tmp_path / "daily_summaries"
    report_path = tmp_path / "TWO_WEEK_REPORT.md"
    calls = {"signals": 0}

    def signal_generator(*args, **kwargs):
        calls["signals"] += 1
        return "buy"

    result = two_week_paper_runner.run_two_week_paper_runner(
        start_day=date(2026, 7, 1),
        output_dir=output_dir,
        report_path=report_path,
        trading_client_factory=lambda **kwargs: MockTradingClient(is_open=True),
        market_data_loader=lambda *args, **kwargs: fake_prices(),
        signal_generator=signal_generator,
        order_manager_factory=lambda **kwargs: MockOrderManager(**kwargs),
    )

    assert result["days_processed"] == 14
    assert calls["signals"] == 14
    summaries = [path.read_text(encoding="utf-8") for path in sorted(output_dir.glob("*.md"))]
    assert all("order submitted or skipped: submitted" in text for text in summaries)


def test_buy_signal_submits_real_paper_order_and_logs(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("ALPACA_API_KEY", "demo-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo-secret")

    output_dir = tmp_path / "daily_summaries"
    report_path = tmp_path / "TWO_WEEK_REPORT.md"

    result = two_week_paper_runner.run_two_week_paper_runner(
        start_day=date(2026, 7, 1),
        days=1,
        output_dir=output_dir,
        report_path=report_path,
        dry_run=False,
        submit_enabled=True,
        trading_client_factory=lambda **kwargs: MockTradingClient(is_open=True),
        market_data_loader=lambda *args, **kwargs: fake_prices(),
        signal_generator=lambda *args, **kwargs: "buy",
        order_manager_factory=lambda **kwargs: MockOrderManager(**kwargs),
    )

    output = capsys.readouterr().out.splitlines()

    assert result["days_processed"] == 1
    assert "PAPER_ORDER_SUBMISSION_ENABLED" in output
    assert "PAPER_PREFLIGHT_STARTED" in output
    assert "PAPER_ACCOUNT_AUTHENTICATED" in output
    assert any(line.startswith("PAPER_ACCOUNT_STATUS status=") for line in output)
    assert any(line.startswith("PAPER_MARKET_STATUS is_open=") for line in output)
    assert "PAPER_PREFLIGHT_COMPLETED" in output
    assert any(line.startswith("DAILY_ORDER_COUNT value=") for line in output)
    assert any(line.startswith("DAILY_SUBMITTED_NOTIONAL value=") for line in output)
    assert "PAPER_ORDER_SUBMIT_STARTED date=2026-07-01 symbol=SPY notional=10.0" in output
    assert any(line.startswith("PAPER_ORDER_SUBMIT_RESULT submitted=True status=accepted order_id=paper-order-") for line in output)
    assert "DAILY_SUMMARY_CREATED date=2026-07-01 path=" + str(output_dir / "2026-07-01.md") in output
    assert report_path.exists()
    assert (output_dir / "2026-07-01.md").exists()


def test_logs_exact_error_between_submission_enabled_and_submit_started(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("ALPACA_API_KEY", "demo-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo-secret")

    output_dir = tmp_path / "daily_summaries"
    report_path = tmp_path / "TWO_WEEK_REPORT.md"

    def failing_order_manager_factory(**kwargs):
        raise RuntimeError("authorization=Bearer abcd account_number=123456789 ALPACA_API_KEY=secret-key")

    result = two_week_paper_runner.run_two_week_paper_runner(
        start_day=date(2026, 7, 1),
        days=1,
        output_dir=output_dir,
        report_path=report_path,
        dry_run=False,
        submit_enabled=True,
        trading_client_factory=lambda **kwargs: MockTradingClient(is_open=True),
        market_data_loader=lambda *args, **kwargs: fake_prices(),
        signal_generator=lambda *args, **kwargs: "buy",
        order_manager_factory=failing_order_manager_factory,
    )

    output = capsys.readouterr().out.splitlines()

    assert result["days_processed"] == 1
    assert "PAPER_ORDER_SUBMISSION_ENABLED" in output
    assert any(line.startswith("PAPER_RUN_ERROR PAPER_RUN_STAGE=order_manager_init") for line in output)
    assert any("PAPER_RUN_ERROR_TYPE=RuntimeError" in line for line in output)
    assert any("PAPER_RUN_ERROR_MESSAGE=" in line for line in output)
    assert not any(line.startswith("PAPER_ORDER_SUBMIT_STARTED") for line in output)
    assert "secret-key" not in "\n".join(output)
    assert "123456789" not in "\n".join(output)

    summary_text = (output_dir / "2026-07-01.md").read_text(encoding="utf-8")
    assert "reason: error" in summary_text
    assert "RuntimeError:" in summary_text
    assert "secret-key" not in summary_text
    assert "123456789" not in summary_text


def test_no_more_than_three_submissions_per_day(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("ALPACA_API_KEY", "demo-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo-secret")

    state_path = tmp_path / "daily_state.json"
    monkeypatch.setenv("PAPER_DAILY_STATE_PATH", str(state_path))

    base = datetime(2026, 7, 1, 10, 0, 0)
    timestamps = [base, base.replace(hour=11), base.replace(hour=12), base.replace(hour=13)]
    closes = [100.0, 101.0, 102.0, 103.0]

    results = []
    for idx in range(4):
        result = two_week_paper_runner.run_two_week_paper_runner(
            start_day=date(2026, 7, 1),
            days=1,
            output_dir=tmp_path / f"summaries_{idx}",
            report_path=tmp_path / f"report_{idx}.md",
            dry_run=False,
            submit_enabled=True,
            trading_client_factory=lambda **kwargs: MockTradingClient(is_open=True, clock_timestamp=timestamps[idx]),
            market_data_loader=lambda *args, _close=closes[idx], **kwargs: fake_prices_with_last_close(_close),
            signal_generator=lambda *args, **kwargs: "buy",
            order_manager_factory=lambda **kwargs: MockOrderManager(**kwargs),
        )
        results.append(result)

    summary4 = (tmp_path / "summaries_3" / "2026-07-01.md").read_text(encoding="utf-8")
    assert "reason: daily order count limit reached" in summary4

    state = json.loads(state_path.read_text(encoding="utf-8"))
    day_state = state["dates"]["2026-07-01"]
    assert day_state["daily_order_count"] == 3
    assert day_state["daily_submitted_notional"] == 30.0


def test_total_submitted_notional_cannot_exceed_thirty(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("ALPACA_API_KEY", "demo-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo-secret")

    state_path = tmp_path / "daily_state.json"
    monkeypatch.setenv("PAPER_DAILY_STATE_PATH", str(state_path))
    state_path.write_text(
        json.dumps(
            {
                "dates": {
                    "2026-07-01": {
                        "daily_order_count": 2,
                        "daily_submitted_notional": 25.0,
                        "orders": [],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    two_week_paper_runner.run_two_week_paper_runner(
        start_day=date(2026, 7, 1),
        days=1,
        output_dir=tmp_path / "summaries",
        report_path=tmp_path / "report.md",
        dry_run=False,
        submit_enabled=True,
        trading_client_factory=lambda **kwargs: MockTradingClient(is_open=True, clock_timestamp=datetime(2026, 7, 1, 11, 0, 0)),
        market_data_loader=lambda *args, **kwargs: fake_prices_with_last_close(123.45),
        signal_generator=lambda *args, **kwargs: "buy",
        order_manager_factory=lambda **kwargs: MockOrderManager(**kwargs),
    )

    summary = (tmp_path / "summaries" / "2026-07-01.md").read_text(encoding="utf-8")
    assert "reason: daily submitted notional limit reached" in summary

    state = json.loads(state_path.read_text(encoding="utf-8"))
    day_state = state["dates"]["2026-07-01"]
    assert day_state["daily_submitted_notional"] == 25.0


def test_cooldown_is_enforced(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("ALPACA_API_KEY", "demo-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo-secret")

    state_path = tmp_path / "daily_state.json"
    monkeypatch.setenv("PAPER_DAILY_STATE_PATH", str(state_path))

    two_week_paper_runner.run_two_week_paper_runner(
        start_day=date(2026, 7, 1),
        days=1,
        output_dir=tmp_path / "summaries1",
        report_path=tmp_path / "report1.md",
        dry_run=False,
        submit_enabled=True,
        trading_client_factory=lambda **kwargs: MockTradingClient(is_open=True, clock_timestamp=datetime(2026, 7, 1, 10, 0, 0)),
        market_data_loader=lambda *args, **kwargs: fake_prices_with_last_close(100.0),
        signal_generator=lambda *args, **kwargs: "buy",
        order_manager_factory=lambda **kwargs: MockOrderManager(**kwargs),
    )

    two_week_paper_runner.run_two_week_paper_runner(
        start_day=date(2026, 7, 1),
        days=1,
        output_dir=tmp_path / "summaries2",
        report_path=tmp_path / "report2.md",
        dry_run=False,
        submit_enabled=True,
        trading_client_factory=lambda **kwargs: MockTradingClient(is_open=True, clock_timestamp=datetime(2026, 7, 1, 10, 10, 0)),
        market_data_loader=lambda *args, **kwargs: fake_prices_with_last_close(101.0),
        signal_generator=lambda *args, **kwargs: "buy",
        order_manager_factory=lambda **kwargs: MockOrderManager(**kwargs),
    )

    summary = (tmp_path / "summaries2" / "2026-07-01.md").read_text(encoding="utf-8")
    assert "reason: cooldown active" in summary


def test_duplicate_signals_are_blocked(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("ALPACA_API_KEY", "demo-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo-secret")

    state_path = tmp_path / "daily_state.json"
    monkeypatch.setenv("PAPER_DAILY_STATE_PATH", str(state_path))

    loader = lambda *args, **kwargs: fake_prices_with_last_close(100.0)

    two_week_paper_runner.run_two_week_paper_runner(
        start_day=date(2026, 7, 1),
        days=1,
        output_dir=tmp_path / "summaries1",
        report_path=tmp_path / "report1.md",
        dry_run=False,
        submit_enabled=True,
        trading_client_factory=lambda **kwargs: MockTradingClient(is_open=True, clock_timestamp=datetime(2026, 7, 1, 10, 0, 0)),
        market_data_loader=loader,
        signal_generator=lambda *args, **kwargs: "buy",
        order_manager_factory=lambda **kwargs: MockOrderManager(**kwargs),
    )

    two_week_paper_runner.run_two_week_paper_runner(
        start_day=date(2026, 7, 1),
        days=1,
        output_dir=tmp_path / "summaries2",
        report_path=tmp_path / "report2.md",
        dry_run=False,
        submit_enabled=True,
        trading_client_factory=lambda **kwargs: MockTradingClient(is_open=True, clock_timestamp=datetime(2026, 7, 1, 11, 0, 0)),
        market_data_loader=loader,
        signal_generator=lambda *args, **kwargs: "buy",
        order_manager_factory=lambda **kwargs: MockOrderManager(**kwargs),
    )

    summary = (tmp_path / "summaries2" / "2026-07-01.md").read_text(encoding="utf-8")
    assert "reason: duplicate signal detected" in summary


def test_redeployment_cannot_reset_daily_limits(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("ALPACA_API_KEY", "demo-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo-secret")

    state_path = tmp_path / "daily_state.json"
    monkeypatch.setenv("PAPER_DAILY_STATE_PATH", str(state_path))

    def make_client_factory(hour):
        return lambda **kwargs: MockTradingClient(
            is_open=True,
            clock_timestamp=datetime(2026, 7, 1, hour, 0, 0),
        )

    for idx in range(3):
        two_week_paper_runner.run_two_week_paper_runner(
            start_day=date(2026, 7, 1),
            days=1,
            output_dir=tmp_path / f"summaries{idx}",
            report_path=tmp_path / f"report{idx}.md",
            dry_run=False,
            submit_enabled=True,
            trading_client_factory=make_client_factory(10 + idx),
            market_data_loader=lambda *args, _idx=idx, **kwargs: fake_prices_with_last_close(100.0 + _idx),
            signal_generator=lambda *args, **kwargs: "buy",
            order_manager_factory=lambda **kwargs: MockOrderManager(**kwargs),
        )

    # Simulates a fresh process/redeploy by calling the runner again with same persisted state path.
    two_week_paper_runner.run_two_week_paper_runner(
        start_day=date(2026, 7, 1),
        days=1,
        output_dir=tmp_path / "summaries_after_redeploy",
        report_path=tmp_path / "report_after_redeploy.md",
        dry_run=False,
        submit_enabled=True,
        trading_client_factory=lambda **kwargs: MockTradingClient(is_open=True, clock_timestamp=datetime(2026, 7, 1, 14, 0, 0)),
        market_data_loader=lambda *args, **kwargs: fake_prices_with_last_close(999.0),
        signal_generator=lambda *args, **kwargs: "buy",
        order_manager_factory=lambda **kwargs: MockOrderManager(**kwargs),
    )

    summary = (tmp_path / "summaries_after_redeploy" / "2026-07-01.md").read_text(encoding="utf-8")
    assert "reason: daily order count limit reached" in summary


def test_preflight_is_read_only_and_does_not_submit(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("ALPACA_API_KEY", "demo-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo-secret")

    client = MockTradingClient(is_open=True, clock_timestamp=datetime(2026, 7, 1, 10, 0, 0))
    two_week_paper_runner.run_two_week_paper_runner(
        start_day=date(2026, 7, 1),
        days=1,
        output_dir=tmp_path / "summaries",
        report_path=tmp_path / "report.md",
        dry_run=False,
        submit_enabled=True,
        trading_client_factory=lambda **kwargs: client,
        market_data_loader=lambda *args, **kwargs: fake_prices_with_last_close(100.0),
        signal_generator=lambda *args, **kwargs: "hold",
        order_manager_factory=lambda **kwargs: MockOrderManager(**kwargs),
    )

    output = capsys.readouterr().out
    assert "PAPER_PREFLIGHT_STARTED" in output
    assert "PAPER_ACCOUNT_AUTHENTICATED" in output
    assert "PAPER_PREFLIGHT_COMPLETED" in output
    assert client.submit_calls == 0


def test_state_survives_new_runner_instance(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("ALPACA_API_KEY", "demo-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo-secret")

    state_path = tmp_path / "daily_state.json"
    monkeypatch.setenv("PAPER_DAILY_STATE_PATH", str(state_path))

    two_week_paper_runner.run_two_week_paper_runner(
        start_day=date(2026, 7, 1),
        days=1,
        output_dir=tmp_path / "summaries1",
        report_path=tmp_path / "report1.md",
        dry_run=False,
        submit_enabled=True,
        trading_client_factory=lambda **kwargs: MockTradingClient(is_open=True, clock_timestamp=datetime(2026, 7, 1, 10, 0, 0)),
        market_data_loader=lambda *args, **kwargs: fake_prices_with_last_close(100.0),
        signal_generator=lambda *args, **kwargs: "buy",
        order_manager_factory=lambda **kwargs: MockOrderManager(**kwargs),
    )

    # New runner instance: second call should read prior state and increase day count.
    two_week_paper_runner.run_two_week_paper_runner(
        start_day=date(2026, 7, 1),
        days=1,
        output_dir=tmp_path / "summaries2",
        report_path=tmp_path / "report2.md",
        dry_run=False,
        submit_enabled=True,
        trading_client_factory=lambda **kwargs: MockTradingClient(is_open=True, clock_timestamp=datetime(2026, 7, 1, 11, 0, 0)),
        market_data_loader=lambda *args, **kwargs: fake_prices_with_last_close(101.0),
        signal_generator=lambda *args, **kwargs: "buy",
        order_manager_factory=lambda **kwargs: MockOrderManager(**kwargs),
    )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    day_state = state["dates"]["2026-07-01"]
    assert day_state["daily_order_count"] == 2
    assert day_state["daily_submitted_notional"] == 20.0


def test_corrupted_state_blocks_trading_and_sets_review_required(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("ALPACA_API_KEY", "demo-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo-secret")
    monkeypatch.delenv("REVIEW_REQUIRED", raising=False)

    state_path = tmp_path / "daily_state.json"
    monkeypatch.setenv("PAPER_DAILY_STATE_PATH", str(state_path))
    state_path.write_text("{this-is-bad-json", encoding="utf-8")

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
        order_manager_factory=lambda **kwargs: MockOrderManager(**kwargs),
    )

    assert result["review_required"] is True
    assert result["stop_reason"] == "state corrupted"
    assert os.environ.get("REVIEW_REQUIRED") == "true"

    summary = (tmp_path / "summaries" / "2026-07-01.md").read_text(encoding="utf-8")
    assert "reason: state corrupted" in summary
    assert "state_error:" in summary


def test_atomic_state_write_replaces_file(monkeypatch, tmp_path):
    state_path = tmp_path / "daily_state.json"
    old_payload = {
        "dates": {
            "2026-07-01": {
                "daily_order_count": 1,
                "daily_submitted_notional": 10.0,
                "orders": [],
            }
        }
    }
    new_payload = {
        "dates": {
            "2026-07-01": {
                "daily_order_count": 2,
                "daily_submitted_notional": 20.0,
                "orders": [],
            }
        }
    }
    state_path.write_text(json.dumps(old_payload), encoding="utf-8")

    two_week_paper_runner._write_daily_state(state_path, new_payload)

    loaded = json.loads(state_path.read_text(encoding="utf-8"))
    assert loaded["dates"]["2026-07-01"]["daily_order_count"] == 2
    assert not (tmp_path / "daily_state.json.tmp").exists()


def test_missing_state_is_initialized_and_persisted_when_signal_not_buy(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("ALPACA_API_KEY", "demo-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo-secret")

    state_path = tmp_path / "daily_state.json"
    monkeypatch.setenv("PAPER_DAILY_STATE_PATH", str(state_path))
    if state_path.exists():
        state_path.unlink()

    result = two_week_paper_runner.run_two_week_paper_runner(
        start_day=date(2026, 7, 1),
        days=1,
        output_dir=tmp_path / "summaries",
        report_path=tmp_path / "report.md",
        dry_run=False,
        submit_enabled=True,
        trading_client_factory=lambda **kwargs: MockTradingClient(is_open=True, clock_timestamp=datetime(2026, 7, 1, 10, 0, 0)),
        market_data_loader=lambda *args, **kwargs: fake_prices_with_last_close(100.0),
        signal_generator=lambda *args, **kwargs: "hold",
        order_manager_factory=lambda **kwargs: MockOrderManager(**kwargs),
    )

    output = capsys.readouterr().out
    assert result["stop_reason"] == "completed"
    assert "PAPER_DAILY_STATE_INITIALIZED" in output
    assert state_path.exists()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state == {"dates": {}}


def test_next_runner_instance_loads_existing_state(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("ALPACA_API_KEY", "demo-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo-secret")

    state_path = tmp_path / "daily_state.json"
    monkeypatch.setenv("PAPER_DAILY_STATE_PATH", str(state_path))

    two_week_paper_runner.run_two_week_paper_runner(
        start_day=date(2026, 7, 1),
        days=1,
        output_dir=tmp_path / "summaries1",
        report_path=tmp_path / "report1.md",
        dry_run=False,
        submit_enabled=True,
        trading_client_factory=lambda **kwargs: MockTradingClient(is_open=True, clock_timestamp=datetime(2026, 7, 1, 10, 0, 0)),
        market_data_loader=lambda *args, **kwargs: fake_prices_with_last_close(100.0),
        signal_generator=lambda *args, **kwargs: "hold",
        order_manager_factory=lambda **kwargs: MockOrderManager(**kwargs),
    )
    _ = capsys.readouterr()

    two_week_paper_runner.run_two_week_paper_runner(
        start_day=date(2026, 7, 2),
        days=1,
        output_dir=tmp_path / "summaries2",
        report_path=tmp_path / "report2.md",
        dry_run=False,
        submit_enabled=True,
        trading_client_factory=lambda **kwargs: MockTradingClient(is_open=True, clock_timestamp=datetime(2026, 7, 2, 10, 0, 0)),
        market_data_loader=lambda *args, **kwargs: fake_prices_with_last_close(101.0),
        signal_generator=lambda *args, **kwargs: "hold",
        order_manager_factory=lambda **kwargs: MockOrderManager(**kwargs),
    )

    output = capsys.readouterr().out
    assert "PAPER_DAILY_STATE_LOADED" in output
