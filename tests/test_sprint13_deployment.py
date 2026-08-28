from __future__ import annotations

import json
import socket
from datetime import datetime, timezone
from pathlib import Path

import pytest

import unattended_daily_runner
from deployment_config import DeploymentConfigError, load_deployment_config
from health_check import run_health_check
from notification_service import NotificationService
from run_lock import DailyRunLock, RunLockBusyError
from unattended_daily_runner import run_unattended_daily_cycle


class _Notifier:
    def __init__(self):
        self.events: list[tuple[str, str, dict]] = []

    def send_alert(self, event, event_id, **fields):
        self.events.append((str(event), str(event_id), dict(fields)))
        return True


class _FailingNotifier:
    def send_alert(self, event, event_id, **fields):
        raise RuntimeError("notification down")


def _cfg(tmp_path, **overrides):
    db_path = tmp_path / "bot.db"
    values = {
        "kill_switch": False,
        "trading_mode": "PAPER",
        "auto_approve_paper": True,
        "database_url": f"sqlite:///{db_path}",
        "database_path": db_path,
        "notifications_enabled": True,
        "max_daily_orders": 100000,
        "scan_symbols": ("AAPL", "MSFT", "SPY"),
    }
    values.update(overrides)
    return type("Cfg", (), values)()


def _cycle_tuple(cycle_status="ENTRY_CANDIDATES_PROCESSED", candidates=1, fills=1, stop_exits=0, target_exits=0, errors=None):
    summary = {
        "cycle_id": "CYCLE-TEST-001",
        "cycle_status": cycle_status,
        "starting_cash": 10000.0,
        "ending_cash": 9800.0,
        "starting_equity": 10000.0,
        "ending_equity": 10010.0,
        "positions_monitored": 2,
        "stop_exits": stop_exits,
        "target_exits": target_exits,
        "available_slots": 10,
        "scanner_executed": True,
        "symbols_scanned": 25,
        "candidates_selected": candidates,
        "tickets_generated": fills,
        "local_entry_execution_attempted": True,
        "local_entry_fills": fills,
        "strategy_identifiers": ["strategy_id:multi_factor_v1"],
        "monitor_results": [
            {
                "symbol": "AAPL",
                "monitoring_status": "OPEN",
                "quantity_before": 1,
                "realized_profit_loss": 0.0,
                "realized_return_percentage": 0.0,
                "simulated_exit_price": "-",
            }
        ],
        "entry_results": [
            {
                "symbol": "MSFT",
                "execution_status": "FILLED",
                "quantity": 2,
                "simulated_fill_price": 150.0,
                "order_fingerprint": "fp-1",
            }
        ],
        "errors": list(errors or []),
        "decision_reason": "cycle completed",
    }
    stage_rows = [{"stage": "position_monitoring", "status": "DONE", "details": "ok"}]
    history = {"total_cycles": 10}
    return summary, stage_rows, history


