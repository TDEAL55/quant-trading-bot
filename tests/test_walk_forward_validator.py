from __future__ import annotations

from evaluation_repository import EvaluationPersistencePayload, MonitoringEvaluationRepository
from research_journal import journal_scanner_run
from research_repository import MonitoringResearchRepository
from walk_forward_data import fetch_walk_forward_dashboard_payload
from walk_forward_validator import WalkForwardValidator


def _seed_candidate(db_url: str, research_run_id: str, symbol: str, created_at: str, score: float, confidence: float, trend: float, regime: str, sector: str, rank: int) -> int:
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
                "latest_price": 100.0,
                "average_dollar_volume": 5_000_000,
                "overall_score": score,
                "confidence": confidence,
                "signal": "BUY",
                "regime": regime,
                "component_scores": {"trend": trend, "momentum": trend, "volume": trend, "volatility": 60.0, "market_regime": 60.0, "risk_quality": 60.0},
                "reasons": ["trend confirmed"],
                "warnings": [],
                "data_quality": {"history_sufficient": True, "factor": {}},
                "eligible": True,
                "rejection_reasons": [],
                "rank": rank,
                "ranking_score": score,
                "status": "scored",
            }
        ],
        "ranked_candidates": [],
    }
    journal_scanner_run(payload, research_run_id=research_run_id, database_url=db_url, data_source="synthetic", data_mode="research")
    repo = MonitoringResearchRepository(database_url=db_url)
    candidate_id = repo.fetch_research_candidates_for_run(research_run_id)[0]["id"]
    repo.db.close()
    return candidate_id


def _evaluation_record(candidate_id: int, research_run_id: str, symbol: str, observation_date: str, one_day: float, five_day: float, ten_day: float, twenty_day: float, excess_one_day: float, excess_five_day: float, excess_ten_day: float, excess_twenty_day: float):
    record = {
        "research_candidate_id": candidate_id,
        "research_run_id": research_run_id,
        "symbol": symbol,
        "observation_date": observation_date,
        "observation_price": 100.0,
        "benchmark_symbol": "SPY",
        "benchmark_observation_price": 100.0,
        "label_status": "complete",
        "data_source": "market_data",
        "last_attempted_at": observation_date + "T15:00:00+00:00",
        "completed_at": observation_date + "T15:00:00+00:00",
        "error_message": None,
        "created_at": observation_date + "T15:00:00+00:00",
        "updated_at": observation_date + "T15:00:00+00:00",
    }
    for horizon, raw, excess in [(1, one_day, excess_one_day), (5, five_day, excess_five_day), (10, ten_day, excess_ten_day), (20, twenty_day, excess_twenty_day)]:
        prefix = f"forward_{horizon}d"
        record[f"{prefix}_target_date"] = observation_date
        record[f"{prefix}_actual_date"] = observation_date
        record[f"{prefix}_future_price"] = 100.0 * (1.0 + raw)
        record[f"{prefix}_benchmark_future_price"] = 100.0 * (1.0 + (raw - excess))
        record[f"{prefix}_return"] = raw
        record[f"{prefix}_benchmark_return"] = raw - excess
        record[f"{prefix}_excess_return"] = excess
        record[f"{prefix}_status"] = "complete"
    return record


def test_walk_forward_validator_integrates_evaluation_rows_and_persistence(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'walk_forward_validator.db'}"
    candidate_rows = [
        ("research-1", "AAA", "2024-01-05", 80.0, 75.0, 80.0, "bull", "Tech", 1, 0.02, 0.03, 0.04, 0.05, 0.01, 0.01, 0.015, 0.02),
        ("research-2", "BBB", "2024-02-06", 70.0, 65.0, 70.0, "bull", "Tech", 2, 0.01, 0.02, 0.03, 0.04, 0.005, 0.01, 0.015, 0.02),
        ("research-3", "CCC", "2024-03-07", 60.0, 55.0, 60.0, "neutral", "Health", 3, 0.00, 0.01, 0.02, 0.03, 0.0, 0.005, 0.01, 0.015),
        ("research-4", "DDD", "2024-04-08", 50.0, 50.0, 50.0, "bear", "Energy", 4, -0.01, 0.00, 0.01, 0.02, -0.005, 0.0, 0.005, 0.01),
        ("research-5", "EEE", "2024-05-09", 40.0, 45.0, 40.0, "bear", "Energy", 5, -0.02, -0.01, 0.00, 0.01, -0.01, -0.005, 0.0, 0.005),
    ]
    repo = MonitoringEvaluationRepository(database_url=db_url)
    records = []
    for item in candidate_rows:
        candidate_id = _seed_candidate(db_url, item[0], item[1], item[2] + "T15:00:00+00:00", item[3], item[4], item[5], item[6], item[7], item[8])
        records.append(_evaluation_record(candidate_id, item[0], item[1], item[2], item[9], item[10], item[11], item[12], item[13], item[14], item[15], item[16]))
    repo.save_evaluations(EvaluationPersistencePayload(records=records))
    repo.close()

    result = WalkForwardValidator(database_url=db_url).validate(horizon=20, window_type="rolling", training_periods=3, validation_periods=1, step_periods=1, min_training_sample=2, min_validation_sample=1, persist=True)
    assert result["run"]["total_windows"] == 2
    assert result["run"]["completed_windows"] == 2
    assert result["scorecard"]["overall_validation_status"] in {"strong", "acceptable", "unstable", "insufficient_data"}
    assert result["factor_stability_summary"]
    payload = fetch_walk_forward_dashboard_payload(db_url)
    assert payload["total_validation_runs"] == 1
    assert payload["latest_run"]["run_id"] == result["run"]["run_id"]
    assert len(payload["windows"]) == 2


def test_walk_forward_validator_supports_expanding_windows_and_dry_run(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'walk_forward_validator.db'}"
    repo = MonitoringEvaluationRepository(database_url=db_url)
    records = []
    for index, month in enumerate(["2024-01-05", "2024-02-06", "2024-03-07", "2024-04-08"], start=1):
        candidate_id = _seed_candidate(db_url, f"research-{index}", f"SYM{index}", month + "T15:00:00+00:00", 60.0 + index, 55.0 + index, 60.0 + index, "bull", "Tech", index)
        records.append(_evaluation_record(candidate_id, f"research-{index}", f"SYM{index}", month, 0.01 * index, 0.01 * index, 0.01 * index, 0.01 * index, 0.005 * index, 0.005 * index, 0.005 * index, 0.005 * index))
    repo.save_evaluations(EvaluationPersistencePayload(records=records))
    repo.close()

    result = WalkForwardValidator(database_url=db_url).validate(horizon=20, window_type="expanding", training_periods=2, validation_periods=1, step_periods=1, min_training_sample=1, min_validation_sample=1, dry_run=True, persist=False)
    assert result["run"]["window_type"] == "expanding"
    assert result["persistence"]["storage"] == "dry_run"
    assert result["windows"][0]["training_start_date"] == "2024-01-01"
    assert result["windows"][1]["training_start_date"] == "2024-01-01"