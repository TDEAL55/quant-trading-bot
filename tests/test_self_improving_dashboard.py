import dashboard_app
from test_dashboard_app import _FakeStreamlit


def test_self_improving_page_renders(monkeypatch):
    fake = _FakeStreamlit()
    fake.session_state["dashboard_research_payload"] = {
        "self_improving": {
            "db_connected": True,
            "trade_memory": [{"trade_id": "t-1", "symbol": "AAA"}],
            "strategy_leaderboard": [{"strategy_id": "trend_momentum_v1", "net_profit": 123.0}],
            "latest_regime": {"regime_id": "normal_bull", "confidence": 75.0},
            "strategy_regime_matrix": [{"strategy_id": "trend_momentum_v1", "compatibility_score": 70.0}],
            "factor_effectiveness": [{"factor_name": "trend_strength", "predictive_status": "PREDICTIVE"}],
            "allocation_recommendations": [{"strategy_id": "trend_momentum_v1", "recommended_allocation_pct": 5.0}],
            "strategy_state_recommendations": [{"strategy_id": "trend_momentum_v1", "proposed_state": "ACTIVE"}],
            "weight_change_recommendations": [{"factor_name": "trend_strength", "proposed_weight": 22.0}],
            "daily_report": {"report_type": "daily", "market_date": "2026-01-01"},
            "weekly_report": {"report_type": "weekly", "period_start": "2025-12-25", "period_end": "2026-01-01"},
        }
    }
    monkeypatch.setattr(dashboard_app, "st", fake)

    dashboard_app.render_self_improving_page()

    assert any(call[0] == "markdown" and "SELF-IMPROVING" in call[1] for call in fake._calls)
