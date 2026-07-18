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
class PaperValidationRunPayload:
    run: dict[str, Any]
    orders: list[dict[str, Any]]
    position_snapshots: list[dict[str, Any]]


class MonitoringPaperExecutionRepository:
    def __init__(self, database_url: str | None = None):
        self.db = MonitoringDatabase(database_url=database_url)

    def _adapt_query(self, query: str) -> str:
        return self.db._adapt_query(query)

    def create_approval(self, approval: dict[str, Any]) -> dict[str, Any]:
        if not self.db.enabled:
            raise RuntimeError("Database is not enabled for paper approval persistence")
        self.db.ensure_schema()
        now = _utc_iso()
        payload = dict(approval)
        payload.setdefault("created_at", now)
        payload.setdefault("updated_at", now)
        conn = self.db.conn
        with conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    self._adapt_query(
                        """
                        INSERT INTO paper_strategy_approvals (
                            approval_id, strategy_id, strategy_version, strategy_fingerprint,
                            portfolio_configuration_json, risk_configuration_json,
                            benchmark, horizon, approved_by, approved_at, expires_at,
                            enabled, notes, configuration_fingerprint, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(approval_id) DO UPDATE SET
                            strategy_id = excluded.strategy_id,
                            strategy_version = excluded.strategy_version,
                            strategy_fingerprint = excluded.strategy_fingerprint,
                            portfolio_configuration_json = excluded.portfolio_configuration_json,
                            risk_configuration_json = excluded.risk_configuration_json,
                            benchmark = excluded.benchmark,
                            horizon = excluded.horizon,
                            approved_by = excluded.approved_by,
                            approved_at = excluded.approved_at,
                            expires_at = excluded.expires_at,
                            enabled = excluded.enabled,
                            notes = excluded.notes,
                            configuration_fingerprint = excluded.configuration_fingerprint,
                            updated_at = excluded.updated_at
                        """
                    ),
                    (
                        payload.get("approval_id"),
                        payload.get("strategy_id"),
                        payload.get("strategy_version"),
                        payload.get("strategy_fingerprint"),
                        _stable_json(payload.get("portfolio_configuration") or {}),
                        _stable_json(payload.get("risk_configuration") or {}),
                        payload.get("benchmark"),
                        int(payload.get("horizon") or 0),
                        payload.get("approved_by"),
                        payload.get("approved_at"),
                        payload.get("expires_at"),
                        1 if bool(payload.get("enabled", True)) else 0,
                        payload.get("notes"),
                        payload.get("configuration_fingerprint"),
                        payload.get("created_at"),
                        payload.get("updated_at"),
                    ),
                )
            finally:
                cursor.close()
        return {"storage": "database", "approval_id": payload.get("approval_id"), "saved_at": _utc_iso()}

    def disable_approval(self, approval_id: str) -> dict[str, Any]:
        if not self.db.enabled:
            raise RuntimeError("Database is not enabled for paper approval persistence")
        self.db.ensure_schema()
        self.db.execute(
            "UPDATE paper_strategy_approvals SET enabled = 0, updated_at = ? WHERE approval_id = ?",
            (_utc_iso(), str(approval_id)),
        )
        return {"approval_id": str(approval_id), "disabled": True, "saved_at": _utc_iso()}

    def fetch_approval(self, approval_id: str) -> dict[str, Any] | None:
        if not self.db.enabled:
            return None
        self.db.ensure_schema()
        row = self.db.query_one("SELECT * FROM paper_strategy_approvals WHERE approval_id = ?", (str(approval_id),))
        return self._normalize_approval(row)

    def list_approvals(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        if not self.db.enabled:
            return []
        self.db.ensure_schema()
        if enabled_only:
            rows = self.db.query_all("SELECT * FROM paper_strategy_approvals WHERE enabled = 1 ORDER BY approved_at DESC")
        else:
            rows = self.db.query_all("SELECT * FROM paper_strategy_approvals ORDER BY approved_at DESC")
        return [item for item in (self._normalize_approval(row) for row in rows) if item is not None]

    def _normalize_approval(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        item = dict(row)
        item["portfolio_configuration"] = _json_load(row.get("portfolio_configuration_json"), {})
        item["risk_configuration"] = _json_load(row.get("risk_configuration_json"), {})
        item["enabled"] = bool(int(row.get("enabled") or 0))
        return item

    def save_validation_run(self, payload: PaperValidationRunPayload) -> dict[str, Any]:
        if not self.db.enabled:
            raise RuntimeError("Database is not enabled for paper validation persistence")
        self.db.ensure_schema()

        run = dict(payload.run)
        run_id = str(run.get("run_id") or "")
        if not run_id:
            raise ValueError("run_id is required")

        now = _utc_iso()
        run.setdefault("created_at", now)
        run.setdefault("updated_at", now)

        conn = self.db.conn
        with conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    self._adapt_query(
                        """
                        INSERT INTO paper_validation_runs (
                            run_id, run_fingerprint, execution_fingerprint, approval_id, strategy_id, strategy_version,
                            strategy_fingerprint, research_run_id, scanner_timestamp, started_at,
                            completed_at, mode, status, dry_run, proposed_order_count,
                            approved_order_count, rejected_order_count, submitted_order_count,
                            filled_order_count, failed_order_count, configuration_json,
                            risk_snapshot_json, performance_json, warnings_json, error_message,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(run_id) DO UPDATE SET
                            run_fingerprint = excluded.run_fingerprint,
                            execution_fingerprint = excluded.execution_fingerprint,
                            approval_id = excluded.approval_id,
                            strategy_id = excluded.strategy_id,
                            strategy_version = excluded.strategy_version,
                            strategy_fingerprint = excluded.strategy_fingerprint,
                            research_run_id = excluded.research_run_id,
                            scanner_timestamp = excluded.scanner_timestamp,
                            started_at = excluded.started_at,
                            completed_at = excluded.completed_at,
                            mode = excluded.mode,
                            status = excluded.status,
                            dry_run = excluded.dry_run,
                            proposed_order_count = excluded.proposed_order_count,
                            approved_order_count = excluded.approved_order_count,
                            rejected_order_count = excluded.rejected_order_count,
                            submitted_order_count = excluded.submitted_order_count,
                            filled_order_count = excluded.filled_order_count,
                            failed_order_count = excluded.failed_order_count,
                            configuration_json = excluded.configuration_json,
                            risk_snapshot_json = excluded.risk_snapshot_json,
                            performance_json = excluded.performance_json,
                            warnings_json = excluded.warnings_json,
                            error_message = excluded.error_message,
                            updated_at = excluded.updated_at
                        """
                    ),
                    (
                        run_id,
                        run.get("run_fingerprint"),
                        run.get("execution_fingerprint"),
                        run.get("approval_id"),
                        run.get("strategy_id"),
                        run.get("strategy_version"),
                        run.get("strategy_fingerprint"),
                        run.get("research_run_id"),
                        run.get("scanner_timestamp"),
                        run.get("started_at"),
                        run.get("completed_at"),
                        run.get("mode"),
                        run.get("status"),
                        1 if bool(run.get("dry_run", True)) else 0,
                        int(run.get("proposed_order_count") or 0),
                        int(run.get("approved_order_count") or 0),
                        int(run.get("rejected_order_count") or 0),
                        int(run.get("submitted_order_count") or 0),
                        int(run.get("filled_order_count") or 0),
                        int(run.get("failed_order_count") or 0),
                        _stable_json(run.get("configuration") or {}),
                        _stable_json(run.get("risk_snapshot") or {}),
                        _stable_json(run.get("performance") or {}),
                        _stable_json(run.get("warnings") or []),
                        run.get("error_message"),
                        run.get("created_at"),
                        run.get("updated_at"),
                    ),
                )

                cursor.execute(self._adapt_query("DELETE FROM paper_orders WHERE run_id = ?"), (run_id,))
                cursor.execute(self._adapt_query("DELETE FROM paper_position_snapshots WHERE run_id = ?"), (run_id,))

                for order in payload.orders:
                    item = dict(order)
                    item.setdefault("created_at", now)
                    item.setdefault("updated_at", now)
                    cursor.execute(
                        self._adapt_query(
                            """
                            INSERT INTO paper_orders (
                                paper_order_id, run_id, symbol, side, quantity, notional,
                                target_weight, current_weight, weight_delta, reference_price,
                                proposed_at, risk_status, risk_reason, submission_status,
                                broker_order_id, submitted_at, filled_quantity,
                                average_fill_price, filled_at, canceled_at, failed_at,
                                error_message, order_payload_json, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """
                        ),
                        (
                            item.get("paper_order_id"),
                            run_id,
                            item.get("symbol"),
                            item.get("side"),
                            float(item.get("quantity") or 0.0),
                            float(item.get("notional") or 0.0),
                            item.get("target_weight"),
                            item.get("current_weight"),
                            item.get("weight_delta"),
                            item.get("reference_price"),
                            item.get("proposed_at") or now,
                            item.get("risk_status") or "rejected",
                            item.get("risk_reason"),
                            item.get("submission_status") or "not_submitted",
                            item.get("broker_order_id"),
                            item.get("submitted_at"),
                            item.get("filled_quantity"),
                            item.get("average_fill_price"),
                            item.get("filled_at"),
                            item.get("canceled_at"),
                            item.get("failed_at"),
                            item.get("error_message"),
                            _stable_json(item.get("order_payload") or {}),
                            item.get("created_at"),
                            item.get("updated_at"),
                        ),
                    )

                for snapshot in payload.position_snapshots:
                    item = dict(snapshot)
                    cursor.execute(
                        self._adapt_query(
                            """
                            INSERT INTO paper_position_snapshots (
                                snapshot_id, run_id, captured_at, positions_json, cash,
                                buying_power, portfolio_value, gross_exposure, net_exposure,
                                concentration_json, reconciliation_status, warnings_json
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """
                        ),
                        (
                            item.get("snapshot_id"),
                            run_id,
                            item.get("captured_at") or now,
                            _stable_json(item.get("positions") or {}),
                            item.get("cash"),
                            item.get("buying_power"),
                            item.get("portfolio_value"),
                            item.get("gross_exposure"),
                            item.get("net_exposure"),
                            _stable_json(item.get("concentration") or {}),
                            item.get("reconciliation_status"),
                            _stable_json(item.get("warnings") or []),
                        ),
                    )
            finally:
                cursor.close()

        return {"storage": "database", "run_id": run_id, "saved_at": _utc_iso(), "order_count": len(payload.orders)}

    def fetch_latest_submitting_run_by_execution_fingerprint(self, execution_fingerprint: str) -> dict[str, Any] | None:
        if not self.db.enabled:
            return None
        self.db.ensure_schema()
        row = self.db.query_one(
            """
            SELECT *
            FROM paper_validation_runs
            WHERE execution_fingerprint = ?
              AND dry_run = 0
              AND submitted_order_count > 0
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (str(execution_fingerprint),),
        )
        return self._normalize_run(row)

    def fetch_run_by_fingerprint(self, run_fingerprint: str) -> dict[str, Any] | None:
        if not self.db.enabled:
            return None
        self.db.ensure_schema()
        row = self.db.query_one("SELECT * FROM paper_validation_runs WHERE run_fingerprint = ?", (str(run_fingerprint),))
        return self._normalize_run(row)

    def fetch_latest_run(self) -> dict[str, Any] | None:
        if not self.db.enabled:
            return None
        self.db.ensure_schema()
        row = self.db.query_one("SELECT * FROM paper_validation_runs ORDER BY started_at DESC LIMIT 1")
        return self._normalize_run(row)

    def fetch_run(self, run_id: str) -> dict[str, Any] | None:
        if not self.db.enabled:
            return None
        self.db.ensure_schema()
        row = self.db.query_one("SELECT * FROM paper_validation_runs WHERE run_id = ?", (str(run_id),))
        return self._normalize_run(row)

    def list_runs(self, limit: int = 25) -> list[dict[str, Any]]:
        if not self.db.enabled:
            return []
        self.db.ensure_schema()
        rows = self.db.query_all("SELECT * FROM paper_validation_runs ORDER BY started_at DESC LIMIT ?", (int(limit),))
        return [item for item in (self._normalize_run(row) for row in rows) if item is not None]

    def fetch_orders_for_run(self, run_id: str) -> list[dict[str, Any]]:
        if not self.db.enabled:
            return []
        self.db.ensure_schema()
        rows = self.db.query_all("SELECT * FROM paper_orders WHERE run_id = ? ORDER BY proposed_at ASC, symbol ASC", (str(run_id),))
        result = []
        for row in rows:
            item = dict(row)
            item["order_payload"] = _json_load(row.get("order_payload_json"), {})
            result.append(item)
        return result

    def fetch_position_snapshots_for_run(self, run_id: str) -> list[dict[str, Any]]:
        if not self.db.enabled:
            return []
        self.db.ensure_schema()
        rows = self.db.query_all("SELECT * FROM paper_position_snapshots WHERE run_id = ? ORDER BY captured_at ASC", (str(run_id),))
        result = []
        for row in rows:
            item = dict(row)
            item["positions"] = _json_load(row.get("positions_json"), {})
            item["concentration"] = _json_load(row.get("concentration_json"), {})
            item["warnings"] = _json_load(row.get("warnings_json"), [])
            result.append(item)
        return result

    def _normalize_run(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        item = dict(row)
        item["dry_run"] = bool(int(row.get("dry_run") or 0))
        item["configuration"] = _json_load(row.get("configuration_json"), {})
        item["risk_snapshot"] = _json_load(row.get("risk_snapshot_json"), {})
        item["performance"] = _json_load(row.get("performance_json"), {})
        item["warnings"] = _json_load(row.get("warnings_json"), [])
        return item

    def close(self) -> None:
        self.db.close()
