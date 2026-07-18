from pathlib import Path

import pytest

from research_repository import MonitoringResearchRepository, ResearchPersistencePayload


def _payload():
    run = {
        "research_run_id": "research-1",
        "started_at": "2026-07-17T10:00:00+00:00",
        "completed_at": "2026-07-17T10:01:00+00:00",
        "scanner_version": "sprint2-scanner-v1",
        "strategy_version": "multi_factor-v1",
        "benchmark_symbol": "SPY",
        "market_regime": "strong_bull",
        "universe_size": 2,
        "scanned_count": 2,
        "eligible_count": 1,
        "rejected_count": 1,
        "error_count": 0,
        "average_overall_score": 75.0,
        "average_confidence": 65.0,
        "scanner_duration_seconds": 1.0,
        "data_source": "synthetic",
        "data_mode": "research",
        "scanner_config": {"scanner_version": "sprint2-scanner-v1"},
        "factor_weights": {"trend": 0.3},
        "scanner_summary": {"symbol_count": 2},
        "status": "completed",
    }
    candidates = [
        {
            "research_run_id": "research-1",
            "symbol": "NVDA",
            "company_name": "NVIDIA Corporation",
            "rank": 1,
            "overall_score": 84.2,
            "confidence": 72.1,
            "signal": "STRONG_BUY",
            "market_regime": "strong_bull",
            "sector": "Technology",
            "industry": "Semiconductors",
            "latest_price": 125.5,
            "average_dollar_volume": 627500000.0,
            "liquidity_score": 91.2,
            "trend_score": 82.0,
            "momentum_score": 70.0,
            "volume_score": 65.0,
            "volatility_score": 55.0,
            "market_regime_score": 88.0,
            "risk_quality_score": 74.0,
            "rejection_status": "ELIGIBLE",
            "rejection_reasons": [],
            "strategy_reasons": ["trend strength"],
            "factor_breakdown": {"component_scores": {"trend": 82.0}},
            "ranking_score": 80.1,
            "created_at": "2026-07-17T10:00:00+00:00",
        },
        {
            "research_run_id": "research-1",
            "symbol": "AMD",
            "company_name": "AMD",
            "rank": None,
            "overall_score": 50.5,
            "confidence": 41.0,
            "signal": "HOLD",
            "market_regime": "weak_bull",
            "sector": "Technology",
            "industry": "Semiconductors",
            "latest_price": 115.0,
            "average_dollar_volume": 12000000.0,
            "liquidity_score": 12.0,
            "trend_score": 45.0,
            "momentum_score": 40.0,
            "volume_score": 30.0,
            "volatility_score": 55.0,
            "market_regime_score": 50.0,
            "risk_quality_score": 44.0,
            "rejection_status": "REJECTED",
            "rejection_reasons": ["score below threshold"],
            "strategy_reasons": ["insufficient momentum"],
            "factor_breakdown": {"component_scores": {"trend": 45.0}},
            "ranking_score": None,
            "created_at": "2026-07-17T10:00:00+00:00",
        },
    ]
    return ResearchPersistencePayload(run=run, candidates=candidates)


def test_repository_inserts_and_reads_research_data(tmp_path):
    repo = MonitoringResearchRepository(database_url=f"sqlite:///{tmp_path / 'research.db'}")
    repo.db.ensure_schema()
    repo.save_research(_payload())

    assert repo.count_total_research_runs() == 1
    assert repo.count_total_candidate_observations() == 2
    assert repo.fetch_latest_research_run()["research_run_id"] == "research-1"
    assert repo.fetch_recent_research_runs(limit=5)[0]["research_run_id"] == "research-1"
    assert [row["symbol"] for row in repo.fetch_research_candidates_for_run("research-1")] == ["NVDA", "AMD"]
    assert [row["symbol"] for row in repo.fetch_candidates_by_symbol("NVDA")] == ["NVDA"]
    assert [row["symbol"] for row in repo.fetch_candidates_by_sector("Technology")] == ["NVDA", "AMD"]
    assert [row["symbol"] for row in repo.fetch_candidates_by_regime("strong_bull")] == ["NVDA"]
    assert [row["symbol"] for row in repo.fetch_candidates_by_score_range(80, 100)] == ["NVDA"]
    assert [row["symbol"] for row in repo.fetch_candidates_by_confidence_range(70, 100)] == ["NVDA"]
    assert repo.fetch_research_run_by_id("research-1")["benchmark_symbol"] == "SPY"
    assert repo.fetch_highest_ranked_candidates_across_stored_runs(limit=5)[0]["symbol"] == "NVDA"


def test_repository_rollback_prevents_partial_persistence(tmp_path):
    repo = MonitoringResearchRepository(database_url=f"sqlite:///{tmp_path / 'research.db'}")
    repo.db.ensure_schema()
    payload = _payload()
    payload.candidates[0]["symbol"] = None

    with pytest.raises(Exception):
        repo.save_research(payload)

    assert repo.count_total_research_runs() == 0
    assert repo.count_total_candidate_observations() == 0


def test_research_migration_tables_and_indexes_exist(tmp_path):
    repo = MonitoringResearchRepository(database_url=f"sqlite:///{tmp_path / 'research.db'}")
    repo.db.ensure_schema()

    tables = {row["name"] for row in repo.db.query_all("SELECT name FROM sqlite_master WHERE type = 'table'")}
    indexes = {row["name"] for row in repo.db.query_all("SELECT name FROM sqlite_master WHERE type = 'index'")}

    assert "research_runs" in tables
    assert "research_candidates" in tables
    assert "idx_research_runs_started_at" in indexes
    assert "idx_research_candidates_symbol" in indexes
    assert "idx_research_candidates_rank" in indexes
