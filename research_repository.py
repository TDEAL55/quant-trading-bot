from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from monitoring_db import MonitoringDatabase


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class ResearchPersistencePayload:
    run: dict[str, Any]
    candidates: list[dict[str, Any]]


class ResearchRepository:
    def save_research(self, payload: ResearchPersistencePayload) -> dict[str, Any]:
        raise NotImplementedError


class MonitoringResearchRepository(ResearchRepository):
    def __init__(self, database_url: str | None = None):
        self.db = MonitoringDatabase(database_url=database_url)

    def _adapt_query(self, query: str) -> str:
        return self.db._adapt_query(query)

    def _stable_json(self, value: Any) -> str:
        return _stable_json(value)

    def _candidate_rows(self, run_id: str, candidates: list[dict[str, Any]], cursor) -> None:
        cursor.execute(self._adapt_query("DELETE FROM research_candidates WHERE research_run_id = ?"), (run_id,))
        for candidate in candidates:
            cursor.execute(
                self._adapt_query(
                    """
                    INSERT INTO research_candidates (
                        research_run_id, symbol, company_name, rank, overall_score, confidence,
                        signal, market_regime, sector, industry, latest_price, average_dollar_volume,
                        liquidity_score, trend_score, momentum_score, volume_score, volatility_score,
                        market_regime_score, risk_quality_score, rejection_status,
                        rejection_reasons_json, strategy_reasons_json, factor_breakdown_json,
                        ranking_score, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(research_run_id, symbol) DO UPDATE SET
                        company_name = excluded.company_name,
                        rank = excluded.rank,
                        overall_score = excluded.overall_score,
                        confidence = excluded.confidence,
                        signal = excluded.signal,
                        market_regime = excluded.market_regime,
                        sector = excluded.sector,
                        industry = excluded.industry,
                        latest_price = excluded.latest_price,
                        average_dollar_volume = excluded.average_dollar_volume,
                        liquidity_score = excluded.liquidity_score,
                        trend_score = excluded.trend_score,
                        momentum_score = excluded.momentum_score,
                        volume_score = excluded.volume_score,
                        volatility_score = excluded.volatility_score,
                        market_regime_score = excluded.market_regime_score,
                        risk_quality_score = excluded.risk_quality_score,
                        rejection_status = excluded.rejection_status,
                        rejection_reasons_json = excluded.rejection_reasons_json,
                        strategy_reasons_json = excluded.strategy_reasons_json,
                        factor_breakdown_json = excluded.factor_breakdown_json,
                        ranking_score = excluded.ranking_score,
                        created_at = excluded.created_at
                    """
                ),
                (
                    run_id,
                    candidate.get("symbol"),
                    candidate.get("company_name"),
                    candidate.get("rank"),
                    candidate.get("overall_score"),
                    candidate.get("confidence"),
                    candidate.get("signal"),
                    candidate.get("market_regime"),
                    candidate.get("sector"),
                    candidate.get("industry"),
                    candidate.get("latest_price"),
                    candidate.get("average_dollar_volume"),
                    candidate.get("liquidity_score"),
                    candidate.get("trend_score"),
                    candidate.get("momentum_score"),
                    candidate.get("volume_score"),
                    candidate.get("volatility_score"),
                    candidate.get("market_regime_score"),
                    candidate.get("risk_quality_score"),
                    candidate.get("rejection_status"),
                    self._stable_json(candidate.get("rejection_reasons") or []),
                    self._stable_json(candidate.get("strategy_reasons") or []),
                    self._stable_json(candidate.get("factor_breakdown") or {}),
                    candidate.get("ranking_score"),
                    candidate.get("created_at") or _utc_iso(),
                ),
            )

    def save_research(self, payload: ResearchPersistencePayload) -> dict[str, Any]:
        if not self.db.enabled:
            raise RuntimeError("Database is not enabled for research persistence")
        self.db.ensure_schema()
        run = dict(payload.run)
        run_id = str(run.get("research_run_id") or "")
        if not run_id:
            raise ValueError("research_run_id is required")

        conn = self.db.conn
        existing_run = self.fetch_research_run_by_id(run_id)
        with conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    self._adapt_query(
                        """
                        INSERT INTO research_runs (
                            research_run_id, started_at, completed_at, scanner_version, strategy_version,
                            benchmark_symbol, market_regime, universe_size, scanned_count, eligible_count,
                            rejected_count, error_count, average_overall_score, average_confidence,
                            scanner_duration_seconds, data_source, data_mode, scanner_config_json,
                            factor_weights_json, scanner_summary_json, status
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(research_run_id) DO UPDATE SET
                            started_at = excluded.started_at,
                            completed_at = excluded.completed_at,
                            scanner_version = excluded.scanner_version,
                            strategy_version = excluded.strategy_version,
                            benchmark_symbol = excluded.benchmark_symbol,
                            market_regime = excluded.market_regime,
                            universe_size = excluded.universe_size,
                            scanned_count = excluded.scanned_count,
                            eligible_count = excluded.eligible_count,
                            rejected_count = excluded.rejected_count,
                            error_count = excluded.error_count,
                            average_overall_score = excluded.average_overall_score,
                            average_confidence = excluded.average_confidence,
                            scanner_duration_seconds = excluded.scanner_duration_seconds,
                            data_source = excluded.data_source,
                            data_mode = excluded.data_mode,
                            scanner_config_json = excluded.scanner_config_json,
                            factor_weights_json = excluded.factor_weights_json,
                            scanner_summary_json = excluded.scanner_summary_json,
                            status = excluded.status
                        """
                    ),
                    (
                        run_id,
                        run.get("started_at"),
                        run.get("completed_at"),
                        run.get("scanner_version"),
                        run.get("strategy_version"),
                        run.get("benchmark_symbol"),
                        run.get("market_regime"),
                        run.get("universe_size"),
                        run.get("scanned_count"),
                        run.get("eligible_count"),
                        run.get("rejected_count"),
                        run.get("error_count"),
                        run.get("average_overall_score"),
                        run.get("average_confidence"),
                        run.get("scanner_duration_seconds"),
                        run.get("data_source"),
                        run.get("data_mode"),
                        self._stable_json(run.get("scanner_config") or {}),
                        self._stable_json(run.get("factor_weights") or {}),
                        self._stable_json(run.get("scanner_summary") or {}),
                        run.get("status") or "completed",
                    ),
                )
                self._candidate_rows(run_id, list(payload.candidates), cursor)
            finally:
                cursor.close()

        return {
            "storage": "database",
            "research_run_id": run_id,
            "duplicate_run": bool(existing_run),
            "stored_candidate_count": len(payload.candidates),
            "saved_at": _utc_iso(),
        }

    def insert_research_run(self, payload: dict[str, Any]) -> str:
        return self.save_research(ResearchPersistencePayload(run=payload, candidates=[]))["research_run_id"]

    def insert_research_candidates(self, research_run_id: str, candidates: list[dict[str, Any]]):
        if not self.db.enabled:
            raise RuntimeError("Database is not enabled for research persistence")
        self.db.ensure_schema()
        conn = self.db.conn
        with conn:
            cursor = conn.cursor()
            try:
                self._candidate_rows(str(research_run_id), candidates, cursor)
            finally:
                cursor.close()

    def fetch_latest_research_run(self) -> dict[str, Any] | None:
        return self.db.query_one("SELECT * FROM research_runs ORDER BY started_at DESC LIMIT 1")

    def fetch_recent_research_runs(self, limit: int = 25) -> list[dict[str, Any]]:
        return self.db.query_all("SELECT * FROM research_runs ORDER BY started_at DESC LIMIT ?", (int(limit),))

    def fetch_research_run_by_id(self, research_run_id: str) -> dict[str, Any] | None:
        return self.db.query_one("SELECT * FROM research_runs WHERE research_run_id = ?", (str(research_run_id),))

    def fetch_research_candidates_for_run(self, research_run_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM research_candidates WHERE research_run_id = ? ORDER BY COALESCE(rank, 2147483647), symbol ASC"
        params: tuple[Any, ...] = (str(research_run_id),)
        if limit is not None:
            query += " LIMIT ?"
            params = (str(research_run_id), int(limit))
        return self.db.query_all(query, params)

    def fetch_highest_ranked_candidates_across_stored_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.db.query_all(
            """
            SELECT c.*, r.started_at AS research_started_at
            FROM research_candidates c
            JOIN research_runs r ON r.research_run_id = c.research_run_id
            ORDER BY COALESCE(c.rank, 2147483647), c.overall_score DESC, c.confidence DESC, c.symbol ASC
            LIMIT ?
            """,
            (int(limit),),
        )

    def fetch_candidates_by_symbol(self, symbol: str, limit: int = 100) -> list[dict[str, Any]]:
        return self.db.query_all(
            "SELECT * FROM research_candidates WHERE symbol = ? ORDER BY created_at DESC LIMIT ?",
            (str(symbol).upper(), int(limit)),
        )

    def fetch_candidates_by_sector(self, sector: str, limit: int = 100) -> list[dict[str, Any]]:
        return self.db.query_all(
            "SELECT * FROM research_candidates WHERE sector = ? ORDER BY overall_score DESC, confidence DESC LIMIT ?",
            (str(sector), int(limit)),
        )

    def fetch_candidates_by_regime(self, regime: str, limit: int = 100) -> list[dict[str, Any]]:
        return self.db.query_all(
            "SELECT * FROM research_candidates WHERE market_regime = ? ORDER BY overall_score DESC, confidence DESC LIMIT ?",
            (str(regime), int(limit)),
        )

    def fetch_candidates_by_score_range(self, minimum: float, maximum: float, limit: int = 200) -> list[dict[str, Any]]:
        return self.db.query_all(
            "SELECT * FROM research_candidates WHERE overall_score BETWEEN ? AND ? ORDER BY overall_score DESC, confidence DESC LIMIT ?",
            (float(minimum), float(maximum), int(limit)),
        )

    def fetch_candidates_by_confidence_range(self, minimum: float, maximum: float, limit: int = 200) -> list[dict[str, Any]]:
        return self.db.query_all(
            "SELECT * FROM research_candidates WHERE confidence BETWEEN ? AND ? ORDER BY confidence DESC, overall_score DESC LIMIT ?",
            (float(minimum), float(maximum), int(limit)),
        )

    def count_total_research_runs(self) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS n FROM research_runs") or {"n": 0}
        return int(row.get("n") or 0)

    def count_total_candidate_observations(self) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS n FROM research_candidates") or {"n": 0}
        return int(row.get("n") or 0)


class JsonResearchRepository(ResearchRepository):
    def __init__(self, root_path: str | Path = "research_state"):
        self.root = Path(root_path)
        self.root.mkdir(parents=True, exist_ok=True)

    def save_research(self, payload: ResearchPersistencePayload) -> dict[str, Any]:
        run = dict(payload.run)
        run_id = str(run.get("research_run_id") or f"research-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}")
        run["research_run_id"] = run_id
        target = self.root / f"{run_id}.json"
        duplicate_run = target.exists()
        target.write_text(
            json.dumps({"run": run, "candidates": payload.candidates}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return {
            "storage": "json",
            "research_run_id": run_id,
            "duplicate_run": duplicate_run,
            "stored_candidate_count": len(payload.candidates),
            "saved_at": _utc_iso(),
        }


def save_research_results(
    run_payload: dict[str, Any],
    candidate_payloads: list[dict[str, Any]],
    database_url: str | None = None,
    json_fallback_dir: str | Path = "research_state",
) -> dict[str, Any]:
    payload = ResearchPersistencePayload(run=run_payload, candidates=candidate_payloads)
    repository = MonitoringResearchRepository(database_url=database_url)
    try:
        if repository.db.enabled:
            return repository.save_research(payload)
    except Exception:
        pass
    finally:
        repository.db.close()
    return JsonResearchRepository(root_path=json_fallback_dir).save_research(payload)
