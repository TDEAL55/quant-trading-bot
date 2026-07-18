from __future__ import annotations

from pathlib import Path

import pytest

from evaluation_repository import EvaluationPersistencePayload, MonitoringEvaluationRepository
from research_journal import journal_scanner_run
from research_repository import MonitoringResearchRepository


REPO_ROOT = Path(__file__).resolve().parents[1]


def _seed_candidate(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'evaluation.db'}"
    created_at = "2024-01-02T15:00:00+00:00"
    payload = {
        "summary": {
            "started_at": created_at,
            "scan_started_at": created_at,
            "completed_at": created_at,
            "benchmark_symbol": "SPY",
            "market_regime": "strong_bull",
            "symbol_count": 1,
            "eligible_count": 1,
            "rejection_count": 0,
            "error_count": 0,
            "duration_seconds": 0.1,
            "status": "completed",
        },
        "scan_results": [
            {
                "symbol": "AAA",
                "company_name": "AAA",
                "sector": "Technology",
                "industry": "Software",
                "scan_timestamp": created_at,
                "latest_price": 100.0,
                "average_dollar_volume": 5_000_000,
                "overall_score": 80.0,
                "confidence": 70.0,
                "signal": "BUY",
                "regime": "strong_bull",
                "component_scores": {"trend": 80.0, "momentum": 70.0, "volume": 60.0, "volatility": 50.0, "market_regime": 65.0, "risk_quality": 60.0},
                "reasons": ["trend confirmed"],
                "warnings": [],
                "data_quality": {"history_sufficient": True, "factor": {}},
                "eligible": True,
                "rejection_reasons": [],
                "rank": 1,
                "ranking_score": 90.0,
                "status": "scored",
            }
        ],
        "ranked_candidates": [],
    }
    journal_scanner_run(payload, research_run_id="research-1", database_url=db_url, data_source="synthetic", data_mode="research")
    research_repo = MonitoringResearchRepository(database_url=db_url)
    candidate_id = research_repo.fetch_research_candidates_for_run("research-1")[0]["id"]
    research_repo.db.close()
    return db_url, candidate_id


def _complete_record(candidate_id: int, status: str = "complete") -> dict[str, object]:
    record = {
        "research_candidate_id": candidate_id,
        "research_run_id": "research-1",
        "symbol": "AAA",
        "observation_date": "2024-01-02",
        "observation_price": 100.0,
        "benchmark_symbol": "SPY",
        "benchmark_observation_price": 100.0,
        "label_status": status,
        "data_source": "market_data",
        "last_attempted_at": "2024-01-10T15:00:00+00:00",
        "completed_at": "2024-01-10T15:00:00+00:00",
        "error_message": None,
        "created_at": "2024-01-10T15:00:00+00:00",
        "updated_at": "2024-01-10T15:00:00+00:00",
    }
    for horizon, value in [(1, 0.01), (5, 0.05), (10, 0.10), (20, 0.20)]:
        prefix = f"forward_{horizon}d"
        record[f"{prefix}_target_date"] = "2024-01-03"
        record[f"{prefix}_actual_date"] = "2024-01-03"
        record[f"{prefix}_future_price"] = 100.0 * (1.0 + value)
        record[f"{prefix}_benchmark_future_price"] = 100.0
        record[f"{prefix}_return"] = value
        record[f"{prefix}_benchmark_return"] = 0.0
        record[f"{prefix}_excess_return"] = value
        record[f"{prefix}_status"] = status
    return record


def test_repository_inserts_updates_and_reads_evaluation_data(tmp_path):
    db_url, candidate_id = _seed_candidate(tmp_path)
    repo = MonitoringEvaluationRepository(database_url=db_url)
    repo.save_evaluations(EvaluationPersistencePayload(records=[_complete_record(candidate_id)]))

    assert repo.count_total_evaluations() == 1
    assert repo.fetch_evaluation_by_candidate_id(candidate_id)["label_status"] == "complete"
    assert repo.fetch_evaluations_by_research_run("research-1")[0]["symbol"] == "AAA"
    assert repo.fetch_evaluations_by_symbol("AAA")[0]["research_candidate_id"] == candidate_id
    assert repo.fetch_evaluations_by_status("complete")[0]["research_candidate_id"] == candidate_id
    assert repo.fetch_completed_horizon_data(1)[0]["forward_1d_excess_return"] == pytest.approx(0.01, rel=1e-6)

    partial = _complete_record(candidate_id, status="partial")
    partial["forward_1d_status"] = "complete"
    partial["forward_5d_status"] = "pending"
    partial["forward_10d_status"] = "pending"
    partial["forward_20d_status"] = "pending"
    partial["forward_1d_return"] = 0.01
    partial["forward_1d_benchmark_return"] = 0.0
    partial["forward_1d_excess_return"] = 0.01
    partial["forward_5d_return"] = None
    partial["forward_10d_return"] = None
    partial["forward_20d_return"] = None
    repo.save_evaluations(EvaluationPersistencePayload(records=[partial]))

    row = repo.fetch_evaluation_by_candidate_id(candidate_id)
    assert row["label_status"] == "partial"
    assert row["forward_1d_status"] == "complete"
    assert row["forward_5d_status"] == "pending"
    repo.close()


def test_repository_rollback_prevents_partial_persistence(tmp_path):
    db_url, candidate_id = _seed_candidate(tmp_path)
    repo = MonitoringEvaluationRepository(database_url=db_url)
    bad_record = _complete_record(candidate_id)
    bad_record["research_candidate_id"] = None

    with pytest.raises(Exception):
        repo.save_evaluations(EvaluationPersistencePayload(records=[bad_record]))

    assert repo.count_total_evaluations() == 0
    repo.close()


def test_research_evaluation_migration_indexes_exist(tmp_path):
    db_url, _ = _seed_candidate(tmp_path)
    repo = MonitoringEvaluationRepository(database_url=db_url)
    repo.db.ensure_schema()

    tables = {row["name"] for row in repo.db.query_all("SELECT name FROM sqlite_master WHERE type = 'table'")}
    indexes = {row["name"] for row in repo.db.query_all("SELECT name FROM sqlite_master WHERE type = 'index'")}

    assert "strategy_evaluations" in tables
    assert "idx_strategy_evaluations_research_run_id" in indexes
    assert "idx_strategy_evaluations_forward_1d_excess_return" in indexes
    assert "idx_strategy_evaluations_forward_20d_excess_return" in indexes
    repo.close()