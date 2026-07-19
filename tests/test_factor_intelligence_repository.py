from factor_intelligence_repository import FactorIntelligenceRepository, FactorIntelligenceRunPayload


def test_run_persistence_and_retrieval(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'fi.db'}"
    repo = FactorIntelligenceRepository(database_url=db_url)
    repo.db.ensure_schema()
    run = {
        "run_id": "r1",
        "run_fingerprint": "fp1",
        "attempt_id": "a1",
        "started_at": "2024-01-01T00:00:00+00:00",
        "status": "running",
        "analysis_start_date": "2024-01-01",
        "analysis_end_date": "2024-01-31",
        "forward_horizon": 20,
        "configuration": {},
        "timings": {},
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
    }
    repo.create_run(run)
    repo.save_results(
        FactorIntelligenceRunPayload(
            run=run,
            predictive_stats=[],
            bucket_stats=[],
            stability_results=[],
            regime_stats=[],
            redundancy_stats=[],
            scorecards=[],
        )
    )
    repo.update_run_status("r1", "completed", completed_at="2024-01-01T00:01:00+00:00", timings={})
    latest = repo.latest_completed_run()
    assert latest["run_id"] == "r1"
    repo.close()
