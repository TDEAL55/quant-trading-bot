from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import scheduled_paper_test


EASTERN_TZ = ZoneInfo("America/New_York")


class MockClock:
    def __init__(self, is_open=True):
        self.is_open = is_open


class MockAccount:
    def __init__(self, status="ACTIVE"):
        self.status = status


class MockTradingClient:
    def __init__(self, api_key=None, secret_key=None, paper=None):
        self.api_key = api_key
        self.secret_key = secret_key
        self.paper = paper
        self.submit_called = False

    def get_account(self):
        return MockAccount(status="ACTIVE")

    def get_clock(self):
        return MockClock(is_open=True)

    def submit_order(self, *args, **kwargs):
        self.submit_called = True
        raise AssertionError("submit_order should not be called")


class MockOrderManager:
    def __init__(self, mode=None, dry_run=None, submit_enabled=None, trading_client=None):
        self.mode = mode
        self.dry_run = dry_run
        self.submit_enabled = submit_enabled
        self.trading_client = trading_client
        self.last_command = None

    def place_order(self, command):
        self.last_command = command
        return {
            "approved": True,
            "reason": "approved",
            "dry_run": True,
            "submitted": False,
            "simulated_order": {
                "symbol": "SPY",
                "notional": 10.0,
                "side": "buy",
                "type": "market",
            },
        }


def test_next_weekday_10am_moves_to_monday_from_weekend():
    saturday = datetime(2026, 7, 11, 9, 0, tzinfo=EASTERN_TZ)
    target = scheduled_paper_test.get_next_weekday_10am_eastern(saturday)

    assert target.weekday() == 0
    assert target.hour == 10
    assert target.minute == 0


def test_next_weekday_10am_moves_to_next_day_after_10am():
    monday_11am = datetime(2026, 7, 6, 11, 0, tzinfo=EASTERN_TZ)
    target = scheduled_paper_test.get_next_weekday_10am_eastern(monday_11am)

    assert target.weekday() == 1
    assert target.hour == 10
    assert target.minute == 0


def test_blocks_live_mode(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "LIVE")
    with pytest.raises(RuntimeError, match="LIVE mode is blocked"):
        scheduled_paper_test.run_scheduled_paper_test_once(now=datetime(2026, 7, 6, 9, 0, tzinfo=EASTERN_TZ), sleep_fn=lambda _: None)


def test_requires_paper_mode(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "SIMULATION")
    with pytest.raises(RuntimeError, match="requires TRADING_MODE=PAPER"):
        scheduled_paper_test.run_scheduled_paper_test_once(now=datetime(2026, 7, 6, 9, 0, tzinfo=EASTERN_TZ), sleep_fn=lambda _: None)


def test_runs_one_dry_run_cycle_and_logs_result(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("ALPACA_API_KEY", "demo-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo-secret")

    sleep_calls = []
    created_managers = []

    def fake_sleep(seconds):
        sleep_calls.append(seconds)

    def fake_order_manager_factory(**kwargs):
        manager = MockOrderManager(**kwargs)
        created_managers.append(manager)
        return manager

    now = datetime(2026, 7, 6, 9, 59, 0, tzinfo=EASTERN_TZ)
    summary = scheduled_paper_test.run_scheduled_paper_test_once(
        now=now,
        sleep_fn=fake_sleep,
        trading_client_factory=MockTradingClient,
        order_manager_factory=fake_order_manager_factory,
    )

    assert len(sleep_calls) == 1
    assert summary["account_status"] == "ACTIVE"
    assert summary["market_open"] is True
    assert summary["order_result"]["approved"] is True
    assert created_managers[0].mode == "PAPER"
    assert created_managers[0].dry_run is True
    assert created_managers[0].submit_enabled is False
    assert created_managers[0].last_command == "BUY $10 of SPY"


def test_missing_credentials_fails_safely(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)

    isolated_env = tmp_path / ".env"
    isolated_env.write_text("TRADING_MODE=PAPER\n", encoding="utf-8")

    summary = scheduled_paper_test.run_scheduled_paper_test_once(
        now=datetime(2026, 7, 6, 9, 0, tzinfo=EASTERN_TZ),
        sleep_fn=lambda _: None,
        env_path=isolated_env,
        trading_client_factory=MockTradingClient,
    )

    assert summary["account_status"] == "unavailable"
    assert summary["order_result"]["approved"] is False
    assert "missing credentials" in summary["order_result"]["reason"]