def test_deployment_config_allows_paper_auto_approval(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("AUTO_APPROVE_PAPER", "true")
    monkeypatch.setenv("MAX_DAILY_ORDERS", "1")
    monkeypatch.setenv("RUN_TIMEZONE", "America/New_York")
    monkeypatch.setenv("RUN_HOUR", "9")
    monkeypatch.setenv("RUN_MINUTE", "30")
    monkeypatch.setenv("NOTIFICATIONS_ENABLED", "false")
    monkeypatch.setenv("KILL_SWITCH", "false")

    config = load_deployment_config()
    assert config.trading_mode == "PAPER"
    assert config.auto_approve_paper is True


def test_deployment_config_blocks_live(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("TRADING_MODE", "LIVE")
    with pytest.raises(DeploymentConfigError):
        load_deployment_config()


def test_deployment_config_rejects_non_paper_alpaca_endpoint(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("AUTO_APPROVE_PAPER", "true")
    monkeypatch.setenv("MAX_DAILY_ORDERS", "1")
    monkeypatch.setenv("PAPER_BROKER_BACKEND", "ALPACA")
    monkeypatch.setenv("ALPACA_API_KEY", "demo")
    monkeypatch.setenv("ALPACA_API_SECRET", "demo")
    monkeypatch.setenv("ALPACA_PAPER_BASE_URL", "https://api.alpaca.markets")

    with pytest.raises(DeploymentConfigError, match="ALPACA_PAPER_BASE_URL"):
        load_deployment_config()


def test_deployment_config_requires_alpaca_credentials(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("AUTO_APPROVE_PAPER", "true")
    monkeypatch.setenv("MAX_DAILY_ORDERS", "1")
    monkeypatch.setenv("PAPER_BROKER_BACKEND", "ALPACA")
    monkeypatch.setenv("ALPACA_PAPER_BASE_URL", "https://paper-api.alpaca.markets")
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)

    with pytest.raises(DeploymentConfigError, match="ALPACA_API_KEY"):
        load_deployment_config()


def test_run_lock_releases_and_recovers_stale_lock(tmp_path):
    lock_path = tmp_path / "daily.lock"
    lock = DailyRunLock(lock_path, stale_after_seconds=1, owner="test-owner")
    state = lock.acquire()
    assert state.owner == "test-owner"
    with pytest.raises(RunLockBusyError):
        DailyRunLock(lock_path, stale_after_seconds=3600, owner="other").acquire()
    lock.release()
    lock_path.write_text(json.dumps({"owner": "stale", "acquired_at": "2020-01-01T00:00:00+00:00", "pid": 1}), encoding="utf-8")
    recovered = DailyRunLock(lock_path, stale_after_seconds=1, owner="recovered").acquire()
    assert recovered.owner == "recovered"


def test_run_lock_recovers_immediately_when_prior_process_is_dead(tmp_path):
    lock_path = tmp_path / "dead-process.lock"
    lock_path.write_text(
        json.dumps(
            {
                "owner": "continuous-paper-runner",
                "acquired_at": datetime.now(timezone.utc).isoformat(),
                "pid": 99999999,
                "host": socket.gethostname(),
            }
        ),
        encoding="utf-8",
    )

    recovered = DailyRunLock(lock_path, stale_after_seconds=7200, owner="restart").acquire()

    assert recovered.owner == "restart"


def test_unattended_runner_blocks_kill_switch(tmp_path):
    cfg = _cfg(tmp_path, kill_switch=True)
    result = run_unattended_daily_cycle(config_loader=lambda: cfg)
    assert result["status"] == "killed"


def test_unattended_runner_auto_approval_disabled(tmp_path):
    cfg = _cfg(tmp_path, auto_approve_paper=False)
    result = run_unattended_daily_cycle(config_loader=lambda: cfg)
    assert result["status"] == "auto_approval_disabled"


def test_unattended_runner_rejects_non_paper_mode(tmp_path):
    cfg = _cfg(tmp_path, trading_mode="SIMULATION")
    result = run_unattended_daily_cycle(config_loader=lambda: cfg)
    assert result["status"] == "failed"


def test_unattended_runner_skips_when_market_closed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(unattended_daily_runner, "_market_is_open", lambda _now: False)
    result = run_unattended_daily_cycle(config_loader=lambda: cfg)
    assert result["status"] == "market_closed"


def test_unattended_runner_completes_and_sends_notifications(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    notifier = _Notifier()
    monkeypatch.setattr(unattended_daily_runner, "_market_is_open", lambda _now: True)

    result = run_unattended_daily_cycle(
        config_loader=lambda: cfg,
        cycle_runner=lambda: _cycle_tuple(),
        notifier_factory=lambda: notifier,
    )

    assert result["status"] == "completed"
    assert result["cycle"]["local_entry_fills"] == 1
    assert result["notification"]["sent"] >= 3
    assert any(event == "paper_trade_opened" for event, _, _fields in notifier.events)
    assert any(event == "scan_completed_with_candidates" for event, _, _fields in notifier.events)


def test_notification_failure_does_not_stop_trading(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(unattended_daily_runner, "_market_is_open", lambda _now: True)

    result = run_unattended_daily_cycle(
        config_loader=lambda: cfg,
        cycle_runner=lambda: _cycle_tuple(),
        notifier_factory=lambda: _FailingNotifier(),
    )

    assert result["status"] == "completed"
    assert result["notification"]["failed"] > 0


def test_unattended_runner_reports_data_source_failure(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    notifier = _Notifier()
    monkeypatch.setattr(unattended_daily_runner, "_market_is_open", lambda _now: True)

    def _boom_cycle():
        raise RuntimeError("Market data unavailable for JPM")

    result = run_unattended_daily_cycle(
        config_loader=lambda: cfg,
        cycle_runner=_boom_cycle,
        notifier_factory=lambda: notifier,
    )

    assert result["status"] == "failed"
    assert any(event == "unhandled_cycle_error" for event, _, _fields in notifier.events)
    assert any(event == "data_source_failure" for event, _, _fields in notifier.events)


def test_unattended_runner_reports_integrity_failure(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    notifier = _Notifier()
    monkeypatch.setattr(unattended_daily_runner, "_market_is_open", lambda _now: True)

    summary, rows, history = _cycle_tuple(cycle_status="FAILED_PRECHECK", candidates=0, fills=0, errors=["Ledger integrity validation failed"])
    result = run_unattended_daily_cycle(
        config_loader=lambda: cfg,
        cycle_runner=lambda: (summary, rows, history),
        notifier_factory=lambda: notifier,
    )

    assert result["status"] == "integrity_failed"
    assert any(event == "integrity_failure" for event, _, _fields in notifier.events)


def test_notification_service_file_output(tmp_path):
    path = tmp_path / "summary.log"
    service = NotificationService(output="file", file_path=path)
    result = service.send({"run_status": "completed", "selected_symbol": "JPM", "score": 83.0, "confidence": 68.0, "risk_result": "approved", "order_fill": "sim-JPM", "reconciliation": "matched", "portfolio_value": 10000.0, "dashboard_update": True})
    assert result["status"] == "sent"
    assert "selected symbol: JPM" in path.read_text(encoding="utf-8")


def test_health_check_success_and_failure(tmp_path, monkeypatch):
    db_path = tmp_path / "health.db"
    from monitoring_db import MonitoringDatabase

    db = MonitoringDatabase(database_url=f"sqlite:///{db_path}")
    db.ensure_schema()
    db.insert_bot_run(
        {
            "run_id": "run-health",
            "run_timestamp": "2026-07-18T12:00:00+00:00",
            "market_date": "2026-07-18",
            "trading_mode": "PAPER",
            "market_status": "open",
            "bot_status": "healthy",
            "review_required": False,
            "stop_reason": "completed",
            "safe_error_type": "",
            "safe_error_message": "",
            "submitted": False,
            "symbol": "JPM",
            "notional": 0.0,
            "safe_order_status": "skipped",
        }
    )
    db.close()

    monkeypatch.setattr("health_check.download_price_data", lambda *args, **kwargs: __import__("pandas").DataFrame({"Close": [1.0, 2.0]}, index=["2026-07-17", "2026-07-18"]))
    good = run_health_check(database_url=f"sqlite:///{db_path}", minimum_free_gb=0.0)
    assert good["healthy"] is True

    monkeypatch.setattr("health_check.download_price_data", lambda *args, **kwargs: __import__("pandas").DataFrame())
    bad = run_health_check(database_url=f"sqlite:///{db_path}", minimum_free_gb=0.0)
    assert bad["healthy"] is False


def test_systemd_files_parse_and_contain_expected_settings():
    service = Path(__file__).resolve().parents[1] / "deployment" / "quant-bot.service"
    timer = Path(__file__).resolve().parents[1] / "deployment" / "quant-bot.timer"
    install = Path(__file__).resolve().parents[1] / "deployment" / "install_server.sh"

    service_text = service.read_text(encoding="utf-8")
    timer_text = timer.read_text(encoding="utf-8")
    install_text = install.read_text(encoding="utf-8")

    assert "Type=oneshot" in service_text
    assert "User=quantbot" in service_text
    assert "WorkingDirectory=/home/quantbot/quant-trading-bot" in service_text
    assert "ExecStartPost=/usr/bin/env bash /home/quantbot/quant-trading-bot/deployment/backup_daily_database.sh" in service_text
    assert "Persistent=true" in timer_text
    assert "Timezone=America/New_York" in timer_text
    assert "09:30:00" in timer_text
    assert "PROJECT_PATH=\"/home/quantbot/quant-trading-bot\"" in install_text
    assert "quant-bot-mobile-dashboard.service" in install_text
