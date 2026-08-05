from __future__ import annotations

from datetime import datetime, timedelta, timezone

from self_improving_data import fetch_self_improving_dashboard_payload
from self_improving_intelligence import (
    build_factor_effectiveness,
    build_strategy_leaderboard,
    build_trade_memory_record,
    classify_market_regime,
    recommend_position_size,
    recommend_strategy_states,
    recommendation_policy_summary,
)
from self_improving_repository import SelfImprovingRepository


def _closed_trade(trade_id: str, strategy_id: str = "trend_momentum_v1", net_pnl: float = 10.0) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "trade_id": trade_id,
        "run_id": "run-1",
        "symbol": "AAA",
        "strategy_id": strategy_id,
        "strategy_version": "v1",
        "entry_timestamp": (now - timedelta(hours=8)).isoformat(),
        "exit_timestamp": now.isoformat(),
        "entry_price": 100.0,
        "exit_price": 101.0,
        "quantity": 1.0,
        "realized_gross_pnl": net_pnl,
        "estimated_fees": 0.1,
        "estimated_slippage": 0.1,
        "net_pnl": net_pnl,
        "percentage_return": net_pnl / 100.0,
        "holding_duration_hours": 8.0,
        "max_adverse_excursion": -0.01,
        "max_favorable_excursion": 0.03,
        "market_regime": "normal_bull",
        "exit_reason": "target_hit",
    }


def test_trade_memory_requires_completed_status():
    trade = _closed_trade("t-1")
    rejected = build_trade_memory_record(trade, source_order_status="accepted")
    assert rejected is None

    accepted = build_trade_memory_record(
        trade,
        source_order_status="filled",
        quantum_score={"final_score": 91.0, "score_version": "quantum_v2", "normalized_component_scores": {"trend_strength": 85.0}, "component_weights": {"trend_strength": 20.0}},
        strategy_signal={"strategy_score": 88.0, "confidence": 86.0, "market_regime": "normal_bull"},
    )
    assert accepted is not None
    assert accepted["completed_only"] is True
    assert accepted["trade_id"] == "t-1"


def test_leaderboard_and_recommendations_are_review_only():
    rows = []
    for idx in range(35):
        trade = _closed_trade(f"t-{idx}", net_pnl=15.0 if idx % 2 == 0 else -5.0)
        rows.append(
            build_trade_memory_record(
                trade,
                source_order_status="filled",
                quantum_score={
                    "final_score": 90.0,
                    "score_version": "quantum_v2",
                    "normalized_component_scores": {"trend_strength": 80.0, "momentum_quality": 75.0},
                    "component_weights": {"trend_strength": 20.0, "momentum_quality": 15.0},
                },
                strategy_signal={"strategy_score": 84.0, "market_regime": "normal_bull"},
            )
        )

    leaderboard = build_strategy_leaderboard([item for item in rows if item is not None], minimum_sample=30)
    assert len(leaderboard) == 1
    assert leaderboard[0]["sample_status"] == "READY"

    strategy_regime = [
        {
            "strategy_id": "trend_momentum_v1",
            "strategy_version": "v1",
            "regime_id": "normal_bull",
            "sample_count": 35,
            "win_rate": 0.55,
            "expectancy": 1.0,
            "drawdown": 0.1,
            "recent_degradation": 0.0,
            "compatibility_score": 70.0,
            "pause_recommended": False,
            "reasons": [],
        }
    ]
    state_recos = recommend_strategy_states(leaderboard, strategy_regime, min_samples=30)
    assert len(state_recos) == 1
    assert state_recos[0]["review_required"] is True
    assert state_recos[0]["automation_allowed"] is False


def test_regime_classification_and_position_recommendation_policy():
    regime = classify_market_regime(
        {
            "spy_vs_ema20_pct": -1.5,
            "spy_vs_ema50_pct": -2.0,
            "spy_vs_ema200_pct": -4.0,
            "ema20_slope_pct": -0.03,
            "ema50_slope_pct": -0.02,
            "ema200_slope_pct": -0.01,
            "benchmark_momentum_20d_pct": -4.0,
            "realized_volatility_pct": 45.0,
            "atr_pct": 6.5,
            "breadth_above_200_pct": 30.0,
            "sector_participation_pct": 35.0,
            "drawdown_from_high_pct": -22.0,
        }
    )
    assert regime["regime_id"] in {"panic_risk_off", "strong_bear"}

    reco = recommend_position_size(
        strategy_id="trend_momentum_v1",
        strategy_version="v1",
        symbol="AAA",
        quantum_score=95.0,
        strategy_score=90.0,
        historical_expectancy=0.8,
        profit_factor=1.3,
        drawdown=0.1,
        sample_size=50,
        market_regime=regime["regime_id"],
        portfolio_concentration=0.10,
        recent_stability=0.8,
        max_allowed_allocation=0.10,
        hard_cap_allocation=0.10,
        hard_cap_risk=0.02,
        risk_policy_checks={
            "daily_loss_limits": True,
            "max_position_limits": True,
            "duplicate_protection": True,
            "open_order_checks": True,
            "sector_concentration": True,
            "strategy_pause_state": True,
        },
    )
    assert reco["review_required"] is True
    assert reco["recommended_allocation_pct"] <= 10.0


def test_repository_round_trip_payload(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'self_improving.db'}"
    repo = SelfImprovingRepository(database_url=db_url)
    try:
        repo.db.ensure_schema()
        trade = build_trade_memory_record(
            _closed_trade("t-db-1"),
            source_order_status="filled",
            quantum_score={
                "final_score": 89.0,
                "score_version": "quantum_v2",
                "normalized_component_scores": {"trend_strength": 70.0},
                "component_weights": {"trend_strength": 20.0},
            },
            strategy_signal={"strategy_score": 82.0, "market_regime": "normal_bull"},
        )
        repo.save_trade_memory_records([trade])

        payload = fetch_self_improving_dashboard_payload(db_url)
        assert payload["db_connected"] is True
        assert len(payload["trade_memory"]) >= 1
        assert payload["trade_memory"][0]["trade_id"] == "t-db-1"
    finally:
        repo.close()


def test_factor_effectiveness_minimum_shape():
    rows = []
    for idx in range(8):
        trade = _closed_trade(f"t-f-{idx}", net_pnl=5.0 if idx % 2 == 0 else -2.0)
        rows.append(
            build_trade_memory_record(
                trade,
                source_order_status="filled",
                quantum_score={
                    "final_score": 85.0,
                    "score_version": "quantum_v2",
                    "normalized_component_scores": {"trend_strength": 75.0},
                    "component_weights": {"trend_strength": 20.0},
                },
                strategy_signal={"strategy_score": 80.0, "market_regime": "normal_bull"},
            )
        )

    factor_rows = build_factor_effectiveness([item for item in rows if item is not None], min_samples=5)
    assert factor_rows
    assert "factor_name" in factor_rows[0]
    assert factor_rows[0]["analysis_version"]


def test_recommendation_policy_is_review_only():
    policy = recommendation_policy_summary()
    assert policy["live_blocked"] is True
    assert policy["recommendation_review_only_default"] is True
    assert policy["no_auto_weight_change"] is True
