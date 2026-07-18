from monitoring_db import MonitoringDatabase
from strategy_lab_repository import MonitoringStrategyLabRepository, StrategyLabRunPayload


def test_strategy_lab_repository_save_and_fetch(tmp_path):
    db_path = tmp_path / "strategy_lab.db"
    database_url = f"sqlite:///{db_path}"
    db = MonitoringDatabase(database_url=database_url)
    db.ensure_schema()
    repo = MonitoringStrategyLabRepository(database_url=database_url)
    payload = StrategyLabRunPayload(
        definitions=[
            {
                "strategy_id": "baseline_scanner",
                "strategy_name": "Baseline Scanner",
                "version": "1",
                "description": "Baseline",
                "configuration_fingerprint": "fp",
                "enabled": True,
                "configuration": {},
                "created_at": "2024-01-01T00:00:00+00:00",
            }
        ],
        run={
            "run_id": "run-1",
            "created_at": "2024-01-01T00:00:00+00:00",
            "horizon": 20,
            "benchmark": "SPY",
            "comparison_mode": "common_snapshots",
            "start_date": None,
            "end_date": None,
            "strategy_ids": ["baseline_scanner"],
            "portfolio_configuration": {},
            "transaction_cost_configuration": {},
            "status": "completed",
            "duration_seconds": 0.1,
            "error_message": None,
            "summary": {},
            "performance": {},
        },
        results=[
            {
                "strategy_id": "baseline_scanner",
                "eligible_candidate_count": 10,
                "snapshot_count": 2,
                "completed_count": 2,
                "skipped_count": 0,
                "analytics": {"average_net_excess_return": 0.01},
                "scorecard": {"composite_score": 60},
                "walk_forward": {},
                "regime": [],
                "factor_exposure": {},
                "warnings": [],
            }
        ],
        pairwise=[],
    )
    repo.save_run(payload)
    latest = repo.fetch_latest_run()
    assert latest is not None
    assert latest["run_id"] == "run-1"
    assert repo.count_runs() == 1
    rows = repo.fetch_results_for_run("run-1")
    assert len(rows) == 1
    repo.close()
