from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from monitoring_db import MonitoringDatabase


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True, separators=(",", ":"))


def _json_load(value: Any, default: Any) -> Any:
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


@dataclass(frozen=True)
class StrategyLabRunPayload:
    definitions: list[dict[str, Any]]
    run: dict[str, Any]
    results: list[dict[str, Any]]
    pairwise: list[dict[str, Any]]


class MonitoringStrategyLabRepository:
    def __init__(self, database_url: str | None = None):
        self.db = MonitoringDatabase(database_url=database_url)

    def _adapt_query(self, query: str) -> str:
        return self.db._adapt_query(query)

    def save_run(self, payload: StrategyLabRunPayload) -> dict[str, Any]:
        if not self.db.enabled:
            raise RuntimeError("Database is not enabled for strategy lab persistence")
        self.db.ensure_schema()

        run = dict(payload.run)
        run_id = str(run.get("run_id") or "")
        if not run_id:
            raise ValueError("run_id is required")

        conn = self.db.conn
        with conn:
            cursor = conn.cursor()
            try:
                for definition in payload.definitions:
                    cursor.execute(
                        self._adapt_query(
                            """
                            INSERT INTO strategy_definitions (
                                strategy_id, strategy_name, version, description,
                                configuration_json, configuration_fingerprint,
                                enabled, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(strategy_id, version) DO UPDATE SET
                                strategy_name = excluded.strategy_name,
                                description = excluded.description,
                                configuration_json = excluded.configuration_json,
                                configuration_fingerprint = excluded.configuration_fingerprint,
                                enabled = excluded.enabled,
                                updated_at = excluded.updated_at
                            """
                        ),
                        (
                            definition.get("strategy_id"),
                            definition.get("strategy_name"),
                            definition.get("version"),
                            definition.get("description"),
                            _stable_json(definition.get("configuration") or {}),
                            definition.get("configuration_fingerprint"),
                            1 if bool(definition.get("enabled", True)) else 0,
                            definition.get("created_at") or _utc_iso(),
                            _utc_iso(),
                        ),
                    )

                cursor.execute(
                    self._adapt_query(
                        """
                        INSERT INTO strategy_comparison_runs (
                            run_id, created_at, horizon, benchmark, comparison_mode,
                            start_date, end_date, strategy_ids_json,
                            portfolio_configuration_json,
                            transaction_cost_configuration_json,
                            status, duration_seconds, error_message,
                            summary_json, performance_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(run_id) DO UPDATE SET
                            created_at = excluded.created_at,
                            horizon = excluded.horizon,
                            benchmark = excluded.benchmark,
                            comparison_mode = excluded.comparison_mode,
                            start_date = excluded.start_date,
                            end_date = excluded.end_date,
                            strategy_ids_json = excluded.strategy_ids_json,
                            portfolio_configuration_json = excluded.portfolio_configuration_json,
                            transaction_cost_configuration_json = excluded.transaction_cost_configuration_json,
                            status = excluded.status,
                            duration_seconds = excluded.duration_seconds,
                            error_message = excluded.error_message,
                            summary_json = excluded.summary_json,
                            performance_json = excluded.performance_json
                        """
                    ),
                    (
                        run_id,
                        run.get("created_at") or _utc_iso(),
                        run.get("horizon"),
                        run.get("benchmark"),
                        run.get("comparison_mode"),
                        run.get("start_date"),
                        run.get("end_date"),
                        _stable_json(run.get("strategy_ids") or []),
                        _stable_json(run.get("portfolio_configuration") or {}),
                        _stable_json(run.get("transaction_cost_configuration") or {}),
                        run.get("status") or "completed",
                        run.get("duration_seconds") or 0.0,
                        run.get("error_message"),
                        _stable_json(run.get("summary") or {}),
                        _stable_json(run.get("performance") or {}),
                    ),
                )

                cursor.execute(self._adapt_query("DELETE FROM strategy_comparison_results WHERE run_id = ?"), (run_id,))
                cursor.execute(self._adapt_query("DELETE FROM strategy_pairwise_results WHERE run_id = ?"), (run_id,))

                for result in payload.results:
                    cursor.execute(
                        self._adapt_query(
                            """
                            INSERT INTO strategy_comparison_results (
                                run_id, strategy_id, eligible_candidate_count,
                                snapshot_count, completed_count, skipped_count,
                                analytics_json, scorecard_json, walk_forward_json,
                                regime_json, factor_exposure_json, warnings_json,
                                created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """
                        ),
                        (
                            run_id,
                            result.get("strategy_id"),
                            result.get("eligible_candidate_count", 0),
                            result.get("snapshot_count", 0),
                            result.get("completed_count", 0),
                            result.get("skipped_count", 0),
                            _stable_json(result.get("analytics") or {}),
                            _stable_json(result.get("scorecard") or {}),
                            _stable_json(result.get("walk_forward") or {}),
                            _stable_json(result.get("regime") or []),
                            _stable_json(result.get("factor_exposure") or {}),
                            _stable_json(result.get("warnings") or []),
                            _utc_iso(),
                        ),
                    )

                for pair in payload.pairwise:
                    cursor.execute(
                        self._adapt_query(
                            """
                            INSERT INTO strategy_pairwise_results (
                                run_id, strategy_a_id, strategy_b_id,
                                common_snapshot_count, comparison_json, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?)
                            """
                        ),
                        (
                            run_id,
                            pair.get("strategy_a_id"),
                            pair.get("strategy_b_id"),
                            pair.get("common_snapshot_count", 0),
                            _stable_json(pair),
                            _utc_iso(),
                        ),
                    )
            finally:
                cursor.close()

        return {"storage": "database", "run_id": run_id, "stored_result_count": len(payload.results), "saved_at": _utc_iso()}

    def _normalize_run(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        result = dict(row)
        result["strategy_ids"] = _json_load(row.get("strategy_ids_json"), [])
        result["portfolio_configuration"] = _json_load(row.get("portfolio_configuration_json"), {})
        result["transaction_cost_configuration"] = _json_load(row.get("transaction_cost_configuration_json"), {})
        result["summary"] = _json_load(row.get("summary_json"), {})
        result["performance"] = _json_load(row.get("performance_json"), {})
        return result

    def fetch_latest_run(self) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM strategy_comparison_runs ORDER BY created_at DESC LIMIT 1")
        return self._normalize_run(row)

    def count_runs(self) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS n FROM strategy_comparison_runs") or {"n": 0}
        return int(row.get("n") or 0)

    def fetch_results_for_run(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.db.query_all("SELECT * FROM strategy_comparison_results WHERE run_id = ? ORDER BY strategy_id ASC", (str(run_id),))
        result = []
        for row in rows:
            item = dict(row)
            item["analytics"] = _json_load(row.get("analytics_json"), {})
            item["scorecard"] = _json_load(row.get("scorecard_json"), {})
            item["walk_forward"] = _json_load(row.get("walk_forward_json"), {})
            item["regime"] = _json_load(row.get("regime_json"), [])
            item["factor_exposure"] = _json_load(row.get("factor_exposure_json"), {})
            item["warnings"] = _json_load(row.get("warnings_json"), [])
            result.append(item)
        return result

    def fetch_pairwise_for_run(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.db.query_all("SELECT * FROM strategy_pairwise_results WHERE run_id = ? ORDER BY strategy_a_id ASC, strategy_b_id ASC", (str(run_id),))
        result = []
        for row in rows:
            item = _json_load(row.get("comparison_json"), {})
            item.setdefault("strategy_a_id", row.get("strategy_a_id"))
            item.setdefault("strategy_b_id", row.get("strategy_b_id"))
            item.setdefault("common_snapshot_count", row.get("common_snapshot_count"))
            result.append(item)
        return result

    def close(self) -> None:
        self.db.close()
