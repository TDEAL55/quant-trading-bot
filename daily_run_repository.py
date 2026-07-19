from __future__ import annotations

import json
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


class DailyRunRepository:
    def __init__(self, database_url: str | None = None):
        self.db = MonitoringDatabase(database_url=database_url)

    def save_run(self, row: dict[str, Any]) -> dict[str, Any]:
        if not self.db.enabled:
            raise RuntimeError("Database is not enabled for daily run persistence")
        self.db.ensure_schema()
        now = _utc_iso()
        item = dict(row)
        item.setdefault("created_at", now)
        item.setdefault("updated_at", now)

        self.db.execute(
            """
            INSERT INTO daily_runs (
                run_id, timestamp, market_session, market_status, candidate_count,
                qualified_count, selected_symbols_json, execution_status,
                performance_run_id, paper_validation_run_id, report_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                timestamp = excluded.timestamp,
                market_session = excluded.market_session,
                market_status = excluded.market_status,
                candidate_count = excluded.candidate_count,
                qualified_count = excluded.qualified_count,
                selected_symbols_json = excluded.selected_symbols_json,
                execution_status = excluded.execution_status,
                performance_run_id = excluded.performance_run_id,
                paper_validation_run_id = excluded.paper_validation_run_id,
                report_json = excluded.report_json,
                updated_at = excluded.updated_at
            """,
            (
                item.get("run_id"),
                item.get("timestamp"),
                item.get("market_session"),
                item.get("market_status"),
                int(item.get("candidate_count") or 0),
                int(item.get("qualified_count") or 0),
                _stable_json(item.get("selected_symbols") or []),
                item.get("execution_status"),
                item.get("performance_run_id"),
                item.get("paper_validation_run_id"),
                _stable_json(item.get("report") or {}),
                item.get("created_at"),
                item.get("updated_at"),
            ),
        )
        return {"storage": "database", "run_id": item.get("run_id"), "saved_at": _utc_iso()}

    def latest_run(self) -> dict[str, Any] | None:
        if not self.db.enabled:
            return None
        self.db.ensure_schema()
        row = self.db.query_one("SELECT * FROM daily_runs ORDER BY timestamp DESC LIMIT 1")
        return self._normalize_row(row)

    def list_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.db.enabled:
            return []
        self.db.ensure_schema()
        rows = self.db.query_all("SELECT * FROM daily_runs ORDER BY timestamp DESC LIMIT ?", (int(limit),))
        return [item for item in (self._normalize_row(row) for row in rows) if item is not None]

    def dashboard_payload(self) -> dict[str, Any]:
        return {
            "db_connected": self.db.enabled,
            "latest_run": self.latest_run() or {},
            "history": self.list_runs(limit=100),
        }

    def _normalize_row(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        item = dict(row)
        item["selected_symbols"] = _json_load(item.get("selected_symbols_json"), [])
        item["report"] = _json_load(item.get("report_json"), {})
        return item

    def close(self) -> None:
        self.db.close()
