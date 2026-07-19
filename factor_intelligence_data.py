from __future__ import annotations

from typing import Any

from factor_intelligence_repository import FactorIntelligenceRepository


def fetch_factor_intelligence_dashboard_payload(database_url: str | None) -> dict[str, Any]:
    repo = FactorIntelligenceRepository(database_url=database_url)
    try:
        if not repo.db.enabled:
            return {
                "db_connected": False,
                "latest_run": {},
                "leaderboard": [],
                "predictive": [],
                "bucket": [],
                "stability": [],
                "regime": [],
                "redundancy": [],
                "warnings": ["database unavailable"],
            }
        repo.db.ensure_schema()
        latest = repo.latest_completed_run()
        if not latest:
            return {
                "db_connected": True,
                "latest_run": {},
                "leaderboard": [],
                "predictive": [],
                "bucket": [],
                "stability": [],
                "regime": [],
                "redundancy": [],
                "warnings": ["no completed factor intelligence runs"],
            }

        run_id = str(latest.get("run_id") or "")
        leaderboard = repo.factor_leaderboard(run_id, limit=100)
        predictive = repo.db.query_all(
            """
            SELECT factor_id, factor_version, sample_count, valid_sample_count, missing_count,
                   pearson_correlation, spearman_correlation, top_minus_bottom_spread,
                   positive_return_rate, confidence_classification, status
            FROM factor_predictive_statistics
            WHERE run_id = ?
            ORDER BY factor_id ASC, factor_version ASC
            """,
            (run_id,),
        )
        bucket = repo.db.query_all(
            """
            SELECT factor_id, factor_version, bucket_count, bucket_number, observation_count,
                   average_forward_return, median_forward_return, positive_return_rate,
                   top_minus_bottom_spread, monotonicity_score, direction_consistency,
                   status
            FROM factor_bucket_statistics
            WHERE run_id = ?
            ORDER BY factor_id ASC, factor_version ASC, bucket_number ASC
            """,
            (run_id,),
        )
        stability = repo.db.query_all(
            """
            SELECT factor_id, factor_version, per_window, window_id, window_sample_count,
                   window_correlation, window_spread, stability_score,
                   stability_classification, status
            FROM factor_stability_results
            WHERE run_id = ?
            ORDER BY factor_id ASC, factor_version ASC, per_window DESC, window_id ASC
            """,
            (run_id,),
        )
        regime = repo.db.query_all(
            """
            SELECT factor_id, factor_version, regime_label, sample_count,
                   spearman_correlation, top_minus_bottom_spread, expected_direction_success_rate,
                   status
            FROM factor_regime_statistics
            WHERE run_id = ?
            ORDER BY factor_id ASC, factor_version ASC, regime_label ASC
            """,
            (run_id,),
        )
        redundancy = repo.db.query_all(
            """
            SELECT factor_a_id, factor_a_version, factor_b_id, factor_b_version,
                   aligned_sample_count, absolute_correlation, redundancy_classification, status
            FROM factor_redundancy_statistics
            WHERE run_id = ?
            ORDER BY factor_a_id ASC, factor_a_version ASC, factor_b_id ASC, factor_b_version ASC
            """,
            (run_id,),
        )

        warnings = []
        if any(row.get("status") == "insufficient_data" for row in predictive):
            warnings.append("Some factors have insufficient predictive sample size")
        return {
            "db_connected": True,
            "latest_run": latest,
            "leaderboard": leaderboard,
            "predictive": predictive,
            "bucket": bucket,
            "stability": stability,
            "regime": regime,
            "redundancy": redundancy,
            "warnings": warnings,
            "research_note": "Historical research analytics only. No automatic strategy-weight updates.",
        }
    finally:
        repo.close()
