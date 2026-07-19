from __future__ import annotations

from daily_run_repository import DailyRunRepository


def test_daily_run_repository_persistence(tmp_path):
    db_path = tmp_path / "daily_runs.db"
    repo = DailyRunRepository(database_url=f"sqlite:///{db_path}")
    try:
        row = {
            "run_id": "daily-1",
            "timestamp": "2026-07-18T21:00:00+00:00",
            "market_session": "latest_completed_session",
            "market_status": "fresh",
            "candidate_count": 20,
            "qualified_count": 2,
            "selected_symbols": ["JPM"],
            "execution_status": "completed",
            "performance_run_id": "perf-1",
            "paper_validation_run_id": "pv-1",
            "report": {"Dashboard Updated": True},
        }
        saved = repo.save_run(row)
        latest = repo.latest_run()
        history = repo.list_runs(limit=10)

        assert saved["run_id"] == "daily-1"
        assert latest["run_id"] == "daily-1"
        assert latest["selected_symbols"] == ["JPM"]
        assert latest["report"]["Dashboard Updated"] is True
        assert len(history) >= 1
    finally:
        repo.close()
