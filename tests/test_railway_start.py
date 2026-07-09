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
    assert marker.exists()
    marker_data = json.loads(marker.read_text(encoding="utf-8"))
    assert marker_data["market_date"] == "2026-07-08"
