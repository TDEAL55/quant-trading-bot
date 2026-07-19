import dashboard_app
from test_dashboard_app import _FakeStreamlit


def test_factor_intelligence_page_renders(monkeypatch):
    fake = _FakeStreamlit()
    fake.session_state["dashboard_research_payload"] = {
        "factor_intelligence": {
            "db_connected": True,
            "latest_run": {
                "run_id": "fi-1",
                "status": "completed",
                "analysis_start_date": "2024-01-01",
                "analysis_end_date": "2024-12-31",
                "forward_horizon": 20,
                "sample_count": 100,
            },
            "leaderboard": [
                {
                    "factor_id": "overall_score",
                    "factor_version": "v1",
                    "name": "Overall Score",
                    "category": "quality",
                    "overall_research_score": 0.7,
                    "predictive_score": 0.5,
                    "stability_score": 0.4,
                    "regime_score": 0.3,
                    "sample_count": 100,
                    "confidence_classification": "medium",
                    "warnings": [],
                    "strongest_evidence": [{"component": "predictive_rank_correlation", "value": 0.5}],
                    "weakest_evidence": [{"component": "redundancy_penalty", "value": 0.0}],
                }
            ],
            "predictive": [],
            "bucket": [],
            "stability": [],
            "regime": [],
            "redundancy": [],
            "warnings": [],
            "research_note": "Historical research analytics only.",
        }
    }
    monkeypatch.setattr(dashboard_app, "st", fake)
    dashboard_app.render_factor_intelligence_page()
    assert any(call[0] == "markdown" and "FACTOR INTELLIGENCE" in call[1] for call in fake._calls)
