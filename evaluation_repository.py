from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from monitoring_db import MonitoringDatabase


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class EvaluationPersistencePayload:
    records: list[dict[str, Any]]


class EvaluationRepository:
    def save_evaluations(self, payload: EvaluationPersistencePayload) -> dict[str, Any]:
        raise NotImplementedError


class MonitoringEvaluationRepository(EvaluationRepository):
    def __init__(self, database_url: str | None = None):
        self.db = MonitoringDatabase(database_url=database_url)

    def _adapt_query(self, query: str) -> str:
        return self.db._adapt_query(query)

    def _columns(self) -> list[str]:
        horizons = [1, 5, 10, 20]
        columns = [
            "research_candidate_id",
            "research_run_id",
            "symbol",
            "observation_date",
            "observation_price",
            "benchmark_symbol",
            "benchmark_observation_price",
        ]
        for horizon in horizons:
            prefix = f"forward_{horizon}d"
            columns.extend(
                [
                    f"{prefix}_target_date",
                    f"{prefix}_actual_date",
                    f"{prefix}_future_price",
                    f"{prefix}_benchmark_future_price",
                    f"{prefix}_return",
                    f"{prefix}_benchmark_return",
                    f"{prefix}_excess_return",
                    f"{prefix}_status",
                ]
            )
        columns.extend(
            [
                "label_status",
                "data_source",
                "last_attempted_at",
                "completed_at",
                "error_message",
                "created_at",
                "updated_at",
            ]
        )
        return columns

    def _row_values(self, record: dict[str, Any]) -> list[Any]:
        values: list[Any] = []
        for column in self._columns():
            if column in {"created_at", "updated_at"}:
                values.append(record.get(column) or _utc_iso())
            else:
                values.append(record.get(column))
        return values

    def _upsert_sql(self) -> str:
        columns = self._columns()
        insert_columns = ", ".join(columns)
        placeholders = ", ".join(["?"] * len(columns))
        update_columns = [column for column in columns if column not in {"research_candidate_id", "created_at"}]
        update_clause = ",\n                        ".join(f"{column} = excluded.{column}" for column in update_columns)
        return (
            f"""
            INSERT INTO strategy_evaluations ({insert_columns})
            VALUES ({placeholders})
            ON CONFLICT(research_candidate_id) DO UPDATE SET
                        {update_clause}
            """
        )

    def save_evaluations(self, payload: EvaluationPersistencePayload) -> dict[str, Any]:
        if not self.db.enabled:
            raise RuntimeError("Database is not enabled for evaluation persistence")
        self.db.ensure_schema()
        records = list(payload.records)
        if not records:
            return {"storage": "database", "stored_evaluation_count": 0, "saved_at": _utc_iso()}

        conn = self.db.conn
        sql = self._adapt_query(self._upsert_sql())
        with conn:
            cursor = conn.cursor()
            try:
                for record in records:
                    cursor.execute(sql, self._row_values(record))
            finally:
                cursor.close()

        return {
            "storage": "database",
            "stored_evaluation_count": len(records),
            "saved_at": _utc_iso(),
        }

    def insert_or_update_evaluation(self, record: dict[str, Any]) -> dict[str, Any]:
        return self.save_evaluations(EvaluationPersistencePayload(records=[record]))

    def _join_query(self, where_clause: str = "", order_clause: str = "ORDER BY COALESCE(e.completed_at, e.last_attempted_at, e.updated_at) DESC, e.symbol ASC", limit_clause: str = "") -> str:
        query = f"""
            SELECT
                e.*,
                c.rank,
                c.overall_score,
                c.confidence,
                c.signal,
                c.market_regime,
                c.sector,
                c.industry,
                c.company_name,
                c.latest_price AS candidate_latest_price,
                c.average_dollar_volume,
                c.ranking_score,
                c.created_at AS candidate_created_at,
                c.rejection_status,
                r.started_at AS research_started_at,
                r.completed_at AS research_completed_at,
                r.benchmark_symbol AS research_benchmark_symbol,
                r.scanner_version,
                r.strategy_version
            FROM strategy_evaluations e
            JOIN research_candidates c ON c.id = e.research_candidate_id
            JOIN research_runs r ON r.research_run_id = e.research_run_id
            {where_clause}
            {order_clause}
            {limit_clause}
        """
        return query

    def _query_joined(self, where_clause: str = "", params: tuple[Any, ...] = (), limit: int | None = None, order_clause: str | None = None) -> list[dict[str, Any]]:
        query = self._join_query(where_clause=where_clause, order_clause=order_clause or "ORDER BY COALESCE(e.completed_at, e.last_attempted_at, e.updated_at) DESC, e.symbol ASC", limit_clause=("LIMIT ?" if limit is not None else ""))
        final_params = params + ((int(limit),) if limit is not None else tuple())
        return self.db.query_all(query, final_params)

    def fetch_evaluation_by_candidate_id(self, candidate_id: int) -> dict[str, Any] | None:
        rows = self._query_joined("WHERE e.research_candidate_id = ?", (int(candidate_id),), limit=1)
        return rows[0] if rows else None

    def fetch_evaluations_by_research_run(self, research_run_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        return self._query_joined("WHERE e.research_run_id = ?", (str(research_run_id),), limit=limit)

    def fetch_evaluations_by_symbol(self, symbol: str, limit: int | None = None) -> list[dict[str, Any]]:
        return self._query_joined("WHERE e.symbol = ?", (str(symbol).upper(),), limit=limit)

    def fetch_evaluations_by_status(self, status: str, limit: int | None = None) -> list[dict[str, Any]]:
        return self._query_joined("WHERE e.label_status = ?", (str(status).lower(),), limit=limit)

    def fetch_completed_horizon_data(self, horizon: int, limit: int | None = None) -> list[dict[str, Any]]:
        prefix = f"forward_{int(horizon)}d"
        where_clause = f"WHERE e.{prefix}_status = 'complete'"
        return self._query_joined(
            where_clause,
            (),
            limit=limit,
            order_clause=f"ORDER BY CASE WHEN e.{prefix}_excess_return IS NULL THEN 1 ELSE 0 END, e.{prefix}_excess_return DESC, e.symbol ASC",
        )

    def fetch_pending_or_partial_candidates(self, research_run_id: str | None = None, symbol: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return self.fetch_labeling_candidates(research_run_id=research_run_id, symbol=symbol, limit=limit)

    def fetch_recent_labeled_observations(self, limit: int = 25) -> list[dict[str, Any]]:
        return self._query_joined(limit=limit)

    def fetch_recent_label_failures(self, limit: int = 25) -> list[dict[str, Any]]:
        return self._query_joined("WHERE e.label_status IN ('unavailable', 'data_error')", limit=limit)

    def fetch_evaluation_rows_for_dashboard(self, limit: int = 1000) -> list[dict[str, Any]]:
        return self._query_joined(limit=limit)

    def count_total_evaluations(self) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS n FROM strategy_evaluations") or {"n": 0}
        return int(row.get("n") or 0)

    def count_evaluations_by_status(self) -> list[dict[str, Any]]:
        return self.db.query_all(
            "SELECT label_status, COUNT(*) AS count FROM strategy_evaluations GROUP BY label_status ORDER BY count DESC, label_status ASC"
        )

    def fetch_latest_labeling_timestamp(self) -> dict[str, Any] | None:
        return self.db.query_one(
            "SELECT label_status, MAX(COALESCE(last_attempted_at, updated_at, created_at)) AS latest_attempted_at FROM strategy_evaluations"
        )

    def fetch_labeling_candidates(
        self,
        research_run_id: str | None = None,
        symbol: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses = ["(e.research_candidate_id IS NULL OR e.label_status IN ('pending', 'partial', 'data_error'))"]
        params: list[Any] = []
        if research_run_id:
            clauses.append("c.research_run_id = ?")
            params.append(str(research_run_id))
        if symbol:
            clauses.append("c.symbol = ?")
            params.append(str(symbol).upper())
        where_clause = "WHERE " + " AND ".join(clauses)
        query = f"""
            SELECT
                c.id AS research_candidate_id,
                c.research_run_id,
                c.symbol,
                c.created_at AS candidate_created_at,
                c.latest_price AS candidate_latest_price,
                c.rank,
                c.overall_score,
                c.confidence,
                c.signal,
                c.market_regime,
                c.sector,
                c.industry,
                c.average_dollar_volume,
                c.ranking_score,
                c.rejection_status,
                r.started_at AS research_started_at,
                r.completed_at AS research_completed_at,
                r.benchmark_symbol AS research_benchmark_symbol,
                e.label_status,
                e.last_attempted_at,
                e.completed_at AS evaluation_completed_at,
                e.error_message
            FROM research_candidates c
            JOIN research_runs r ON r.research_run_id = c.research_run_id
            LEFT JOIN strategy_evaluations e ON e.research_candidate_id = c.id
            {where_clause}
            ORDER BY COALESCE(c.rank, 2147483647), c.symbol ASC
            LIMIT ?
        """
        params.append(int(limit))
        return self.db.query_all(query, tuple(params))

    def close(self) -> None:
        self.db.close()


class JsonEvaluationRepository(EvaluationRepository):
    def __init__(self, root_path: str | Path = "research_state"):
        self.root = Path(root_path)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self) -> Path:
        return self.root / "strategy_evaluations.json"

    def _load(self) -> dict[str, Any]:
        path = self._path()
        if not path.exists():
            return {"evaluations": {}}
        return json.loads(path.read_text(encoding="utf-8"))

    def save_evaluations(self, payload: EvaluationPersistencePayload) -> dict[str, Any]:
        current = self._load()
        stored = current.setdefault("evaluations", {})
        for record in payload.records:
            candidate_id = str(record.get("research_candidate_id") or "")
            if not candidate_id:
                continue
            stored[candidate_id] = dict(record)
        path = self._path()
        path.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return {"storage": "json", "stored_evaluation_count": len(payload.records), "saved_at": _utc_iso()}

    def fetch_recent_labeled_observations(self, limit: int = 25) -> list[dict[str, Any]]:
        current = self._load()
        evaluations = list((current.get("evaluations") or {}).values())
        return sorted(evaluations, key=lambda item: str(item.get("updated_at") or ""), reverse=True)[: int(limit)]


def save_evaluation_results(
    evaluation_payloads: list[dict[str, Any]],
    database_url: str | None = None,
    json_fallback_dir: str | Path = "research_state",
) -> dict[str, Any]:
    payload = EvaluationPersistencePayload(records=evaluation_payloads)
    repository = MonitoringEvaluationRepository(database_url=database_url)
    try:
        if repository.db.enabled:
            return repository.save_evaluations(payload)
    except Exception:
        pass
    finally:
        repository.close()
    return JsonEvaluationRepository(root_path=json_fallback_dir).save_evaluations(payload)