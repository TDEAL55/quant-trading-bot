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


def _json_loads(value: Any, default: Any):
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


@dataclass(frozen=True)
class WalkForwardRunPayload:
    run: dict[str, Any]
    windows: list[dict[str, Any]]


class MonitoringWalkForwardRepository:
    def __init__(self, database_url: str | None = None):
        self.db = MonitoringDatabase(database_url=database_url)

    def _adapt_query(self, query: str) -> str:
        return self.db._adapt_query(query)

    def save_run(self, payload: WalkForwardRunPayload) -> dict[str, Any]:
        if not self.db.enabled:
            raise RuntimeError("Database is not enabled for walk-forward persistence")
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
                        INSERT INTO walk_forward_runs (
                            run_id, created_at, window_type, training_periods, validation_periods,
                            step_periods, horizon, benchmark_symbol, configuration_snapshot_json,
                            total_windows, completed_windows, skipped_windows, scorecard_json,
                            factor_stability_summary_json, performance_decay_json, regime_robustness_json,
                            performance_json, status, duration_seconds,
                            error_message
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(run_id) DO UPDATE SET
                            created_at = excluded.created_at,
                            window_type = excluded.window_type,
                            training_periods = excluded.training_periods,
                            validation_periods = excluded.validation_periods,
                            step_periods = excluded.step_periods,
                            horizon = excluded.horizon,
                            benchmark_symbol = excluded.benchmark_symbol,
                            configuration_snapshot_json = excluded.configuration_snapshot_json,
                            total_windows = excluded.total_windows,
                            completed_windows = excluded.completed_windows,
                            skipped_windows = excluded.skipped_windows,
                            scorecard_json = excluded.scorecard_json,
                            factor_stability_summary_json = excluded.factor_stability_summary_json,
                            performance_decay_json = excluded.performance_decay_json,
                            regime_robustness_json = excluded.regime_robustness_json,
                            performance_json = excluded.performance_json,
                            status = excluded.status,
                            duration_seconds = excluded.duration_seconds,
                            error_message = excluded.error_message
                        """
                    ),
                    (
                        run_id,
                        run.get("created_at") or _utc_iso(),
                        run.get("window_type"),
                        run.get("training_periods"),
                        run.get("validation_periods"),
                        run.get("step_periods"),
                        run.get("horizon"),
                        run.get("benchmark_symbol"),
                        _stable_json(run.get("configuration_snapshot") or {}),
                        run.get("total_windows", 0),
                        run.get("completed_windows", 0),
                        run.get("skipped_windows", 0),
                        _stable_json(run.get("scorecard") or {}),
                        _stable_json(run.get("factor_stability_summary") or []),
                        _stable_json(run.get("performance_decay") or {}),
                        _stable_json(run.get("regime_robustness") or []),
                        _stable_json(run.get("performance") or {}),
                        run.get("status") or "completed",
                        run.get("duration_seconds") or 0.0,
                        run.get("error_message"),
                    ),
                )
                cursor.execute(self._adapt_query("DELETE FROM walk_forward_windows WHERE run_id = ?"), (run_id,))
                for window in payload.windows:
                    cursor.execute(
                        self._adapt_query(
                            """
                            INSERT INTO walk_forward_windows (
                                run_id, window_id, training_start_date, training_end_date,
                                validation_start_date, validation_end_date, training_observation_count,
                                validation_observation_count, horizon, benchmark_symbol, window_type,
                                training_metrics_json, validation_metrics_json, degradation_metrics_json,
                                factor_stability_json, regime_metrics_json, warnings_json, status, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """
                        ),
                        (
                            run_id,
                            window.get("window_id"),
                            window.get("training_start_date"),
                            window.get("training_end_date"),
                            window.get("validation_start_date"),
                            window.get("validation_end_date"),
                            window.get("training_observation_count", 0),
                            window.get("validation_observation_count", 0),
                            window.get("horizon"),
                            window.get("benchmark_symbol"),
                            window.get("window_type"),
                            _stable_json(window.get("training_metrics") or {}),
                            _stable_json(window.get("validation_metrics") or {}),
                            _stable_json(window.get("degradation_metrics") or {}),
                            _stable_json(window.get("factor_stability") or {}),
                            _stable_json(window.get("regime_metrics") or {}),
                            _stable_json(window.get("warnings") or []),
                            window.get("status") or "completed",
                            window.get("created_at") or _utc_iso(),
                        ),
                    )
            finally:
                cursor.close()
        return {"storage": "database", "run_id": run_id, "stored_window_count": len(payload.windows), "saved_at": _utc_iso()}

    def _normalize_run_row(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        normalized = dict(row)
        normalized["configuration_snapshot"] = _json_loads(row.get("configuration_snapshot_json"), {})
        normalized["scorecard"] = _json_loads(row.get("scorecard_json"), {})
        normalized["factor_stability_summary"] = _json_loads(row.get("factor_stability_summary_json"), [])
        normalized["performance_decay"] = _json_loads(row.get("performance_decay_json"), {})
        normalized["regime_robustness"] = _json_loads(row.get("regime_robustness_json"), [])
        normalized["performance"] = _json_loads(row.get("performance_json"), {})
        return normalized

    def _normalize_window_row(self, row: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(row)
        normalized["training_metrics"] = _json_loads(row.get("training_metrics_json"), {})
        normalized["validation_metrics"] = _json_loads(row.get("validation_metrics_json"), {})
        normalized["degradation_metrics"] = _json_loads(row.get("degradation_metrics_json"), {})
        normalized["factor_stability"] = _json_loads(row.get("factor_stability_json"), {})
        normalized["regime_metrics"] = _json_loads(row.get("regime_metrics_json"), {})
        normalized["warnings"] = _json_loads(row.get("warnings_json"), [])
        return normalized

    def fetch_run(self, run_id: str) -> dict[str, Any] | None:
        return self._normalize_run_row(self.db.query_one("SELECT * FROM walk_forward_runs WHERE run_id = ?", (str(run_id),)))

    def fetch_windows_for_run(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.db.query_all("SELECT * FROM walk_forward_windows WHERE run_id = ? ORDER BY validation_start_date ASC, window_id ASC", (str(run_id),))
        return [self._normalize_window_row(row) for row in rows]

    def fetch_recent_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.db.query_all("SELECT * FROM walk_forward_runs ORDER BY created_at DESC LIMIT ?", (int(limit),))
        return [self._normalize_run_row(row) for row in rows if row]

    def fetch_latest_run(self) -> dict[str, Any] | None:
        return self._normalize_run_row(self.db.query_one("SELECT * FROM walk_forward_runs ORDER BY created_at DESC LIMIT 1"))

    def count_runs(self) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS n FROM walk_forward_runs") or {"n": 0}
        return int(row.get("n") or 0)

    def close(self) -> None:
        self.db.close()
