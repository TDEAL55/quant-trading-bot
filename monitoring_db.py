import os
import contextlib
import sqlite3
import json
from pathlib import Path
from typing import Any

try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:  # pragma: no cover - optional dependency for tests/local runs.
    psycopg = None
    dict_row = None


def _is_postgres_url(url: str) -> bool:
    return str(url).startswith("postgres://") or str(url).startswith("postgresql://")


def _is_sqlite_url(url: str) -> bool:
    return str(url).startswith("sqlite:///")


class MonitoringDatabase:
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or os.getenv("DATABASE_URL", "")
        self.engine = None
        self.conn = None

        if not self.database_url:
            return

        if _is_sqlite_url(self.database_url):
            db_path = self.database_url.replace("sqlite:///", "", 1)
            path = Path(db_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(path))
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA busy_timeout=5000")
            self.engine = "sqlite"
            return

        if _is_postgres_url(self.database_url):
            if psycopg is None:
                raise RuntimeError("psycopg is required for PostgreSQL DATABASE_URL")
            kwargs = {}
            if dict_row is not None:
                kwargs["row_factory"] = dict_row
            self.conn = psycopg.connect(self.database_url, **kwargs)
            self.engine = "postgres"
            return

        raise RuntimeError("Unsupported DATABASE_URL scheme")

    @property
    def enabled(self) -> bool:
        return self.conn is not None

    def close(self):
        if self.conn is not None:
            self.conn.close()

    def _adapt_query(self, query: str) -> str:
        if self.engine == "postgres":
            return query.replace("?", "%s")
        return query

    def _rollback_safe(self):
        if not self.enabled:
            return
        with contextlib.suppress(Exception):
            self.conn.rollback()

    def _migration_files(self) -> list[Path]:
        migrations_dir = Path(__file__).resolve().parent / "migrations"
        return sorted(migrations_dir.glob("*.sql"), key=lambda p: p.name)

    def _should_run_migration(self, migration_path: Path) -> bool:
        name = migration_path.name.lower()
        if self.engine == "sqlite" and "postgres" in name:
            return False
        return True

    def execute_script(self, sql_text: str):
        if not self.enabled:
            return
        if self.engine == "sqlite":
            try:
                self.conn.executescript(sql_text)
                self.conn.commit()
            except Exception:
                self._rollback_safe()
                raise
            return

        cur = self.conn.cursor()
        try:
            cur.execute(sql_text)
            self.conn.commit()
        except Exception:
            self._rollback_safe()
            raise
        finally:
            with contextlib.suppress(Exception):
                cur.close()

    def execute(self, query: str, params: tuple[Any, ...] | None = None):
        if not self.enabled:
            return
        params = params or ()
        cur = self.conn.cursor()
        try:
            cur.execute(self._adapt_query(query), params)
            self.conn.commit()
        except Exception:
            self._rollback_safe()
            raise
        finally:
            with contextlib.suppress(Exception):
                cur.close()

    def query_all(self, query: str, params: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        params = params or ()
        cur = self.conn.cursor()
        description = None
        try:
            cur.execute(self._adapt_query(query), params)
            description = cur.description
            rows = cur.fetchall()
        except Exception:
            self._rollback_safe()
            raise
        finally:
            with contextlib.suppress(Exception):
                cur.close()
        return self._rows_to_dicts(rows, description)

    def _rows_to_dicts(self, rows: list[Any], description: Any) -> list[dict[str, Any]]:
        if not rows:
            return []

        column_names = []
        if description:
            for col in description:
                if hasattr(col, "name"):
                    column_names.append(col.name)
                elif isinstance(col, (tuple, list)) and col:
                    column_names.append(col[0])
                else:
                    column_names.append(str(col))

        result = []
        for row in rows:
            if isinstance(row, sqlite3.Row):
                result.append(dict(row))
                continue
            if isinstance(row, dict):
                result.append(dict(row))
                continue
            if hasattr(row, "_mapping"):
                result.append(dict(row._mapping))
                continue
            if isinstance(row, (tuple, list)):
                if column_names:
                    result.append({name: value for name, value in zip(column_names, row)})
                else:
                    result.append({str(i): value for i, value in enumerate(row)})
                continue

            # Fallback for uncommon row objects.
            with contextlib.suppress(Exception):
                result.append(dict(row))
                continue
            result.append({"value": row})
        return result

    def query_one(self, query: str, params: tuple[Any, ...] | None = None) -> dict[str, Any] | None:
        rows = self.query_all(query, params)
        return rows[0] if rows else None

    def ensure_schema(self):
        if not self.enabled:
            return
        for migration_path in self._migration_files():
            if not self._should_run_migration(migration_path):
                continue
            sql_text = migration_path.read_text(encoding="utf-8")
            self.execute_script(sql_text)

    def insert_bot_run(self, payload: dict[str, Any]):
        self.execute(
            """
            INSERT INTO bot_runs (
                run_id, run_timestamp, market_date, trading_mode, market_status, bot_status,
                review_required, stop_reason, safe_error_type, safe_error_message,
                submitted, symbol, notional, safe_order_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO NOTHING
            """,
            (
                payload.get("run_id"),
                payload.get("run_timestamp"),
                payload.get("market_date"),
                payload.get("trading_mode"),
                payload.get("market_status"),
                payload.get("bot_status"),
                1 if payload.get("review_required") else 0,
                payload.get("stop_reason"),
                payload.get("safe_error_type"),
                payload.get("safe_error_message"),
                None if payload.get("submitted") is None else (1 if payload.get("submitted") else 0),
                payload.get("symbol"),
                payload.get("notional"),
                payload.get("safe_order_status"),
            ),
        )

    def insert_signal_snapshot(self, payload: dict[str, Any]):
        self.execute(
            """
            INSERT INTO signal_snapshots (
                run_id, snapshot_timestamp, market_date, market_open, latest_market_data_timestamp,
                symbol, latest_price, short_moving_average, long_moving_average, generated_signal,
                trade_or_skip_reason, daily_submitted_order_count, max_daily_orders,
                daily_submitted_notional, max_daily_submitted_notional, cooldown_status,
                duplicate_signal_status, pending_order_status, daily_loss_stop_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.get("run_id"),
                payload.get("snapshot_timestamp"),
                payload.get("market_date"),
                None if payload.get("market_open") is None else (1 if payload.get("market_open") else 0),
                payload.get("latest_market_data_timestamp"),
                payload.get("symbol"),
                payload.get("latest_price"),
                payload.get("short_moving_average"),
                payload.get("long_moving_average"),
                payload.get("generated_signal"),
                payload.get("trade_or_skip_reason"),
                payload.get("daily_submitted_order_count"),
                payload.get("max_daily_orders"),
                payload.get("daily_submitted_notional"),
                payload.get("max_daily_submitted_notional"),
                payload.get("cooldown_status"),
                payload.get("duplicate_signal_status"),
                payload.get("pending_order_status"),
                payload.get("daily_loss_stop_status"),
            ),
        )

    def insert_account_snapshot(self, payload: dict[str, Any]):
        self.execute(
            """
            INSERT INTO paper_account_snapshots (
                run_id, snapshot_timestamp, account_status, portfolio_value,
                cash, buying_power, open_positions, unrealized_paper_pl, pending_orders
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.get("run_id"),
                payload.get("snapshot_timestamp"),
                payload.get("account_status"),
                payload.get("portfolio_value"),
                payload.get("cash"),
                payload.get("buying_power"),
                payload.get("open_positions"),
                payload.get("unrealized_paper_pl"),
                payload.get("pending_orders"),
            ),
        )

    def insert_order_event(self, payload: dict[str, Any]):
        self.execute(
            """
            INSERT INTO sanitized_order_events (
                run_id, event_timestamp, market_date, signal, submitted, symbol, notional,
                safe_order_status, stop_reason, review_required, safe_error_type,
                safe_error_message, order_id_masked
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.get("run_id"),
                payload.get("event_timestamp"),
                payload.get("market_date"),
                payload.get("signal"),
                None if payload.get("submitted") is None else (1 if payload.get("submitted") else 0),
                payload.get("symbol"),
                payload.get("notional"),
                payload.get("safe_order_status"),
                payload.get("stop_reason"),
                None if payload.get("review_required") is None else (1 if payload.get("review_required") else 0),
                payload.get("safe_error_type"),
                payload.get("safe_error_message"),
                payload.get("order_id_masked"),
            ),
        )

    def retention_sql(self) -> dict[str, str]:
        return {
            "bot_runs": "DELETE FROM bot_runs WHERE run_timestamp < ?",
            "signal_snapshots": "DELETE FROM signal_snapshots WHERE snapshot_timestamp < ?",
            "paper_account_snapshots": "DELETE FROM paper_account_snapshots WHERE snapshot_timestamp < ?",
            "sanitized_order_events": "DELETE FROM sanitized_order_events WHERE event_timestamp < ?",
        }

    def fetch_latest_bot_run(self) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM bot_runs ORDER BY run_timestamp DESC LIMIT 1")

    def fetch_latest_successful_run(self) -> dict[str, Any] | None:
        return self.query_one(
            "SELECT * FROM bot_runs WHERE bot_status != ? ORDER BY run_timestamp DESC LIMIT 1",
            ("error",),
        )

    def fetch_latest_signal_snapshot(self) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM signal_snapshots ORDER BY snapshot_timestamp DESC LIMIT 1")

    def fetch_latest_account_snapshot(self) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM paper_account_snapshots ORDER BY snapshot_timestamp DESC LIMIT 1")

    def fetch_recent_order_events(self, limit: int = 25) -> list[dict[str, Any]]:
        return self.query_all(
            "SELECT * FROM sanitized_order_events ORDER BY event_timestamp DESC LIMIT ?",
            (int(limit),),
        )

    def fetch_recent_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.query_all(
            "SELECT * FROM bot_runs ORDER BY run_timestamp DESC LIMIT ?",
            (int(limit),),
        )

    def fetch_portfolio_history(self, limit: int = 500) -> list[dict[str, Any]]:
        return self.query_all(
            "SELECT snapshot_timestamp, portfolio_value, unrealized_paper_pl FROM paper_account_snapshots ORDER BY snapshot_timestamp DESC LIMIT ?",
            (int(limit),),
        )

    def fetch_signal_history(self, limit: int = 500) -> list[dict[str, Any]]:
        return self.query_all(
            """
            SELECT
                snapshot_timestamp,
                generated_signal,
                latest_price,
                short_moving_average,
                long_moving_average,
                trade_or_skip_reason,
                market_open
            FROM signal_snapshots
            ORDER BY snapshot_timestamp DESC
            LIMIT ?
            """,
            (int(limit),),
        )

    def fetch_order_count_by_day(self, limit: int = 90) -> list[dict[str, Any]]:
        return self.query_all(
            """
            SELECT market_date, SUM(CASE WHEN submitted = 1 THEN 1 ELSE 0 END) AS submitted_count
            FROM sanitized_order_events
            GROUP BY market_date
            ORDER BY market_date DESC
            LIMIT ?
            """,
            (int(limit),),
        )

    def _stable_json(self, payload: Any) -> str:
        return json.dumps(payload if payload is not None else {}, sort_keys=True, separators=(",", ":"))

    def insert_scanner_run(self, payload: dict[str, Any]) -> int:
        self.execute(
            """
            INSERT INTO scanner_runs (
                started_at, completed_at, universe_name, symbol_count,
                success_count, rejection_count, error_count, eligible_count,
                status, duration_seconds
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.get("started_at"),
                payload.get("completed_at"),
                payload.get("universe_name"),
                payload.get("symbol_count"),
                payload.get("success_count"),
                payload.get("rejection_count"),
                payload.get("error_count"),
                payload.get("eligible_count"),
                payload.get("status"),
                payload.get("duration_seconds"),
            ),
        )
        row = self.query_one("SELECT id FROM scanner_runs ORDER BY id DESC LIMIT 1")
        return int((row or {}).get("id") or 0)

    def insert_scanner_result(self, run_id: int, payload: dict[str, Any]):
        self.execute(
            """
            INSERT INTO scanner_results (
                run_id, symbol, company_name, sector, latest_price,
                average_dollar_volume, overall_score, confidence, signal,
                regime, ranking_score, rank, eligible, rejection_reasons_json,
                component_scores_json, reasons_json, warnings_json, data_quality_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                payload.get("symbol"),
                payload.get("company_name"),
                payload.get("sector"),
                payload.get("latest_price"),
                payload.get("average_dollar_volume"),
                payload.get("overall_score"),
                payload.get("confidence"),
                payload.get("signal"),
                payload.get("regime"),
                payload.get("ranking_score"),
                payload.get("rank"),
                1 if payload.get("eligible") else 0,
                self._stable_json(payload.get("rejection_reasons") or []),
                self._stable_json(payload.get("component_scores") or {}),
                self._stable_json(payload.get("reasons") or []),
                self._stable_json(payload.get("warnings") or []),
                self._stable_json(payload.get("data_quality") or {}),
            ),
        )

    def insert_portfolio_candidate(self, run_id: int, payload: dict[str, Any]):
        self.execute(
            """
            INSERT INTO portfolio_candidates (
                run_id, symbol, rank, sector, score, confidence,
                suggested_allocation_percent, suggested_paper_notional,
                selection_reasons_json, warnings_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                payload.get("symbol"),
                payload.get("rank"),
                payload.get("sector"),
                payload.get("score"),
                payload.get("confidence"),
                payload.get("suggested_max_allocation_percent"),
                payload.get("suggested_paper_notional"),
                self._stable_json(payload.get("reasons_selected") or []),
                self._stable_json(payload.get("warnings") or []),
            ),
        )

    def insert_position_review(self, run_id: int, payload: dict[str, Any]):
        self.execute(
            """
            INSERT INTO position_reviews (
                run_id, symbol, current_quantity, current_entry_price,
                current_market_price, score, confidence, recommendation,
                reasons_json, warnings_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                payload.get("symbol"),
                payload.get("current_quantity"),
                payload.get("current_entry_price"),
                payload.get("current_market_price"),
                payload.get("score"),
                payload.get("confidence"),
                payload.get("recommendation"),
                self._stable_json(payload.get("reasons") or []),
                self._stable_json(payload.get("warnings") or []),
            ),
        )

    def fetch_latest_scanner_run(self) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM scanner_runs ORDER BY started_at DESC LIMIT 1")

    def fetch_scanner_runs(self, limit: int = 25) -> list[dict[str, Any]]:
        return self.query_all(
            "SELECT * FROM scanner_runs ORDER BY started_at DESC LIMIT ?",
            (int(limit),),
        )

    def fetch_top_scanner_results(self, run_id: int | None = None, limit: int = 20) -> list[dict[str, Any]]:
        if run_id is None:
            latest = self.fetch_latest_scanner_run()
            run_id = int((latest or {}).get("id") or 0)
        if not run_id:
            return []
        return self.query_all(
            """
            SELECT * FROM scanner_results
            WHERE run_id = ? AND eligible = 1
            ORDER BY rank ASC, ranking_score DESC
            LIMIT ?
            """,
            (int(run_id), int(limit)),
        )

    def fetch_scanner_rejections(self, run_id: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if run_id is None:
            latest = self.fetch_latest_scanner_run()
            run_id = int((latest or {}).get("id") or 0)
        if not run_id:
            return []
        return self.query_all(
            """
            SELECT symbol, rejection_reasons_json, warnings_json
            FROM scanner_results
            WHERE run_id = ? AND eligible = 0
            ORDER BY symbol ASC
            LIMIT ?
            """,
            (int(run_id), int(limit)),
        )

    def fetch_scanner_sector_distribution(self, run_id: int | None = None) -> list[dict[str, Any]]:
        if run_id is None:
            latest = self.fetch_latest_scanner_run()
            run_id = int((latest or {}).get("id") or 0)
        if not run_id:
            return []
        return self.query_all(
            """
            SELECT sector, COUNT(*) AS candidate_count
            FROM scanner_results
            WHERE run_id = ? AND eligible = 1
            GROUP BY sector
            ORDER BY candidate_count DESC, sector ASC
            """,
            (int(run_id),),
        )

    def fetch_latest_position_reviews(self, run_id: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if run_id is None:
            latest = self.fetch_latest_scanner_run()
            run_id = int((latest or {}).get("id") or 0)
        if not run_id:
            return []
        return self.query_all(
            """
            SELECT * FROM position_reviews
            WHERE run_id = ?
            ORDER BY symbol ASC
            LIMIT ?
            """,
            (int(run_id), int(limit)),
        )
