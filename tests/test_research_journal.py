from copy import deepcopy

import pytest

from research_journal import journal_scanner_run
from research_repository import MonitoringResearchRepository


def _scanner_payload():
    return {
        "summary": {
            "symbol_count": 2,
            "eligible_count": 1,
            "rejection_count": 1,
            "error_count": 0,
            "duration_seconds": 1.2345,
            "benchmark_symbol": "SPY",
            "market_regime": "strong_bull",
        },
        "ranked_candidates": [
            {
                "symbol": "NVDA",
                "company_name": "NVIDIA Corporation",
                "rank": 1,
                "overall_score": 84.2,
                "confidence": 72.1,
                "signal": "STRONG_BUY",
                "regime": "strong_bull",
                "sector": "Technology",
                "industry": "Semiconductors",
                "latest_price": 125.5,
                "average_dollar_volume": 627500000.0,
                "liquidity_score": 91.2,
                "component_scores": {
                    "trend": 82.0,
                    "momentum": 70.0,
                    "volume": 65.0,
                    "volatility": 55.0,
                    "market_regime": 88.0,
                    "risk_quality": 74.0,
                },
                "reasons": ["trend strength"],
                "warnings": ["extended"],
                "data_quality": {"filter": {"passed": True}, "factor": {"history_sufficient": True}},
                "eligible": True,
                "rejection_reasons": [],
                "ranking_score": 80.1,
                "scan_timestamp": "2026-07-17T10:00:00+00:00",
            }
        ],
        "scan_results": [
            {
                "symbol": "NVDA",
                "company_name": "NVIDIA Corporation",
                "rank": 1,
                "overall_score": 84.2,
                "confidence": 72.1,
                "signal": "STRONG_BUY",
                "regime": "strong_bull",
                "sector": "Technology",
                "industry": "Semiconductors",
                "latest_price": 125.5,
                "average_dollar_volume": 627500000.0,
                "liquidity_score": 91.2,
                "component_scores": {
                    "trend": 82.0,
                    "momentum": 70.0,
                    "volume": 65.0,
                    "volatility": 55.0,
                    "market_regime": 88.0,
                    "risk_quality": 74.0,
                },
                "reasons": ["trend strength"],
                "warnings": ["extended"],
                "data_quality": {"filter": {"passed": True}, "factor": {"history_sufficient": True}},
                "eligible": True,
                "rejection_reasons": [],
                "ranking_score": 80.1,
                "scan_timestamp": "2026-07-17T10:00:00+00:00",
            },
            {
                "symbol": "AMD",
                "company_name": None,
                "rank": None,
                "overall_score": 50.5,
                "confidence": 41.0,
                "signal": "HOLD",
                "regime": "weak_bull",
                "sector": "Technology",
                "industry": "Semiconductors",
                "latest_price": 115.0,
                "average_dollar_volume": 12000000.0,
                "liquidity_score": 12.0,
                "component_scores": {
                    "trend": 45.0,
                    "momentum": 40.0,
                    "volume": 30.0,
                    "volatility": 55.0,
                    "market_regime": 50.0,
                    "risk_quality": 44.0,
                },
                "reasons": ["insufficient momentum"],
                "warnings": [],
                "data_quality": {"filter": {"passed": True}, "factor": {"history_sufficient": True}},
                "eligible": False,
                "rejection_reasons": ["score below threshold"],
                "ranking_score": None,
                "scan_timestamp": "2026-07-17T10:00:00+00:00",
            },
        ],
    }


def test_journal_converts_scanner_output_and_preserves_order(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'research.db'}"
    payload = _scanner_payload()
    original = deepcopy(payload)

    result = journal_scanner_run(payload, research_run_id="research-1", database_url=db_url)

    assert payload == original
    assert result["research_run_id"] == "research-1"
    assert result["stored_candidate_count"] == 2

    repo = MonitoringResearchRepository(database_url=db_url)
    run = repo.fetch_research_run_by_id("research-1")
    candidates = repo.fetch_research_candidates_for_run("research-1")

    assert run is not None
    assert run["benchmark_symbol"] == "SPY"
    assert run["universe_size"] == 2
    assert [item["symbol"] for item in candidates] == ["NVDA", "AMD"]
    assert candidates[0]["rank"] == 1
    assert candidates[0]["rejection_status"] == "ELIGIBLE"
    assert candidates[1]["rejection_status"] == "REJECTED"
    assert candidates[1]["company_name"] == "AMD"
    assert candidates[0]["factor_breakdown_json"]


def test_journal_supports_duplicate_run_ids_without_duplicate_candidates(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'research.db'}"
    payload = _scanner_payload()

    first = journal_scanner_run(payload, research_run_id="research-dup", database_url=db_url)
    second = journal_scanner_run(payload, research_run_id="research-dup", database_url=db_url)

    repo = MonitoringResearchRepository(database_url=db_url)
    assert repo.count_total_research_runs() == 1
    assert repo.count_total_candidate_observations() == 2
    assert second["duplicate_run"] is True
    assert first["research_run_id"] == second["research_run_id"]


def test_journal_rejects_invalid_scanner_payload():
    with pytest.raises(ValueError, match="summary is required"):
        journal_scanner_run({"ranked_candidates": []}, research_run_id="research-invalid", database_url=None)
