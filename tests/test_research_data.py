from research_data import build_research_analytics, build_research_candidate_records, build_research_config_snapshot, fetch_research_dashboard_payload
from research_repository import MonitoringResearchRepository, ResearchPersistencePayload


def test_build_research_candidate_records_preserves_order_and_details():
    payload = {
        "summary": {"symbol_count": 2, "eligible_count": 1, "rejection_count": 1, "error_count": 0, "duration_seconds": 1.0},
        "ranked_candidates": [
            {
                "symbol": "NVDA",
                "company_name": "NVIDIA",
                "rank": 1,
                "overall_score": 84.2,
                "confidence": 72.1,
                "signal": "BUY",
                "regime": "strong_bull",
                "sector": "Technology",
                "industry": "Semiconductors",
                "latest_price": 125.5,
                "average_dollar_volume": 627500000.0,
                "liquidity_score": 91.2,
                "component_scores": {"trend": 82.0, "momentum": 70.0, "volume": 65.0, "volatility": 55.0, "market_regime": 88.0, "risk_quality": 74.0},
                "reasons": ["trend strength"],
                "warnings": ["extended"],
                "data_quality": {"factor": {"history_sufficient": True}},
                "eligible": True,
                "rejection_reasons": [],
                "ranking_score": 80.1,
                "scan_timestamp": "2026-07-17T10:00:00+00:00",
            }
        ],
        "scan_results": [
            {
                "symbol": "NVDA",
                "company_name": "NVIDIA",
                "rank": 1,
                "overall_score": 84.2,
                "confidence": 72.1,
                "signal": "BUY",
                "regime": "strong_bull",
                "sector": "Technology",
                "industry": "Semiconductors",
                "latest_price": 125.5,
                "average_dollar_volume": 627500000.0,
                "liquidity_score": 91.2,
                "component_scores": {"trend": 82.0, "momentum": 70.0, "volume": 65.0, "volatility": 55.0, "market_regime": 88.0, "risk_quality": 74.0},
                "reasons": ["trend strength"],
                "warnings": ["extended"],
                "data_quality": {"factor": {"history_sufficient": True}},
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
                "component_scores": {"trend": 45.0, "momentum": 40.0, "volume": 30.0, "volatility": 55.0, "market_regime": 50.0, "risk_quality": 44.0},
                "reasons": ["insufficient momentum"],
                "warnings": [],
                "data_quality": {"factor": {"history_sufficient": True}},
                "eligible": False,
                "rejection_reasons": ["score below threshold"],
                "ranking_score": None,
                "scan_timestamp": "2026-07-17T10:00:00+00:00",
            },
        ],
    }
    candidates = build_research_candidate_records(payload, "research-1")
    assert [item["symbol"] for item in candidates] == ["NVDA", "AMD"]
    assert candidates[1]["company_name"] == "AMD"
    assert candidates[0]["factor_breakdown"]["component_scores"]["trend"] == 82.0


def test_build_research_analytics_calculations():
    candidates = [
        {"symbol": "NVDA", "sector": "Technology", "market_regime": "strong_bull", "signal": "BUY", "overall_score": 84.0, "confidence": 72.0},
        {"symbol": "AMD", "sector": "Technology", "market_regime": "weak_bull", "signal": "HOLD", "overall_score": 50.0, "confidence": 40.0},
        {"symbol": "EQIX", "sector": "Real Estate", "market_regime": "strong_bull", "signal": "STRONG_BUY", "overall_score": 79.0, "confidence": 68.0},
        {"symbol": "NVDA", "sector": "Technology", "market_regime": "strong_bull", "signal": "BUY", "overall_score": 85.0, "confidence": 73.0},
    ]
    analytics = build_research_analytics(candidates, recent_runs=[{"research_run_id": "r1"}, {"research_run_id": "r2"}])
    assert analytics["total_research_runs"] == 2
    assert analytics["total_candidate_observations"] == 4
    assert analytics["average_candidates_per_run"] == 2.0
    assert analytics["average_overall_score"] == 74.5
    assert analytics["average_confidence"] == 63.25
    assert analytics["candidate_count_by_sector"][0]["sector"] == "Technology"
    assert analytics["candidate_count_by_regime"][0]["market_regime"] == "strong_bull"
    assert analytics["signal_distribution"][0]["signal"] == "BUY"
    assert analytics["top_recurring_symbols"][0]["symbol"] == "NVDA"


def test_empty_database_behavior_returns_zero_metrics(tmp_path):
    repo = MonitoringResearchRepository(database_url=f"sqlite:///{tmp_path / 'research.db'}")
    repo.db.ensure_schema()
    payload = fetch_research_dashboard_payload(f"sqlite:///{tmp_path / 'research.db'}")
    assert payload["latest_research_run"] == {}
    assert payload["recent_research_runs"] == []
    assert payload["research_analytics"]["total_research_runs"] == 0
    assert payload["research_analytics"]["total_candidate_observations"] == 0
    assert payload["selected_research_candidates"] == []


def test_research_config_snapshot_omits_secrets():
    snapshot = build_research_config_snapshot()
    assert "ALPACA_API_KEY" not in snapshot
    assert "FACTOR_WEIGHTS" not in snapshot
    assert snapshot["scanner_version"]
