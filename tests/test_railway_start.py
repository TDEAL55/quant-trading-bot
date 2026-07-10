import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import railway_start


EASTERN_TZ = ZoneInfo("America/New_York")


def test_blocks_live_mode(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "LIVE")
    with pytest.raises(RuntimeError, match="LIVE mode detected"):
        railway_start.run_railway_job(now=datetime(2026, 7, 8, 9, 0, tzinfo=EASTERN_TZ))


def test_requires_paper_mode(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "SIMULATION")
    with pytest.raises(RuntimeError, match="requires TRADING_MODE=PAPER"):
        railway_start.run_railway_job(now=datetime(2026, 7, 8, 9, 0, tzinfo=EASTERN_TZ))


def test_skips_if_already_ran_for_market_day(monkeypatch, tmp_path):
    marker = tmp_path / "marker.json"
    marker.write_text(json.dumps({"market_date": "2026-07-08"}), encoding="utf-8")

    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("RAILWAY_RUN_MARKER_PATH", str(marker))

    calls = {"count": 0}

    def fake_runner(**kwargs):
        calls["count"] += 1
        return {"days_processed": 1, "review_required": False, "report_path": "TWO_WEEK_REPORT.md"}

    monkeypatch.setattr(railway_start, "run_two_week_paper_runner", fake_runner)

    result = railway_start.run_railway_job(now=datetime(2026, 7, 8, 9, 0, tzinfo=EASTERN_TZ))

    assert result["ran"] is False
    assert result["reason"] == "already ran for market day"
    assert calls["count"] == 0


def test_emits_railway_markers_on_success(monkeypatch, tmp_path, capsys):
    marker = tmp_path / "marker.json"

    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("RAILWAY_RUN_MARKER_PATH", str(marker))
    monkeypatch.setenv("ALPACA_API_KEY", "key-secret-value")
    monkeypatch.setenv("ALPACA_API_SECRET", "secret-secret-value")

    def fake_runner(**kwargs):
        return {
            "days_processed": 1,
            "review_required": False,
            "stop_reason": "completed",
            "report_path": "TWO_WEEK_REPORT.md",
        }

    monkeypatch.setattr(railway_start, "run_two_week_paper_runner", fake_runner)

    result = railway_start.run_railway_job(now=datetime(2026, 7, 8, 9, 0, tzinfo=EASTERN_TZ))
    output = capsys.readouterr().out.splitlines()
    expected_summary = Path(railway_start.__file__).resolve().parent / "daily_summaries" / "2026-07-08.md"

    assert result["ran"] is True
    assert output == [
        "RAILWAY_JOB_STARTED",
        "TRADING_MODE value=PAPER",
        "PAPER_MODE_CONFIRMED",
        "ACCOUNT_CHECK_STARTED",
        "MARKET_CHECK_STARTED",
        "RAILWAY_JOB_COMPLETED market_date=2026-07-08 status=completed",
    ]
    assert "key-secret-value" not in "\n".join(output)
    assert "secret-secret-value" not in "\n".join(output)


def test_main_reports_safe_failure(monkeypatch, capsys):
    def fake_run_railway_job(now=None):
        raise RuntimeError("ALPACA_API_KEY=secret-value")

    monkeypatch.setattr(railway_start, "run_railway_job", fake_run_railway_job)

    exit_code = railway_start.main()
    output = capsys.readouterr().out.splitlines()

    assert exit_code == 1
    assert output[-1] == "RAILWAY_JOB_FAILED error=RuntimeError"
    assert "secret-value" not in "\n".join(output)


def test_runs_one_day_and_writes_marker(monkeypatch, tmp_path):
    marker = tmp_path / "marker.json"

    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("RAILWAY_RUN_MARKER_PATH", str(marker))

    captured = {}

    def fake_runner(**kwargs):
        captured.update(kwargs)
        return {
            "days_processed": 1,
            "review_required": False,
            "report_path": "TWO_WEEK_REPORT.md",
        }

    monkeypatch.setattr(railway_start, "run_two_week_paper_runner", fake_runner)

    now = datetime(2026, 7, 8, 9, 0, tzinfo=EASTERN_TZ)
    result = railway_start.run_railway_job(now=now)

    assert result["ran"] is True
    assert result["reason"] == "completed"
    assert captured["days"] == 1
    assert captured["load_env_file"] is False
    assert captured["dry_run"] is False
    assert captured["submit_enabled"] is True
    assert marker.exists()
    marker_data = json.loads(marker.read_text(encoding="utf-8"))
    assert marker_data["market_date"] == "2026-07-08"
