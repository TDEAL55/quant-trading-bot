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
class PortfolioResearchRunPayload:
    run: dict[str, Any]
    snapshots: list[dict[str, Any]]


class MonitoringPortfolioResearchRepository:
    def __init__(self, database_url: str | None = None):
        self.db = MonitoringDatabase(database_url=database_url)

    def _adapt_query(self, query: str) -> str:
        return self.db._adapt_query(query)

    def save_run(self, payload: PortfolioResearchRunPayload) -> dict[str, Any]:
        if not self.db.enabled:
            raise RuntimeError("Database is not enabled for portfolio research persistence")
        self.db.ensure_schema()

        run = dict(payload.run)
        run_id = str(run.get("run_id") or "")
        if not run_id:
            raise ValueError("run_id is required")

        conn = self.db.conn
        with conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    self._adapt_query(
                        """
                        INSERT INTO portfolio_research_runs (
                            run_id, created_at, horizon, weighting_method, top_n,
                            maximum_position_weight, sector_cap, target_volatility,
                            benchmark, start_date, end_date, configuration_json,
                            portfolio_count, completed_count, skipped_count, status,
                            duration_seconds, error_message, performance_json,
                            analytics_json, method_comparison_json, walk_forward_json,
                            warnings_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(run_id) DO UPDATE SET
                            created_at = excluded.created_at,
                            horizon = excluded.horizon,
                            weighting_method = excluded.weighting_method,
                            top_n = excluded.top_n,
                            maximum_position_weight = excluded.maximum_position_weight,
                            sector_cap = excluded.sector_cap,
                            target_volatility = excluded.target_volatility,
                            benchmark = excluded.benchmark,
                            start_date = excluded.start_date,
                            end_date = excluded.end_date,
                            configuration_json = excluded.configuration_json,
                            portfolio_count = excluded.portfolio_count,
                            completed_count = excluded.completed_count,
                            skipped_count = excluded.skipped_count,
                            status = excluded.status,
                            duration_seconds = excluded.duration_seconds,
                            error_message = excluded.error_message,
                            performance_json = excluded.performance_json,
                            analytics_json = excluded.analytics_json,
                            method_comparison_json = excluded.method_comparison_json,
                            walk_forward_json = excluded.walk_forward_json,
                            warnings_json = excluded.warnings_json
                        """
                    ),
                    (
                        run_id,
                        run.get("created_at") or _utc_iso(),
                        run.get("horizon"),
                        run.get("weighting_method"),
                        run.get("top_n"),
                        run.get("maximum_position_weight"),
                        run.get("sector_cap"),
                        run.get("target_volatility"),
                        run.get("benchmark"),
                        run.get("start_date"),
                        run.get("end_date"),
                        _stable_json(run.get("configuration") or {}),
                        run.get("portfolio_count", 0),
                        run.get("completed_count", 0),
                        run.get("skipped_count", 0),
                        run.get("status") or "completed",
                        run.get("duration_seconds", 0.0),
                        run.get("error_message"),
                        _stable_json(run.get("performance") or {}),
                        _stable_json(run.get("analytics") or {}),
                        _stable_json(run.get("method_comparison") or []),
                        _stable_json(run.get("walk_forward") or {}),
                        _stable_json(run.get("warnings") or []),
                    ),
                )
                cursor.execute(self._adapt_query("DELETE FROM portfolio_research_snapshots WHERE run_id = ?"), (run_id,))
                for snapshot in payload.snapshots:
                    cursor.execute(
                        self._adapt_query(
                            """
                            INSERT INTO portfolio_research_snapshots (
                                run_id, snapshot_id, research_run_id, formation_date,
                                horizon, weighting_method, holding_count, invested_weight,
                                cash_weight, portfolio_return, benchmark_return, excess_return,
                                turnover, concentration_metrics_json, sector_exposure_json,
                                holdings_json, symbol_contribution_json,
                                sector_contribution_json, signal_contribution_json,
                                regime_contribution_json, warnings_json, status, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """
                        ),
                        (
                            run_id,
                            snapshot.get("snapshot_id"),
                            snapshot.get("research_run_id"),
                            snapshot.get("formation_date"),
                            snapshot.get("horizon"),
                            snapshot.get("weighting_method"),
                            snapshot.get("holding_count", 0),
                            snapshot.get("invested_weight", 0.0),
                            snapshot.get("cash_weight", 0.0),
                            snapshot.get("portfolio_return"),
                            snapshot.get("benchmark_return"),
                            snapshot.get("excess_return"),
                            snapshot.get("turnover"),
                            _stable_json(snapshot.get("concentration_metrics") or {}),
                            _stable_json(snapshot.get("sector_exposure") or {}),
                            _stable_json(snapshot.get("holdings") or []),
                            _stable_json(snapshot.get("symbol_contribution") or []),
                            _stable_json(snapshot.get("sector_contribution") or []),
                            _stable_json(snapshot.get("signal_contribution") or []),
                            _stable_json(snapshot.get("regime_contribution") or []),
                            _stable_json(snapshot.get("warnings") or []),
                            snapshot.get("status") or "completed",
                            snapshot.get("created_at") or _utc_iso(),
                        ),
                    )
            finally:
                cursor.close()
        return {
            "storage": "database",
            "run_id": run_id,
            "stored_snapshot_count": len(payload.snapshots),
            "saved_at": _utc_iso(),
        }

    def _normalize_run(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        normalized = dict(row)
        normalized["configuration"] = _json_load(row.get("configuration_json"), {})
        normalized["performance"] = _json_load(row.get("performance_json"), {})
        normalized["analytics"] = _json_load(row.get("analytics_json"), {})
        normalized["method_comparison"] = _json_load(row.get("method_comparison_json"), [])
        normalized["walk_forward"] = _json_load(row.get("walk_forward_json"), {})
        normalized["warnings"] = _json_load(row.get("warnings_json"), [])
        return normalized

    def _normalize_snapshot(self, row: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(row)
        normalized["concentration_metrics"] = _json_load(row.get("concentration_metrics_json"), {})
        normalized["sector_exposure"] = _json_load(row.get("sector_exposure_json"), {})
        normalized["holdings"] = _json_load(row.get("holdings_json"), [])
        normalized["symbol_contribution"] = _json_load(row.get("symbol_contribution_json"), [])
        normalized["sector_contribution"] = _json_load(row.get("sector_contribution_json"), [])
        normalized["signal_contribution"] = _json_load(row.get("signal_contribution_json"), [])
        normalized["regime_contribution"] = _json_load(row.get("regime_contribution_json"), [])
        normalized["warnings"] = _json_load(row.get("warnings_json"), [])
        return normalized

    def fetch_run(self, run_id: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM portfolio_research_runs WHERE run_id = ?", (str(run_id),))
        return self._normalize_run(row)

    def fetch_latest_run(self) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM portfolio_research_runs ORDER BY created_at DESC LIMIT 1")
        return self._normalize_run(row)

    def count_runs(self) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS n FROM portfolio_research_runs") or {"n": 0}
        return int(row.get("n") or 0)

    def fetch_snapshots_for_run(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            "SELECT * FROM portfolio_research_snapshots WHERE run_id = ? ORDER BY formation_date ASC, research_run_id ASC, snapshot_id ASC",
            (str(run_id),),
        )
        return [self._normalize_snapshot(row) for row in rows]

    def fetch_recent_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.db.query_all("SELECT * FROM portfolio_research_runs ORDER BY created_at DESC LIMIT ?", (int(limit),))
        return [self._normalize_run(row) for row in rows if row]

    def close(self) -> None:
        self.db.close()
