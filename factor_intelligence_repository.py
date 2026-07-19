from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from monitoring_db import MonitoringDatabase
from factor_intelligence_utils import stable_json, utc_iso


def _json_loads(value: Any, default: Any) -> Any:
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


@dataclass(frozen=True)
class FactorIntelligenceRunPayload:
    run: dict[str, Any]
    predictive_stats: list[dict[str, Any]]
    bucket_stats: list[dict[str, Any]]
    stability_results: list[dict[str, Any]]
    regime_stats: list[dict[str, Any]]
    redundancy_stats: list[dict[str, Any]]
    scorecards: list[dict[str, Any]]


class FactorIntelligenceRepository:
    def __init__(self, database_url: str | None = None):
        self.db = MonitoringDatabase(database_url=database_url)

    def close(self) -> None:
        self.db.close()

    def _adapt(self, query: str) -> str:
        return self.db._adapt_query(query)

    def register_factors(self, definitions: list[dict[str, Any]]) -> dict[str, Any]:
        if not self.db.enabled:
            raise RuntimeError("database is not enabled")
        self.db.ensure_schema()
        conn = self.db.conn
        inserted = 0
        with conn:
            cur = conn.cursor()
            try:
                sql = self._adapt(
                    """
                    INSERT INTO factor_definitions (
                        factor_id, name, description, category, version, direction,
                        calculation_source, lookback_period, minimum_history_required,
                        expected_value_type, active, metadata_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(factor_id, version) DO UPDATE SET
                        name = excluded.name,
                        description = excluded.description,
                        category = excluded.category,
                        direction = excluded.direction,
                        calculation_source = excluded.calculation_source,
                        lookback_period = excluded.lookback_period,
                        minimum_history_required = excluded.minimum_history_required,
                        expected_value_type = excluded.expected_value_type,
                        active = excluded.active,
                        metadata_json = excluded.metadata_json,
                        updated_at = excluded.updated_at
                    """
                )
                for row in definitions:
                    cur.execute(
                        sql,
                        (
                            row.get("factor_id"),
                            row.get("name"),
                            row.get("description"),
                            row.get("category"),
                            row.get("version"),
                            row.get("direction"),
                            row.get("calculation_source"),
                            row.get("lookback_period"),
                            row.get("minimum_history_required"),
                            row.get("expected_value_type", "numeric"),
                            1 if row.get("active", True) else 0,
                            stable_json(row.get("metadata") or {}),
                            row.get("created_at") or utc_iso(),
                            utc_iso(),
                        ),
                    )
                    inserted += 1
            finally:
                cur.close()
        return {"stored_factor_count": inserted, "saved_at": utc_iso()}

    def get_factor_definitions(self, active_only: bool = False) -> list[dict[str, Any]]:
        where = "WHERE active = 1" if active_only else ""
        rows = self.db.query_all(
            f"SELECT * FROM factor_definitions {where} ORDER BY factor_id ASC, version ASC"
        )
        for row in rows:
            row["metadata"] = _json_loads(row.get("metadata_json"), {})
        return rows

    def upsert_observations(self, observations: list[dict[str, Any]]) -> dict[str, Any]:
        if not self.db.enabled:
            raise RuntimeError("database is not enabled")
        self.db.ensure_schema()
        conn = self.db.conn
        with conn:
            cur = conn.cursor()
            try:
                sql = self._adapt(
                    """
                    INSERT INTO factor_observations (
                        observation_id, snapshot_id, candidate_id, symbol, factor_id, factor_version,
                        observation_timestamp, factor_value, normalized_value, percentile_rank,
                        universe_size, regime_label, data_freshness_timestamp, value_status,
                        metadata_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(observation_id) DO UPDATE SET
                        normalized_value = excluded.normalized_value,
                        percentile_rank = excluded.percentile_rank,
                        universe_size = excluded.universe_size,
                        regime_label = excluded.regime_label,
                        data_freshness_timestamp = excluded.data_freshness_timestamp,
                        value_status = excluded.value_status,
                        metadata_json = excluded.metadata_json,
                        updated_at = excluded.updated_at
                    """
                )
                for row in observations:
                    cur.execute(
                        sql,
                        (
                            row.get("observation_id"),
                            row.get("snapshot_id"),
                            row.get("candidate_id"),
                            row.get("symbol"),
                            row.get("factor_id"),
                            row.get("factor_version"),
                            row.get("observation_timestamp"),
                            row.get("factor_value"),
                            row.get("normalized_value"),
                            row.get("percentile_rank"),
                            row.get("universe_size"),
                            row.get("regime_label"),
                            row.get("data_freshness_timestamp"),
                            row.get("value_status", "valid"),
                            stable_json(row.get("metadata") or {}),
                            row.get("created_at") or utc_iso(),
                            utc_iso(),
                        ),
                    )
            finally:
                cur.close()
        return {"stored_observation_count": len(observations), "saved_at": utc_iso()}

    def create_run(self, run: dict[str, Any]) -> dict[str, Any]:
        if not self.db.enabled:
            raise RuntimeError("database is not enabled")
        self.db.ensure_schema()
        self.db.execute(
            """
            INSERT INTO factor_intelligence_runs (
                run_id, run_fingerprint, attempt_id, started_at, completed_at, status,
                analysis_start_date, analysis_end_date, forward_horizon, universe_filter,
                regime_filter, factor_version_set, sample_count, configuration_json,
                timings_json, error_message, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.get("run_id"),
                run.get("run_fingerprint"),
                run.get("attempt_id"),
                run.get("started_at") or utc_iso(),
                run.get("completed_at"),
                run.get("status", "pending"),
                run.get("analysis_start_date"),
                run.get("analysis_end_date"),
                run.get("forward_horizon"),
                run.get("universe_filter"),
                run.get("regime_filter"),
                run.get("factor_version_set"),
                run.get("sample_count", 0),
                stable_json(run.get("configuration") or {}),
                stable_json(run.get("timings") or {}),
                run.get("error_message"),
                run.get("created_at") or utc_iso(),
                run.get("updated_at") or utc_iso(),
            ),
        )
        return run

    def update_run_status(self, run_id: str, status: str, completed_at: str | None = None, error_message: str | None = None, sample_count: int | None = None, timings: dict[str, Any] | None = None) -> None:
        self.db.execute(
            """
            UPDATE factor_intelligence_runs
            SET status = ?, completed_at = ?, error_message = ?, sample_count = COALESCE(?, sample_count), timings_json = ?, updated_at = ?
            WHERE run_id = ?
            """,
            (
                status,
                completed_at,
                error_message,
                sample_count,
                stable_json(timings or {}),
                utc_iso(),
                run_id,
            ),
        )

    def fetch_run_by_fingerprint(self, fingerprint: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM factor_intelligence_runs WHERE run_fingerprint = ? AND status = 'completed' ORDER BY created_at DESC LIMIT 1",
            (fingerprint,),
        )
        if not row:
            return None
        row["configuration"] = _json_loads(row.get("configuration_json"), {})
        row["timings"] = _json_loads(row.get("timings_json"), {})
        return row

    def save_results(self, payload: FactorIntelligenceRunPayload) -> dict[str, Any]:
        if not self.db.enabled:
            raise RuntimeError("database is not enabled")
        self.db.ensure_schema()
        run_id = str(payload.run.get("run_id") or "")
        if not run_id:
            raise ValueError("run_id is required")

        conn = self.db.conn
        with conn:
            cur = conn.cursor()
            try:
                cur.execute(self._adapt("DELETE FROM factor_predictive_statistics WHERE run_id = ?"), (run_id,))
                cur.execute(self._adapt("DELETE FROM factor_bucket_statistics WHERE run_id = ?"), (run_id,))
                cur.execute(self._adapt("DELETE FROM factor_stability_results WHERE run_id = ?"), (run_id,))
                cur.execute(self._adapt("DELETE FROM factor_regime_statistics WHERE run_id = ?"), (run_id,))
                cur.execute(self._adapt("DELETE FROM factor_redundancy_statistics WHERE run_id = ?"), (run_id,))
                cur.execute(self._adapt("DELETE FROM factor_intelligence_scorecards WHERE run_id = ?"), (run_id,))

                for row in payload.predictive_stats:
                    cur.execute(
                        self._adapt(
                            """
                            INSERT INTO factor_predictive_statistics (
                                stat_id, run_id, factor_id, factor_version, forward_horizon, sample_count,
                                valid_sample_count, missing_count, pearson_correlation, spearman_correlation,
                                mean_forward_return, median_forward_return, top_bucket_return,
                                bottom_bucket_return, top_minus_bottom_spread, positive_return_rate,
                                mean_excess_return, median_excess_return, confidence_classification,
                                status, analysis_start_date, analysis_end_date, warnings_json,
                                metadata_json, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """
                        ),
                        (
                            row.get("stat_id"), run_id, row.get("factor_id"), row.get("factor_version"), row.get("forward_horizon"), row.get("sample_count", 0),
                            row.get("valid_sample_count", 0), row.get("missing_count", 0), row.get("pearson_correlation"), row.get("spearman_correlation"),
                            row.get("mean_forward_return"), row.get("median_forward_return"), row.get("top_bucket_return"), row.get("bottom_bucket_return"),
                            row.get("top_minus_bottom_spread"), row.get("positive_return_rate"), row.get("mean_excess_return"), row.get("median_excess_return"),
                            row.get("confidence_classification", "insufficient_data"), row.get("status", "completed"), row.get("analysis_start_date"), row.get("analysis_end_date"),
                            stable_json(row.get("warnings") or []), stable_json(row.get("metadata") or {}), row.get("created_at") or utc_iso(),
                        ),
                    )

                for row in payload.bucket_stats:
                    cur.execute(
                        self._adapt(
                            """
                            INSERT INTO factor_bucket_statistics (
                                bucket_id, run_id, factor_id, factor_version, forward_horizon,
                                bucket_count, bucket_number, lower_bound, upper_bound,
                                observation_count, average_forward_return, median_forward_return,
                                positive_return_rate, average_excess_return, return_volatility,
                                min_return, max_return, top_minus_bottom_spread, monotonicity_score,
                                direction_consistency, bucket_coverage, status, warnings_json, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """
                        ),
                        (
                            row.get("bucket_id"), run_id, row.get("factor_id"), row.get("factor_version"), row.get("forward_horizon"),
                            row.get("bucket_count"), row.get("bucket_number"), row.get("lower_bound"), row.get("upper_bound"), row.get("observation_count", 0),
                            row.get("average_forward_return"), row.get("median_forward_return"), row.get("positive_return_rate"), row.get("average_excess_return"),
                            row.get("return_volatility"), row.get("min_return"), row.get("max_return"), row.get("top_minus_bottom_spread"), row.get("monotonicity_score"),
                            row.get("direction_consistency"), row.get("bucket_coverage"), row.get("status", "completed"), stable_json(row.get("warnings") or []), row.get("created_at") or utc_iso(),
                        ),
                    )

                for row in payload.stability_results:
                    cur.execute(
                        self._adapt(
                            """
                            INSERT INTO factor_stability_results (
                                stability_id, run_id, factor_id, factor_version, window_id, per_window,
                                training_start_date, training_end_date, validation_start_date,
                                validation_end_date, window_sample_count, window_correlation,
                                window_spread, expected_direction_correct, mean_window_score,
                                stddev_window_score, min_window_score, max_window_score,
                                degradation_score, stability_score, stability_classification,
                                status, metadata_json, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """
                        ),
                        (
                            row.get("stability_id"), run_id, row.get("factor_id"), row.get("factor_version"), row.get("window_id"),
                            1 if row.get("per_window") else 0, row.get("training_start_date"), row.get("training_end_date"),
                            row.get("validation_start_date"), row.get("validation_end_date"), row.get("window_sample_count", 0),
                            row.get("window_correlation"), row.get("window_spread"), row.get("expected_direction_correct"),
                            row.get("mean_window_score"), row.get("stddev_window_score"), row.get("min_window_score"), row.get("max_window_score"),
                            row.get("degradation_score"), row.get("stability_score"), row.get("stability_classification", "insufficient_data"),
                            row.get("status", "completed"), stable_json(row.get("metadata") or {}), row.get("created_at") or utc_iso(),
                        ),
                    )

                for row in payload.regime_stats:
                    cur.execute(
                        self._adapt(
                            """
                            INSERT INTO factor_regime_statistics (
                                regime_stat_id, run_id, factor_id, factor_version, regime_label,
                                sample_count, spearman_correlation, top_minus_bottom_spread,
                                positive_return_rate, average_return, average_excess_return,
                                stability_score, expected_direction_success_rate,
                                status, warnings_json, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """
                        ),
                        (
                            row.get("regime_stat_id"), run_id, row.get("factor_id"), row.get("factor_version"), row.get("regime_label"),
                            row.get("sample_count", 0), row.get("spearman_correlation"), row.get("top_minus_bottom_spread"),
                            row.get("positive_return_rate"), row.get("average_return"), row.get("average_excess_return"),
                            row.get("stability_score"), row.get("expected_direction_success_rate"), row.get("status", "completed"),
                            stable_json(row.get("warnings") or []), row.get("created_at") or utc_iso(),
                        ),
                    )

                for row in payload.redundancy_stats:
                    cur.execute(
                        self._adapt(
                            """
                            INSERT INTO factor_redundancy_statistics (
                                redundancy_id, run_id, factor_a_id, factor_a_version, factor_b_id,
                                factor_b_version, aligned_sample_count, pearson_correlation,
                                spearman_correlation, absolute_correlation, redundancy_classification,
                                status, warnings_json, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """
                        ),
                        (
                            row.get("redundancy_id"), run_id, row.get("factor_a_id"), row.get("factor_a_version"), row.get("factor_b_id"), row.get("factor_b_version"),
                            row.get("aligned_sample_count", 0), row.get("pearson_correlation"), row.get("spearman_correlation"), row.get("absolute_correlation"),
                            row.get("redundancy_classification", "insufficient_data"), row.get("status", "completed"), stable_json(row.get("warnings") or []), row.get("created_at") or utc_iso(),
                        ),
                    )

                for row in payload.scorecards:
                    cur.execute(
                        self._adapt(
                            """
                            INSERT INTO factor_intelligence_scorecards (
                                scorecard_id, run_id, factor_id, factor_version, predictive_score,
                                stability_score, regime_score, sample_quality_score,
                                redundancy_penalty, overall_research_score,
                                confidence_classification, strongest_evidence_json,
                                weakest_evidence_json, warnings_json, sample_count,
                                analysis_start_date, analysis_end_date, formula_json, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """
                        ),
                        (
                            row.get("scorecard_id"), run_id, row.get("factor_id"), row.get("factor_version"), row.get("predictive_score"),
                            row.get("stability_score"), row.get("regime_score"), row.get("sample_quality_score"), row.get("redundancy_penalty"), row.get("overall_research_score"),
                            row.get("confidence_classification", "insufficient_data"), stable_json(row.get("strongest_evidence") or []), stable_json(row.get("weakest_evidence") or []),
                            stable_json(row.get("warnings") or []), row.get("sample_count", 0), row.get("analysis_start_date"), row.get("analysis_end_date"),
                            stable_json(row.get("formula") or {}), row.get("created_at") or utc_iso(),
                        ),
                    )
            finally:
                cur.close()

        return {
            "run_id": run_id,
            "predictive_count": len(payload.predictive_stats),
            "bucket_count": len(payload.bucket_stats),
            "stability_count": len(payload.stability_results),
            "regime_count": len(payload.regime_stats),
            "redundancy_count": len(payload.redundancy_stats),
            "scorecard_count": len(payload.scorecards),
            "saved_at": utc_iso(),
        }

    def latest_completed_run(self) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM factor_intelligence_runs WHERE status = 'completed' ORDER BY completed_at DESC, started_at DESC LIMIT 1"
        )
        if not row:
            return None
        row["configuration"] = _json_loads(row.get("configuration_json"), {})
        row["timings"] = _json_loads(row.get("timings_json"), {})
        return row

    def factor_leaderboard(self, run_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT s.*, d.name, d.category
            FROM factor_intelligence_scorecards s
            JOIN factor_definitions d ON d.factor_id = s.factor_id AND d.version = s.factor_version
            WHERE s.run_id = ?
            ORDER BY s.overall_research_score DESC, s.factor_id ASC, s.factor_version ASC
            LIMIT ?
            """,
            (run_id, int(limit)),
        )
        for row in rows:
            row["warnings"] = _json_loads(row.get("warnings_json"), [])
            row["strongest_evidence"] = _json_loads(row.get("strongest_evidence_json"), [])
            row["weakest_evidence"] = _json_loads(row.get("weakest_evidence_json"), [])
        return rows

    def factor_history(self, factor_id: str, factor_version: str, limit: int = 100) -> list[dict[str, Any]]:
        return self.db.query_all(
            """
            SELECT run_id, analysis_start_date, analysis_end_date, overall_research_score,
                   predictive_score, stability_score, regime_score, sample_quality_score,
                   redundancy_penalty, confidence_classification, sample_count, created_at
            FROM factor_intelligence_scorecards
            WHERE factor_id = ? AND factor_version = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (factor_id, factor_version, int(limit)),
        )

    def security_explanation_rows(self, snapshot_id: str, symbol: str, factor_versions: dict[str, str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for factor_id, version in sorted(factor_versions.items()):
            row = self.db.query_one(
                """
                SELECT o.*, d.name, d.direction
                FROM factor_observations o
                JOIN factor_definitions d ON d.factor_id = o.factor_id AND d.version = o.factor_version
                WHERE o.snapshot_id = ? AND o.symbol = ? AND o.factor_id = ? AND o.factor_version = ?
                LIMIT 1
                """,
                (snapshot_id, symbol.upper(), factor_id, version),
            )
            if row:
                row["metadata"] = _json_loads(row.get("metadata_json"), {})
                rows.append(row)
        rows.sort(key=lambda item: (str(item.get("factor_id")), str(item.get("factor_version"))))
        return rows

    def dashboard_summary(self, run_id: str) -> dict[str, Any]:
        run = self.db.query_one("SELECT * FROM factor_intelligence_runs WHERE run_id = ?", (run_id,)) or {}
        leaderboard = self.factor_leaderboard(run_id=run_id, limit=25)
        predictive = self.db.query_all(
            """
            SELECT factor_id, factor_version, sample_count, spearman_correlation,
                   top_minus_bottom_spread, confidence_classification, status
            FROM factor_predictive_statistics
            WHERE run_id = ?
            ORDER BY factor_id ASC, factor_version ASC
            """,
            (run_id,),
        )
        regime = self.db.query_all(
            """
            SELECT factor_id, factor_version, regime_label, sample_count,
                   spearman_correlation, top_minus_bottom_spread, status
            FROM factor_regime_statistics
            WHERE run_id = ?
            ORDER BY factor_id ASC, factor_version ASC, regime_label ASC
            """,
            (run_id,),
        )
        redundancy = self.db.query_all(
            """
            SELECT factor_a_id, factor_a_version, factor_b_id, factor_b_version,
                   aligned_sample_count, absolute_correlation, redundancy_classification, status
            FROM factor_redundancy_statistics
            WHERE run_id = ?
            ORDER BY factor_a_id ASC, factor_a_version ASC, factor_b_id ASC, factor_b_version ASC
            """,
            (run_id,),
        )
        return {
            "run": run,
            "leaderboard": leaderboard,
            "predictive": predictive,
            "regime": regime,
            "redundancy": redundancy,
        }
