from evaluation_repository import EvaluationPersistencePayload, MonitoringEvaluationRepository
from portfolio_research import run_portfolio_research
from portfolio_research_data import fetch_portfolio_research_dashboard_payload
from research_journal import journal_scanner_run
from research_repository import MonitoringResearchRepository


def _seed_candidate(db_url: str, research_run_id: str, symbol: str, created_at: str, score: float, confidence: float, regime: str, sector: str, rank: int) -> int:
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
                "component_scores": {"trend": score, "momentum": score, "volume": score, "volatility": 60.0, "market_regime": 60.0, "risk_quality": 60.0},
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


def _evaluation_record(candidate_id: int, research_run_id: str, symbol: str, observation_date: str, raw: float, bench: float):
    excess = raw - bench
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
    for horizon in [1, 5, 10, 20]:
        prefix = f"forward_{horizon}d"
        record[f"{prefix}_target_date"] = observation_date
        record[f"{prefix}_actual_date"] = observation_date
        record[f"{prefix}_future_price"] = 100.0 * (1.0 + raw)
        record[f"{prefix}_benchmark_future_price"] = 100.0 * (1.0 + bench)
        record[f"{prefix}_return"] = raw
        record[f"{prefix}_benchmark_return"] = bench
        record[f"{prefix}_excess_return"] = excess
        record[f"{prefix}_status"] = "complete"
    return record


def test_portfolio_research_run_and_dashboard_payload(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'portfolio_research.db'}"
    repo = MonitoringEvaluationRepository(database_url=db_url)
    records = []
    fixture_rows = [
        ("research-1", "AAA", "2024-01-05", 80.0, 75.0, "bull", "Tech", 1, 0.05, 0.02),
        ("research-1", "BBB", "2024-01-05", 60.0, 55.0, "bull", "Energy", 2, 0.01, 0.00),
        ("research-2", "AAA", "2024-02-06", 78.0, 74.0, "neutral", "Tech", 1, 0.04, 0.01),
        ("research-2", "CCC", "2024-02-06", 58.0, 52.0, "neutral", "Health", 2, -0.02, -0.01),
        ("research-3", "DDD", "2024-03-07", 65.0, 60.0, "bear", "Energy", 1, 0.03, 0.01),
        ("research-3", "EEE", "2024-03-07", 55.0, 50.0, "bear", "Tech", 2, -0.01, -0.02),
        ("research-4", "FFF", "2024-04-08", 82.0, 78.0, "bull", "Tech", 1, 0.06, 0.02),
        ("research-4", "GGG", "2024-04-08", 62.0, 59.0, "bull", "Utilities", 2, 0.01, 0.00),
    ]
    for row in fixture_rows:
        cid = _seed_candidate(db_url, row[0], row[1], row[2] + "T15:00:00+00:00", row[3], row[4], row[5], row[6], row[7])
        records.append(_evaluation_record(cid, row[0], row[1], row[2], row[8], row[9]))
    repo.save_evaluations(EvaluationPersistencePayload(records=records))
    repo.close()

    result = run_portfolio_research(
        database_url=db_url,
        horizon=20,
        weighting_method="equal_weight",
        top_n=2,
        methods=["equal_weight", "score_proportional", "confidence_proportional", "rank_based", "inverse_volatility"],
        persist=True,
    )
    assert result["run"]["portfolio_count"] >= 4
    assert result["comparison"]["comparison_table"]
    assert result["walk_forward"]["windows"]
    assert result["persistence"]["storage"] == "database"

    payload = fetch_portfolio_research_dashboard_payload(db_url)
    assert payload["total_runs"] == 1
    assert payload["latest_run"]["run_id"] == result["run"]["run_id"]
    assert len(payload["snapshots"]) >= 4
