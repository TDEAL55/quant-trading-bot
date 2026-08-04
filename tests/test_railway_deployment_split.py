from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import continuous_paper_runner


EASTERN_TZ = ZoneInfo("America/New_York")


def _config(tmp_path, trading_mode="PAPER", scan_interval_minutes=5, max_daily_orders=1, db_subpath="runner.db"):
    db_path = tmp_path / db_subpath
    return type(
        "Cfg",
        (),
        {
            "trading_mode": trading_mode,
            "database_url": f"sqlite:///{db_path}",
            "database_path": db_path,
            "scan_interval_minutes": scan_interval_minutes,
            "max_daily_orders": max_daily_orders,
            "scan_only_during_market_hours": False,
            "continuous_runner_dry_run": True,
        },
    )()


def test_procfile_worker_defaults_to_dashboard():
    procfile = Path("Procfile").read_text(encoding="utf-8")
    worker_line = [line for line in procfile.splitlines() if line.startswith("worker:")][0]
    assert "dashboard_app.py" in worker_line
    assert "railway_start.py" not in worker_line
    assert "continuous_paper_runner.py" not in worker_line


def test_procfile_dashboard_entrypoint_is_independent():
    procfile = Path("Procfile").read_text(encoding="utf-8")
    lines = procfile.splitlines()
    worker_line = [line for line in lines if line.startswith("worker:")][0]
    dashboard_line = [line for line in lines if line.startswith("dashboard:")][0]
    assert "streamlit run dashboard_app.py" in worker_line
    assert "streamlit run dashboard_app.py" in dashboard_line


def test_railway_environment_blocks_autonomous_worker_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.delenv("ALLOW_RAILWAY_TRADING_WORKER", raising=False)

    with pytest.raises(RuntimeError, match="ALLOW_RAILWAY_TRADING_WORKER=true"):
        continuous_paper_runner.run_continuous_paper_runner(
            config_loader=lambda: _config(tmp_path),
            runner=lambda **kwargs: {},
            state_path=tmp_path / "state.json",
            now_provider=lambda: datetime(2026, 8, 3, 10, 0, tzinfo=EASTERN_TZ),
            sleep_fn=lambda _seconds: None,
            max_iterations=1,
        )


def test_railway_guard_prevents_lock_acquisition(tmp_path, monkeypatch):
    monkeypatch.setenv("RAILWAY_SERVICE_ID", "svc_123")
    monkeypatch.delenv("ALLOW_RAILWAY_TRADING_WORKER", raising=False)
    lock_attempts = {"count": 0}

    class _ShouldNotBeUsedLock:
        def __init__(self, **kwargs):
            lock_attempts["count"] += 1

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

    with pytest.raises(RuntimeError, match="ALLOW_RAILWAY_TRADING_WORKER=true"):
        continuous_paper_runner.run_continuous_paper_runner(
            config_loader=lambda: _config(tmp_path),
            runner=lambda **kwargs: {},
            lock_factory=_ShouldNotBeUsedLock,
            state_path=tmp_path / "state.json",
            now_provider=lambda: datetime(2026, 8, 3, 10, 0, tzinfo=EASTERN_TZ),
            sleep_fn=lambda _seconds: None,
            max_iterations=1,
        )

    assert lock_attempts["count"] == 0


def test_digitalocean_path_remains_supported_with_dry_run(tmp_path, monkeypatch):
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("RAILWAY_PROJECT_ID", raising=False)
    monkeypatch.delenv("RAILWAY_SERVICE_ID", raising=False)
    monkeypatch.delenv("RAILWAY_DEPLOYMENT_ID", raising=False)
    runner_calls = []

    def _runner(**kwargs):
        runner_calls.append(dict(kwargs))
        return {
            "execution_status": "no_trade",
            "execution": {
                "paper_order": {"order_id": ""},
                "risk_result": {"approved": False, "checks": {"duplicate_protection": True}},
                "reconciliation": {"reconciliation_status": "matched", "position_mismatch_count": 0},
            },
        }

    stats = continuous_paper_runner.run_continuous_paper_runner(
        config_loader=lambda: _config(tmp_path),
        runner=_runner,
        state_path=tmp_path / "state.json",
        now_provider=lambda: datetime(2026, 8, 3, 10, 0, tzinfo=EASTERN_TZ),
        sleep_fn=lambda _seconds: None,
        max_iterations=1,
    )

    assert stats["scans_attempted"] == 1
    assert len(runner_calls) == 1
    assert runner_calls[0]["dry_run"] is True
