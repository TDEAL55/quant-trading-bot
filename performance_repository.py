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
class PerformanceRunPayload:
    run: dict[str, Any]
    daily_equity: list[dict[str, Any]]
    portfolio_snapshots: list[dict[str, Any]]
    trade_statistics: list[dict[str, Any]]
    metrics: list[dict[str, Any]]


class PerformanceRepository:
    def __init__(self, database_url: str | None = None):
        self.db = MonitoringDatabase(database_url=database_url)

    def _adapt_query(self, query: str) -> str:
        return self.db._adapt_query(query)

    def fetch_source_runs(self, limit: int = 1000) -> list[dict[str, Any]]:
        if not self.db.enabled:
            return []
        self.db.ensure_schema()
        rows = self.db.query_all(
            """
            SELECT *
            FROM paper_validation_runs
            WHERE dry_run = 0 AND status = 'completed' AND submitted_order_count > 0
            ORDER BY completed_at ASC
            LIMIT ?
            """,
            (int(limit),),
        )
        result = []
        for row in rows:
            item = dict(row)
            item["configuration"] = _json_load(item.get("configuration_json"), {})
            item["risk_snapshot"] = _json_load(item.get("risk_snapshot_json"), {})
            item["performance"] = _json_load(item.get("performance_json"), {})
            result.append(item)
        return result

    def fetch_orders_for_run(self, source_run_id: str) -> list[dict[str, Any]]:
        if not self.db.enabled:
            return []
        self.db.ensure_schema()
        rows = self.db.query_all(
            "SELECT * FROM paper_orders WHERE run_id = ? ORDER BY proposed_at ASC",
            (str(source_run_id),),
        )
        result = []
        for row in rows:
            item = dict(row)
            item["order_payload"] = _json_load(item.get("order_payload_json"), {})
            result.append(item)
        return result

    def fetch_snapshots_for_run(self, source_run_id: str) -> list[dict[str, Any]]:
        if not self.db.enabled:
            return []
        self.db.ensure_schema()
        rows = self.db.query_all(
            "SELECT * FROM paper_position_snapshots WHERE run_id = ? ORDER BY captured_at ASC",
            (str(source_run_id),),
        )
        result = []
        for row in rows:
            item = dict(row)
            item["positions"] = _json_load(item.get("positions_json"), {})
            item["concentration"] = _json_load(item.get("concentration_json"), {})
            item["warnings"] = _json_load(item.get("warnings_json"), [])
            result.append(item)
        return result

    def save_run(self, payload: PerformanceRunPayload) -> dict[str, Any]:
        if not self.db.enabled:
            raise RuntimeError("Database is not enabled for performance persistence")
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
                        INSERT INTO performance_runs (
                            run_id, started_at, completed_at, status, source_run_count, source_trade_count,
                            analysis_start_date, analysis_end_date, benchmark_symbol, configuration_json,
                            warnings_json, error_message, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(run_id) DO UPDATE SET
                            completed_at = excluded.completed_at,
                            status = excluded.status,
                            source_run_count = excluded.source_run_count,
                            source_trade_count = excluded.source_trade_count,
                            analysis_start_date = excluded.analysis_start_date,
                            analysis_end_date = excluded.analysis_end_date,
                            benchmark_symbol = excluded.benchmark_symbol,
                            configuration_json = excluded.configuration_json,
                            warnings_json = excluded.warnings_json,
                            error_message = excluded.error_message,
                            updated_at = excluded.updated_at
                        """
                    ),
                    (
                        run_id,
                        run.get("started_at"),
                        run.get("completed_at"),
                        run.get("status"),
                        int(run.get("source_run_count") or 0),
                        int(run.get("source_trade_count") or 0),
                        run.get("analysis_start_date"),
                        run.get("analysis_end_date"),
                        run.get("benchmark_symbol") or "SPY",
                        _stable_json(run.get("configuration") or {}),
                        _stable_json(run.get("warnings") or []),
                        run.get("error_message"),
                        run.get("created_at"),
                        run.get("updated_at"),
                    ),
                )

                cursor.execute(self._adapt_query("DELETE FROM daily_equity WHERE run_id = ?"), (run_id,))
                cursor.execute(self._adapt_query("DELETE FROM portfolio_snapshots WHERE run_id = ?"), (run_id,))
                cursor.execute(self._adapt_query("DELETE FROM trade_statistics WHERE run_id = ?"), (run_id,))
                cursor.execute(self._adapt_query("DELETE FROM performance_metrics WHERE run_id = ?"), (run_id,))

                for row in payload.daily_equity:
                    item = dict(row)
                    cursor.execute(
                        self._adapt_query(
                            """
                            INSERT INTO daily_equity (
                                daily_equity_id, run_id, equity_date, portfolio_value, cash, buying_power,
                                daily_return, total_return, cumulative_return, max_drawdown, current_drawdown,
                                volatility, turnover, exposure_pct, position_concentration, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """
                        ),
                        (
                            item.get("daily_equity_id"),
                            run_id,
                            item.get("equity_date"),
                            float(item.get("portfolio_value") or 0.0),
                            float(item.get("cash") or 0.0),
                            float(item.get("buying_power") or 0.0),
                            item.get("daily_return"),
                            item.get("total_return"),
                            item.get("cumulative_return"),
                            item.get("max_drawdown"),
                            item.get("current_drawdown"),
                            item.get("volatility"),
                            item.get("turnover"),
                            item.get("exposure_pct"),
                            item.get("position_concentration"),
                            item.get("created_at") or now,
                        ),
                    )

                for row in payload.portfolio_snapshots:
                    item = dict(row)
                    cursor.execute(
                        self._adapt_query(
                            """
                            INSERT INTO portfolio_snapshots (
                                snapshot_id, run_id, source_validation_run_id, captured_at, portfolio_value,
                                cash, buying_power, exposure_pct, position_concentration,
                                sector_allocation_json, positions_json, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """
                        ),
                        (
                            item.get("snapshot_id"),
                            run_id,
                            item.get("source_validation_run_id"),
                            item.get("captured_at"),
                            float(item.get("portfolio_value") or 0.0),
                            float(item.get("cash") or 0.0),
                            float(item.get("buying_power") or 0.0),
                            item.get("exposure_pct"),
                            item.get("position_concentration"),
                            _stable_json(item.get("sector_allocation") or {}),
                            _stable_json(item.get("positions") or {}),
                            item.get("created_at") or now,
                        ),
                    )

                for row in payload.trade_statistics:
                    item = dict(row)
                    cursor.execute(
                        self._adapt_query(
                            """
                            INSERT INTO trade_statistics (
                                trade_stat_id, run_id, source_validation_run_id, trade_date, trade_count,
                                win_rate, loss_rate, average_winner, average_loser, profit_factor,
                                largest_winner, largest_loser, average_hold_time_days, turnover, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """
                        ),
                        (
                            item.get("trade_stat_id"),
                            run_id,
                            item.get("source_validation_run_id"),
                            item.get("trade_date"),
                            int(item.get("trade_count") or 0),
                            item.get("win_rate"),
                            item.get("loss_rate"),
                            item.get("average_winner"),
                            item.get("average_loser"),
                            item.get("profit_factor"),
                            item.get("largest_winner"),
                            item.get("largest_loser"),
                            item.get("average_hold_time_days"),
                            item.get("turnover"),
                            item.get("created_at") or now,
                        ),
                    )

                for row in payload.metrics:
                    item = dict(row)
                    cursor.execute(
                        self._adapt_query(
                            """
                            INSERT INTO performance_metrics (
                                metric_id, run_id, metric_group, metric_name, metric_value,
                                as_of_date, metadata_json, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """
                        ),
                        (
                            item.get("metric_id"),
                            run_id,
                            item.get("metric_group"),
                            item.get("metric_name"),
                            item.get("metric_value"),
                            item.get("as_of_date"),
                            _stable_json(item.get("metadata") or {}),
                            item.get("created_at") or now,
                        ),
                    )
            finally:
                cursor.close()

        return {
            "storage": "database",
            "run_id": run_id,
            "saved_at": _utc_iso(),
            "daily_equity_rows": len(payload.daily_equity),
            "snapshot_rows": len(payload.portfolio_snapshots),
            "trade_stat_rows": len(payload.trade_statistics),
            "metric_rows": len(payload.metrics),
        }

    def latest_run(self) -> dict[str, Any] | None:
        if not self.db.enabled:
            return None
        self.db.ensure_schema()
        row = self.db.query_one("SELECT * FROM performance_runs ORDER BY completed_at DESC LIMIT 1")
        return self._normalize_run(row)

    def fetch_daily_equity(self, run_id: str) -> list[dict[str, Any]]:
        if not self.db.enabled:
            return []
        self.db.ensure_schema()
        return self.db.query_all("SELECT * FROM daily_equity WHERE run_id = ? ORDER BY equity_date ASC", (str(run_id),))

    def fetch_trade_statistics(self, run_id: str) -> list[dict[str, Any]]:
        if not self.db.enabled:
            return []
        self.db.ensure_schema()
        return self.db.query_all("SELECT * FROM trade_statistics WHERE run_id = ? ORDER BY trade_date ASC", (str(run_id),))

    def fetch_portfolio_snapshots(self, run_id: str) -> list[dict[str, Any]]:
        if not self.db.enabled:
            return []
        self.db.ensure_schema()
        rows = self.db.query_all("SELECT * FROM portfolio_snapshots WHERE run_id = ? ORDER BY captured_at ASC", (str(run_id),))
        result = []
        for row in rows:
            item = dict(row)
            item["sector_allocation"] = _json_load(item.get("sector_allocation_json"), {})
            item["positions"] = _json_load(item.get("positions_json"), {})
            result.append(item)
        return result

    def fetch_metrics(self, run_id: str) -> list[dict[str, Any]]:
        if not self.db.enabled:
            return []
        self.db.ensure_schema()
        rows = self.db.query_all("SELECT * FROM performance_metrics WHERE run_id = ? ORDER BY metric_group, metric_name", (str(run_id),))
        result = []
        for row in rows:
            item = dict(row)
            item["metadata"] = _json_load(item.get("metadata_json"), {})
            result.append(item)
        return result

    def _normalize_run(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        item = dict(row)
        item["configuration"] = _json_load(item.get("configuration_json"), {})
        item["warnings"] = _json_load(item.get("warnings_json"), [])
        return item

    def close(self) -> None:
        self.db.close()
