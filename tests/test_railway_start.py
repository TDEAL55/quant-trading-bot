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

    def fake_report_checker(summary_dir=None, print_fn=print):
        print_fn("latest report date: 2026-07-08")
        print_fn("did bot run?: yes")
        print_fn("did it submit an order?: yes")
        print_fn("submitted=True")
        print_fn("order_id: paper-order-1")
        print_fn("stop_reason: approved")
        print_fn("review_required: False")

    monkeypatch.setattr(railway_start, "run_two_week_paper_runner", fake_runner)
    monkeypatch.setattr(railway_start.report_checker, "check_latest_report", fake_report_checker)

    result = railway_start.run_railway_job(now=datetime(2026, 7, 8, 9, 0, tzinfo=EASTERN_TZ))
    output = capsys.readouterr().out.splitlines()

    assert result["ran"] is True
    assert output == [
        "RAILWAY_JOB_STARTED",
        "TRADING_MODE value=PAPER",
        "PAPER_MODE_CONFIRMED",
        "ACCOUNT_CHECK_STARTED",
        "MARKET_CHECK_STARTED",
        "REPORT_CHECKER_STARTED",
        "latest report date: 2026-07-08",
        "did bot run?: yes",
        "did it submit an order?: yes",
        "submitted=True",
        "order_id: paper-order-1",
        "stop_reason: approved",
        "review_required: False",
        "REPORT_CHECKER_COMPLETED",
        "RAILWAY_JOB_COMPLETED market_date=2026-07-08 status=completed",
    ]
    assert "key-secret-value" not in "\n".join(output)
    assert "secret-secret-value" not in "\n".join(output)


def test_report_checker_failure_is_reported_without_retry(monkeypatch, tmp_path, capsys):
    marker = tmp_path / "marker.json"

    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("RAILWAY_RUN_MARKER_PATH", str(marker))

    calls = {"runner": 0}

    def fake_runner(**kwargs):
        calls["runner"] += 1
        return {
            "days_processed": 1,
            "review_required": False,
            "stop_reason": "completed",
            "report_path": "TWO_WEEK_REPORT.md",
        }

    def failing_report_checker(summary_dir=None, print_fn=print):
        raise RuntimeError("report checker boom")

    monkeypatch.setattr(railway_start, "run_two_week_paper_runner", fake_runner)
    monkeypatch.setattr(railway_start.report_checker, "check_latest_report", failing_report_checker)

    result = railway_start.run_railway_job(now=datetime(2026, 7, 8, 9, 0, tzinfo=EASTERN_TZ))
    output = capsys.readouterr().out.splitlines()

    assert result["ran"] is True
    assert calls["runner"] == 1
    assert output == [
        "RAILWAY_JOB_STARTED",
        "TRADING_MODE value=PAPER",
        "PAPER_MODE_CONFIRMED",
        "ACCOUNT_CHECK_STARTED",
        "MARKET_CHECK_STARTED",
        "REPORT_CHECKER_STARTED",
        "REPORT_CHECKER_FAILED",
        "RAILWAY_JOB_COMPLETED market_date=2026-07-08 status=completed",
    ]


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
