from datetime import date
import os

import pandas as pd
import pytest

import two_week_paper_runner


class MockClock:
    def __init__(self, is_open=True):
        self.is_open = is_open


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
    def __init__(self, api_key=None, secret_key=None, paper=None, is_open=True, portfolio_values=None):
        self.api_key = api_key
        self.secret_key = secret_key
        self.paper = paper
        self.is_open = is_open
        self.portfolio_values = portfolio_values or [1000.0] * 20
        self.account_calls = 0

    def get_clock(self):
        return MockClock(is_open=self.is_open)

    def get_account(self):
        idx = min(self.account_calls, len(self.portfolio_values) - 1)
        value = self.portfolio_values[idx]
        self.account_calls += 1
        return MockAccount(portfolio_value=str(value), cash="1000", buying_power="1000")

    def get_all_positions(self):
        return [MockPosition()]


class MockOrderManager:
    def __init__(self, mode=None, dry_run=None, submit_enabled=None, trading_client=None):
        self.mode = mode
        self.dry_run = dry_run
        self.submit_enabled = submit_enabled
        self.trading_client = trading_client

    def place_order(self, command=None, order_type=None):
        if self.submit_enabled and not self.dry_run:
            return {
                "approved": True,
                "reason": "submitted",
                "submitted": True,
                "status": "accepted",
                "order_id": "paper-order-1",
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
    assert output == [
        "PAPER_ORDER_SUBMISSION_ENABLED",
        "PAPER_ORDER_SUBMIT_STARTED date=2026-07-01 symbol=SPY notional=10.0",
        "PAPER_ORDER_SUBMIT_RESULT submitted=True status=accepted order_id=paper-order-1",
        "DAILY_SUMMARY_CREATED date=2026-07-01 path=" + str(output_dir / "2026-07-01.md"),
    ]
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
    assert output[0] == "PAPER_ORDER_SUBMISSION_ENABLED"
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
