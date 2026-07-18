from __future__ import annotations

from evaluation_repository import EvaluationPersistencePayload, MonitoringEvaluationRepository
from research_journal import journal_scanner_run
from research_repository import MonitoringResearchRepository
from strategy_evaluator import evaluate_strategy_performance


def _seed_candidate(db_url: str, research_run_id: str, symbol: str, created_at: str, latest_price: float, sector: str = "Technology", regime: str = "strong_bull") -> int:
    payload = {
        "summary": {
            "started_at": created_at,
            "scan_started_at": created_at,
            "completed_at": created_at,
            "benchmark_symbol": "SPY",
            "market_regime": regime,
            "symbol_count": 1,
            "eligible_count": 1,
            "rejection_count": 0,
            "error_count": 0,
            "duration_seconds": 0.1,
            "status": "completed",
        },
        "scan_results": [
            {
                "symbol": symbol,
                "company_name": symbol,
                "sector": sector,
                "industry": "Software",
                "scan_timestamp": created_at,
                "latest_price": latest_price,
                "average_dollar_volume": 5_000_000,
                "overall_score": 80.0,
                "confidence": 70.0,
                "signal": "BUY",
                "regime": regime,
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
    journal_scanner_run(payload, research_run_id=research_run_id, database_url=db_url, data_source="synthetic", data_mode="research")
    research_repo = MonitoringResearchRepository(database_url=db_url)
    candidate_id = research_repo.fetch_research_candidates_for_run(research_run_id)[0]["id"]
    research_repo.db.close()
    return candidate_id


def _complete_record(candidate_id: int, research_run_id: str, symbol: str, score: float, confidence: float, raw_return: float):
    record = {
        "research_candidate_id": candidate_id,
        "research_run_id": research_run_id,
        "symbol": symbol,
        "observation_date": "2024-01-02",
        "observation_price": 100.0,
        "benchmark_symbol": "SPY",
        "benchmark_observation_price": 100.0,
        "label_status": "complete",
        "data_source": "market_data",
        "last_attempted_at": "2024-01-10T15:00:00+00:00",
        "completed_at": "2024-01-10T15:00:00+00:00",
        "error_message": None,
        "created_at": "2024-01-10T15:00:00+00:00",
        "updated_at": "2024-01-10T15:00:00+00:00",
        "overall_score": score,
        "confidence": confidence,
    }
    for horizon, value in [(1, raw_return), (5, raw_return), (10, raw_return), (20, raw_return)]:
        prefix = f"forward_{horizon}d"
        record[f"{prefix}_target_date"] = "2024-01-03"
        record[f"{prefix}_actual_date"] = "2024-01-03"
        record[f"{prefix}_future_price"] = 100.0 * (1.0 + value)
        record[f"{prefix}_benchmark_future_price"] = 100.0
        record[f"{prefix}_return"] = value
        record[f"{prefix}_benchmark_return"] = 0.0
        record[f"{prefix}_excess_return"] = value
        record[f"{prefix}_status"] = "complete"
    return record


def test_strategy_evaluator_returns_read_only_analytics(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'strategy.db'}"
    candidate_id = _seed_candidate(db_url, "research-1", "AAA", "2024-01-02T15:00:00+00:00", 100.0)
    repo = MonitoringEvaluationRepository(database_url=db_url)
    repo.save_evaluations(EvaluationPersistencePayload(records=[_complete_record(candidate_id, "research-1", "AAA", 80.0, 70.0, 0.10)]))
    repo.close()

    payload = evaluate_strategy_performance(database_url=db_url)
    assert payload["evaluation_analytics"]["total_observations"] == 1
    assert payload["evaluation_analytics"]["horizons"]["1d"]["sample_size"] == 1
    assert payload["latest_labeling_run"] is not None


def test_strategy_evaluator_filters_by_run_and_symbol(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'strategy.db'}"
    candidate_a = _seed_candidate(db_url, "research-a", "AAA", "2024-01-02T15:00:00+00:00", 100.0)
    candidate_b = _seed_candidate(db_url, "research-b", "BBB", "2024-01-03T15:00:00+00:00", 100.0, sector="Healthcare", regime="bear")
    repo = MonitoringEvaluationRepository(database_url=db_url)
    repo.save_evaluations(
        EvaluationPersistencePayload(
            records=[
                _complete_record(candidate_a, "research-a", "AAA", 80.0, 70.0, 0.05),
                _complete_record(candidate_b, "research-b", "BBB", 90.0, 80.0, 0.20),
            ]
        )
    )
    repo.close()

    run_payload = evaluate_strategy_performance(database_url=db_url, selected_run_id="research-b")
    symbol_payload = evaluate_strategy_performance(database_url=db_url, selected_symbol="AAA")

    assert run_payload["evaluation_analytics"]["total_observations"] == 1
    assert run_payload["evaluation_analytics"]["sector_analysis"]["1d"][0]["sector"] == "Healthcare"
    assert symbol_payload["evaluation_analytics"]["total_observations"] == 1
    assert symbol_payload["evaluation_analytics"]["sector_analysis"]["1d"][0]["sector"] == "Technology"