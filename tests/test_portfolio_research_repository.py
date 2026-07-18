from portfolio_research_repository import MonitoringPortfolioResearchRepository, PortfolioResearchRunPayload


def _run_payload():
    return {
        "run_id": "portfolio-run-1",
        "created_at": "2024-01-10T15:00:00+00:00",
        "horizon": 20,
        "weighting_method": "equal_weight",
        "top_n": 5,
        "maximum_position_weight": 0.3,
        "sector_cap": 0.5,
        "target_volatility": 0.15,
        "benchmark": "SPY",
        "start_date": "2024-01-01",
        "end_date": "2024-06-30",
        "configuration": {"allow_cash": True},
        "portfolio_count": 2,
        "completed_count": 2,
        "skipped_count": 0,
        "status": "completed",
        "duration_seconds": 0.5,
        "performance": {"total_duration": 0.5},
        "analytics": {"average_portfolio_excess_return": 0.01},
        "method_comparison": [{"method": "equal_weight", "average_excess_return": 0.01}],
        "walk_forward": {"windows": []},
        "warnings": [],
    }


def _snapshots():
    return [
        {
            "snapshot_id": "equal_weight-1",
            "research_run_id": "research-1",
            "formation_date": "2024-01-05",
            "horizon": 20,
            "weighting_method": "equal_weight",
            "holding_count": 2,
            "invested_weight": 1.0,
            "cash_weight": 0.0,
            "portfolio_return": 0.02,
            "benchmark_return": 0.01,
            "excess_return": 0.01,
            "turnover": None,
            "concentration_metrics": {"hhi": 0.5},
            "sector_exposure": {"Tech": 0.5, "Energy": 0.5},
            "holdings": [{"symbol": "AAA", "weight": 0.5}, {"symbol": "BBB", "weight": 0.5}],
            "symbol_contribution": [{"symbol": "AAA", "raw_contribution": 0.01}],
            "sector_contribution": [{"sector": "Tech", "raw_contribution": 0.01}],
            "signal_contribution": [{"signal": "BUY", "raw_contribution": 0.01}],
            "regime_contribution": [{"market_regime": "bull", "raw_contribution": 0.01}],
            "warnings": [],
            "status": "completed",
            "created_at": "2024-01-10T15:00:00+00:00",
        }
    ]


def test_repository_insert_fetch_latest_and_snapshots(tmp_path):
    repo = MonitoringPortfolioResearchRepository(database_url=f"sqlite:///{tmp_path / 'portfolio_research.db'}")
    saved = repo.save_run(PortfolioResearchRunPayload(run=_run_payload(), snapshots=_snapshots()))
    assert saved["storage"] == "database"

    latest = repo.fetch_latest_run()
    assert latest is not None
    assert latest["run_id"] == "portfolio-run-1"
    assert latest["analytics"]["average_portfolio_excess_return"] == 0.01

    snapshots = repo.fetch_snapshots_for_run("portfolio-run-1")
    assert len(snapshots) == 1
    assert snapshots[0]["concentration_metrics"]["hhi"] == 0.5
    repo.close()


def test_repository_duplicate_snapshot_protection_and_indexes(tmp_path):
    repo = MonitoringPortfolioResearchRepository(database_url=f"sqlite:///{tmp_path / 'portfolio_research.db'}")
    repo.db.ensure_schema()

    repo.save_run(PortfolioResearchRunPayload(run=_run_payload(), snapshots=_snapshots()))
    dupe = _snapshots() + _snapshots()
    repo.save_run(PortfolioResearchRunPayload(run=_run_payload(), snapshots=dupe[:1]))
    snapshots = repo.fetch_snapshots_for_run("portfolio-run-1")
    assert len(snapshots) == 1

    tables = {row["name"] for row in repo.db.query_all("SELECT name FROM sqlite_master WHERE type='table'")}
    indexes = {row["name"] for row in repo.db.query_all("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "portfolio_research_runs" in tables
    assert "portfolio_research_snapshots" in tables
    assert "idx_portfolio_research_runs_created_at" in indexes
    assert "idx_portfolio_research_snapshots_run_id" in indexes
    repo.close()
