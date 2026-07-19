from factor_intelligence_scorecard import build_scorecards


def test_component_scores_reconcile():
    predictive = [
        {
            "factor_id": "f1",
            "factor_version": "v1",
            "spearman_correlation": 0.5,
            "valid_sample_count": 200,
            "missing_count": 0,
        }
    ]
    stability = [
        {
            "factor_id": "f1",
            "factor_version": "v1",
            "per_window": False,
            "stability_score": 0.4,
            "stability_classification": "stable",
        }
    ]
    regime = [
        {
            "factor_id": "f1",
            "factor_version": "v1",
            "spearman_correlation": 0.2,
            "status": "completed",
        }
    ]
    redundancy = []
    scorecards = build_scorecards(predictive, stability, regime, redundancy, "2024-01-01", "2024-12-31")
    assert len(scorecards) == 1
    row = scorecards[0]
    assert row["overall_research_score"] is not None
    assert row["overall_research_score"] >= 0.0
