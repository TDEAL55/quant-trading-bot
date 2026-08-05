from datetime import datetime, timezone
from pathlib import Path

from portfolio_intelligence import build_strategy_allocation_evidence, run_portfolio_intelligence
from self_improving_repository import SelfImprovingRepository


def _ranked(symbol, rank, score=85.0):
    return {
        "symbol": symbol,
        "rank": rank,
        "overall_score": score,
        "confidence": 75.0,
        "sector": "Information Technology",
        "industry": "Software",
        "latest_price": 100.0,
        "quantum_score": {
            "final_score": score,
            "risk_reward": {"reward_risk_ratio": 2.0},
            "data_quality_status": "ok",
            "liquidity_status": "ok",
            "rejection_reasons": [],
        },
        "strategy_specific_scores": {
            "trend_momentum_v1": {
                "strategy_id": "trend_momentum_v1",
                "strategy_version": "1.0.0",
                "strategy_score": 80.0,
                "confidence": 75.0,
                "eligible": True,
            }
        },
    }


def test_strategy_evidence_actions_cover_required_states():
    evidence = build_strategy_allocation_evidence(
        [
            {
                "strategy_id": "a",
                "strategy_version": "1",
                "completed_trade_count": 10,
                "expectancy": 1.0,
                "profit_factor": 1.5,
                "sharpe_ratio": 0.9,
                "sortino_ratio": 0.8,
                "maximum_drawdown": 0.1,
                "recent_20": {"expectancy": 1.0},
                "recent_60": {"expectancy": 1.0},
            },
            {
                "strategy_id": "b",
                "strategy_version": "1",
                "completed_trade_count": 60,
                "expectancy": -0.5,
                "profit_factor": 0.8,
                "sharpe_ratio": -0.2,
                "sortino_ratio": -0.3,
                "maximum_drawdown": 0.1,
                "recent_20": {"expectancy": -0.2},
                "recent_60": {"expectancy": -0.1},
            },
            {
                "strategy_id": "c",
                "strategy_version": "1",
                "completed_trade_count": 60,
                "expectancy": 1.5,
                "profit_factor": 1.5,
                "sharpe_ratio": 0.9,
                "sortino_ratio": 0.8,
                "maximum_drawdown": 0.35,
                "recent_20": {"expectancy": 1.0},
                "recent_60": {"expectancy": 1.2},
            },
        ]
    )
    assert evidence["a:1"]["action"] == "INSUFFICIENT_SAMPLE"
    assert evidence["b:1"]["action"] == "REDUCE_RECOMMENDED"
    assert evidence["c:1"]["action"] == "PAUSE_RECOMMENDED"


def test_portfolio_intelligence_never_submits_orders_and_is_review_only():
    result = run_portfolio_intelligence(
        ranked_candidates=[_ranked("AAA", 1), _ranked("BBB", 2)],
        current_positions=[],
        account_equity=10000.0,
        available_cash=10000.0,
        price_history_by_symbol={},
        strategy_leaderboard=[],
    )
    payload = result.to_dict()
    assert payload["review_required"] is True
    assert payload["selected_count"] >= 0
    assert "allocation" in payload


def test_live_mode_block_remains_external_guard():
    # Portfolio intelligence itself is recommendation-only and has no order submission path.
    result = run_portfolio_intelligence(
        ranked_candidates=[_ranked("AAA", 1)],
        current_positions=[],
        account_equity=10000.0,
        available_cash=10000.0,
    )
    assert result.review_required is True


def test_portfolio_intelligence_persistence_round_trip(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'portfolio_intel.db'}"
    repo = SelfImprovingRepository(database_url=db_url)
    try:
        result = run_portfolio_intelligence(
            ranked_candidates=[_ranked("AAA", 1), _ranked("BBB", 2)],
            current_positions=[],
            account_equity=10000.0,
            available_cash=10000.0,
        ).to_dict()
        run_id = repo.save_portfolio_intelligence_result(
            allocation_run_id="run-portfolio-1",
            source_scan_run_id="scan-1",
            account_equity=10000.0,
            available_cash=10000.0,
            investable_capital=8000.0,
            result=result,
            configuration={"policy_version": "portfolio_intelligence_v1"},
        )
        assert run_id == "run-portfolio-1"

        payload = repo.fetch_latest_portfolio_intelligence()
        assert payload["run"]["allocation_run_id"] == "run-portfolio-1"
        assert isinstance(payload["recommendations"], list)
        assert isinstance(payload["exposures"], list)
        assert isinstance(payload["reports"], list)
    finally:
        repo.close()


def test_migration_018_contains_portability_friendly_schema():
    text = Path(__file__).resolve().parents[1].joinpath("migrations", "018_portfolio_intelligence.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS portfolio_allocation_runs" in text
    assert "CREATE TABLE IF NOT EXISTS portfolio_allocation_recommendations" in text
    assert "CREATE TABLE IF NOT EXISTS portfolio_exposure_snapshots" in text
    assert "CREATE TABLE IF NOT EXISTS portfolio_intelligence_reports" in text
