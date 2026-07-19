from __future__ import annotations

import json
from pathlib import Path

import pytest

from deployment_config import DeploymentConfigError, load_deployment_config
from health_check import run_health_check
from notification_service import NotificationService
from run_lock import DailyRunLock, RunLockBusyError
from unattended_daily_runner import run_unattended_daily_cycle


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


def test_deployment_config_blocks_auto_approval_outside_paper(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("TRADING_MODE", "SIMULATION")
    monkeypatch.setenv("AUTO_APPROVE_PAPER", "true")
    with pytest.raises(DeploymentConfigError):
        load_deployment_config()


def test_deployment_config_blocks_temp_database_path(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:////tmp/quant-bot.db")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    with pytest.raises(DeploymentConfigError):
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


def test_run_lock_blocks_simultaneous_run(tmp_path):
    lock_path = tmp_path / "daily.lock"
    first = DailyRunLock(lock_path, stale_after_seconds=3600, owner="first")
    first.acquire()
    with pytest.raises(RunLockBusyError):
        DailyRunLock(lock_path, stale_after_seconds=3600, owner="second").acquire()


def test_unattended_runner_blocks_kill_switch(monkeypatch):
    monkeypatch.setattr("unattended_daily_runner.load_deployment_config", lambda: type("Cfg", (), {"kill_switch": True, "trading_mode": "PAPER", "auto_approve_paper": True, "database_url": "sqlite:///x.db", "database_path": Path("x.db"), "notifications_enabled": False, "max_daily_orders": 1})())
    result = run_unattended_daily_cycle(config_loader=lambda: type("Cfg", (), {"kill_switch": True, "trading_mode": "PAPER", "auto_approve_paper": True, "database_url": "sqlite:///x.db", "database_path": Path("x.db"), "notifications_enabled": False, "max_daily_orders": 1})())
    assert result["status"] == "killed"


def test_unattended_runner_blocks_stale_market_data(tmp_path):
    db_path = tmp_path / "stale.db"
    cfg = type("Cfg", (), {"kill_switch": False, "trading_mode": "PAPER", "auto_approve_paper": True, "database_url": f"sqlite:///{db_path}", "database_path": db_path, "notifications_enabled": False, "max_daily_orders": 1})()
    result = run_unattended_daily_cycle(
        config_loader=lambda: cfg,
        market_snapshot_loader=lambda: {"fresh": False, "stale": True, "age_days": 999},
        runner=lambda **kwargs: pytest.fail("runner should not be called"),
    )
    assert result["status"] == "stale_market_data"


def test_unattended_runner_auto_approval_disabled(monkeypatch):
    cfg = type("Cfg", (), {"kill_switch": False, "trading_mode": "PAPER", "auto_approve_paper": False, "database_url": "sqlite:///x.db", "database_path": Path("x.db"), "notifications_enabled": False, "max_daily_orders": 1})()
    result = run_unattended_daily_cycle(config_loader=lambda: cfg)
    assert result["status"] == "auto_approval_disabled"


def test_unattended_runner_rejects_non_paper_mode(tmp_path):
    cfg = type("Cfg", (), {"kill_switch": False, "trading_mode": "SIMULATION", "auto_approve_paper": True, "database_url": f"sqlite:///{tmp_path / 'db.sqlite'}", "database_path": tmp_path / "db.sqlite", "notifications_enabled": False, "max_daily_orders": 1})()
    result = run_unattended_daily_cycle(config_loader=lambda: cfg)
    assert result["status"] == "failed"


def test_unattended_runner_maps_duplicate_rejection(tmp_path):
    cfg = type("Cfg", (), {"kill_switch": False, "trading_mode": "PAPER", "auto_approve_paper": True, "database_url": f"sqlite:///{tmp_path / 'db.sqlite'}", "database_path": tmp_path / "db.sqlite", "notifications_enabled": False, "max_daily_orders": 1})()

    def _runner(**kwargs):
        return {
            "execution_status": "risk_rejected",
            "dashboard_updated": False,
            "execution": {"selected_symbol": "JPM", "overall_score": 83.0, "confidence": 68.0, "risk_result": {"approved": False, "checks": {"duplicate_protection": False}}, "paper_order": {"order_id": None}},
            "performance": {"metrics": {"portfolio_value": 10000.0}},
            "persistence": {"run_id": "daily-1"},
        }

    result = run_unattended_daily_cycle(
        config_loader=lambda: cfg,
        market_snapshot_loader=lambda: {"fresh": True, "stale": False, "age_days": 0},
        runner=_runner,
    )
    assert result["status"] == "duplicate_rejected"


def test_unattended_runner_preserves_risk_rejection(tmp_path):
    cfg = type("Cfg", (), {"kill_switch": False, "trading_mode": "PAPER", "auto_approve_paper": True, "database_url": f"sqlite:///{tmp_path / 'db.sqlite'}", "database_path": tmp_path / "db.sqlite", "notifications_enabled": False, "max_daily_orders": 1})()

    def _runner(**kwargs):
        return {
            "execution_status": "risk_rejected",
            "dashboard_updated": False,
            "execution": {"selected_symbol": "JPM", "overall_score": 83.0, "confidence": 68.0, "risk_result": {"approved": False, "checks": {"duplicate_protection": True}}, "paper_order": {}},
            "performance": {"metrics": {"portfolio_value": 10000.0}},
            "persistence": {"run_id": "daily-1"},
        }

    result = run_unattended_daily_cycle(
        config_loader=lambda: cfg,
        market_snapshot_loader=lambda: {"fresh": True, "stale": False, "age_days": 0},
        runner=_runner,
    )
    assert result["status"] == "risk_rejected"


def test_unattended_runner_requires_reconciliation_match(tmp_path):
    cfg = type("Cfg", (), {"kill_switch": False, "trading_mode": "PAPER", "auto_approve_paper": True, "database_url": f"sqlite:///{tmp_path / 'db.sqlite'}", "database_path": tmp_path / "db.sqlite", "notifications_enabled": False, "max_daily_orders": 1})()

    def _runner(**kwargs):
        return {
            "execution_status": "completed",
            "dashboard_updated": True,
            "execution": {
                "selected_symbol": "JPM",
                "overall_score": 83.0,
                "confidence": 68.0,
                "risk_result": {"approved": True, "checks": {"duplicate_protection": True}},
                "paper_order": {"order_id": "o-1"},
                "reconciliation": {"reconciliation_status": "mismatch", "position_mismatch_count": 1},
            },
            "performance": {"metrics": {"portfolio_value": 10000.0}},
            "persistence": {"run_id": "daily-1"},
        }

    result = run_unattended_daily_cycle(
        config_loader=lambda: cfg,
        market_snapshot_loader=lambda: {"fresh": True, "stale": False, "age_days": 0, "session_type": "latest_completed_session"},
        runner=_runner,
    )
    assert result["status"] == "failed"


def test_unattended_runner_enforces_one_order_limit(tmp_path):
    cfg = type("Cfg", (), {"kill_switch": False, "trading_mode": "PAPER", "auto_approve_paper": True, "database_url": f"sqlite:///{tmp_path / 'db.sqlite'}", "database_path": tmp_path / "db.sqlite", "notifications_enabled": False, "max_daily_orders": 3})()

    def _runner(**kwargs):
        return {
            "execution_status": "completed",
            "dashboard_updated": True,
            "execution": {
                "selected_symbol": "JPM",
                "overall_score": 83.0,
                "confidence": 68.0,
                "risk_result": {"approved": True, "checks": {"duplicate_protection": True}},
                "paper_orders": [{"order_id": "o-1"}, {"order_id": "o-2"}],
                "paper_order": {"order_id": "o-1"},
                "reconciliation": {"reconciliation_status": "matched", "position_mismatch_count": 0},
            },
            "performance": {"metrics": {"portfolio_value": 10000.0}},
            "persistence": {"run_id": "daily-1"},
        }

    result = run_unattended_daily_cycle(
        config_loader=lambda: cfg,
        market_snapshot_loader=lambda: {"fresh": True, "stale": False, "age_days": 0, "session_type": "latest_completed_session"},
        runner=_runner,
    )
    assert result["status"] == "failed"


def test_notification_failure_does_not_corrupt_completed_run(tmp_path):
    cfg = type("Cfg", (), {"kill_switch": False, "trading_mode": "PAPER", "auto_approve_paper": True, "database_url": f"sqlite:///{tmp_path / 'db.sqlite'}", "database_path": tmp_path / "db.sqlite", "notifications_enabled": True, "max_daily_orders": 1})()

    class FailingNotifier:
        def __init__(self, output="console"):
            pass

        def send(self, payload):
            raise RuntimeError("notification down")

    def _runner(**kwargs):
        return {
            "execution_status": "completed",
            "dashboard_updated": True,
            "execution": {
                "selected_symbol": "JPM",
                "overall_score": 83.0,
                "confidence": 68.0,
                "risk_result": {"approved": True, "checks": {"duplicate_protection": True}},
                "paper_order": {"order_id": "o-1"},
                "reconciliation": {"reconciliation_status": "matched", "position_mismatch_count": 0},
            },
            "performance": {"metrics": {"portfolio_value": 10000.0}},
            "persistence": {"run_id": "daily-1"},
        }

    result = run_unattended_daily_cycle(
        config_loader=lambda: cfg,
        market_snapshot_loader=lambda: {"fresh": True, "stale": False, "age_days": 0, "session_type": "latest_completed_session"},
        runner=_runner,
        notification_service_factory=FailingNotifier,
    )
    assert result["status"] == "completed"
    assert result["notification_status"] == "failed"


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
    assert "WorkingDirectory=/opt/quant-bot" in service_text
    assert "ExecStartPost=/usr/bin/env bash /opt/quant-bot/deployment/backup_daily_database.sh" in service_text
    assert "Persistent=true" in timer_text
    assert "Timezone=America/New_York" in timer_text
    assert "chmod" in install_text or "install -m 0600" in install_text
