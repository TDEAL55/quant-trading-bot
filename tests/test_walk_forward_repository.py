from __future__ import annotations

import pytest

from walk_forward_repository import MonitoringWalkForwardRepository, WalkForwardRunPayload


def _payload() -> WalkForwardRunPayload:
    return WalkForwardRunPayload(
        run={
            "run_id": "wf-1",
            "created_at": "2024-06-01T00:00:00+00:00",
            "window_type": "rolling",
            "training_periods": 3,
            "validation_periods": 1,
            "step_periods": 1,
            "horizon": 20,
            "benchmark_symbol": "SPY",
            "configuration_snapshot": {"training_periods": 3},
            "total_windows": 1,
            "completed_windows": 1,
            "skipped_windows": 0,
            "scorecard": {"overall_validation_status": "acceptable"},
            "factor_stability_summary": [{"factor": "trend_score"}],
            "performance_decay": {"trend_slope": -0.01},
            "regime_robustness": [{"market_regime": "bull"}],
            "performance": {"rows_loaded": 10},
            "status": "completed",
            "duration_seconds": 1.2,
            "error_message": None,
        },
        windows=[
            {
                "window_id": "rolling-20-1",
                "training_start_date": "2024-01-01",
                "training_end_date": "2024-03-01",
                "validation_start_date": "2024-04-01",
                "validation_end_date": "2024-04-01",
                "training_observation_count": 12,
                "validation_observation_count": 4,
                "horizon": 20,
                "benchmark_symbol": "SPY",
                "window_type": "rolling",
                "training_metrics": {"all_candidates": {"average_excess_return": 0.01}},
                "validation_metrics": {"all_candidates": {"average_excess_return": 0.02}},
                "degradation_metrics": {"average_excess_return": {"validation_degradation": 0.01}},
                "factor_stability": [{"factor": "trend_score"}],
                "regime_metrics": [{"market_regime": "bull"}],
                "warnings": [],
                "status": "completed",
                "created_at": "2024-06-01T00:00:00+00:00",
            }
        ],
    )


def test_repository_inserts_and_reads_walk_forward_runs(tmp_path):
    repo = MonitoringWalkForwardRepository(database_url=f"sqlite:///{tmp_path / 'walk_forward.db'}")
    result = repo.save_run(_payload())
    assert result["run_id"] == "wf-1"
    assert repo.count_runs() == 1
    run = repo.fetch_run("wf-1")
    windows = repo.fetch_windows_for_run("wf-1")
    assert run["scorecard"]["overall_validation_status"] == "acceptable"
    assert run["factor_stability_summary"][0]["factor"] == "trend_score"
    assert windows[0]["validation_metrics"]["all_candidates"]["average_excess_return"] == 0.02
    assert repo.fetch_latest_run()["run_id"] == "wf-1"
    repo.close()


def test_repository_rollback_prevents_partial_persistence(tmp_path):
    repo = MonitoringWalkForwardRepository(database_url=f"sqlite:///{tmp_path / 'walk_forward.db'}")
    payload = _payload()
    payload.windows[0]["window_id"] = None
    with pytest.raises(Exception):
        repo.save_run(payload)
    assert repo.count_runs() == 0
    repo.close()


def test_walk_forward_migration_tables_and_indexes_exist(tmp_path):
    repo = MonitoringWalkForwardRepository(database_url=f"sqlite:///{tmp_path / 'walk_forward.db'}")
    repo.db.ensure_schema()
    tables = {row["name"] for row in repo.db.query_all("SELECT name FROM sqlite_master WHERE type = 'table'")}
    indexes = {row["name"] for row in repo.db.query_all("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert "walk_forward_runs" in tables
    assert "walk_forward_windows" in tables
    assert "idx_walk_forward_runs_created_at" in indexes
    assert "idx_walk_forward_windows_run_id" in indexes
    repo.close()