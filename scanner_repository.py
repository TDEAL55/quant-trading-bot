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
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


@dataclass
class ScannerPersistencePayload:
    run: dict[str, Any]
    results: list[dict[str, Any]]
    candidates: list[dict[str, Any]]
    position_reviews: list[dict[str, Any]]


class ScannerRepository:
    def save_scan(self, payload: ScannerPersistencePayload) -> dict[str, Any]:
        raise NotImplementedError


class MonitoringScannerRepository(ScannerRepository):
    def __init__(self, database_url: str | None = None):
        self.db = MonitoringDatabase(database_url=database_url)

    def save_scan(self, payload: ScannerPersistencePayload) -> dict[str, Any]:
        if not self.db.enabled:
            raise RuntimeError("Database is not enabled for scanner persistence")
        self.db.ensure_schema()
        run = dict(payload.run)
        run.setdefault("started_at", _utc_iso())
        run.setdefault("completed_at", _utc_iso())
        run.setdefault("status", "completed")
        run_id = self.db.insert_scanner_run(run)
        for result in payload.results:
            self.db.insert_scanner_result(run_id, result)
        for candidate in payload.candidates:
            self.db.insert_portfolio_candidate(run_id, candidate)
        for review in payload.position_reviews:
            self.db.insert_position_review(run_id, review)
        return {"storage": "database", "run_id": run_id, "saved_at": _utc_iso()}


class JsonScannerRepository(ScannerRepository):
    def __init__(self, root_path: str | Path = "scanner_state"):
        self.root = Path(root_path)
        self.root.mkdir(parents=True, exist_ok=True)

    def save_scan(self, payload: ScannerPersistencePayload) -> dict[str, Any]:
        run = dict(payload.run)
        run_id = str(run.get("run_id") or f"scan-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}")
        run["run_id"] = run_id

        target = self.root / f"{run_id}.json"
        serialized = {
            "run": run,
            "results": payload.results,
            "candidates": payload.candidates,
            "position_reviews": payload.position_reviews,
        }
        target.write_text(_stable_json(serialized) + "\n", encoding="utf-8")
        return {"storage": "json", "run_id": run_id, "path": str(target), "saved_at": _utc_iso()}


def save_scan_results(
    run_payload: dict[str, Any],
    scan_results: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    position_reviews: list[dict[str, Any]],
    database_url: str | None = None,
    json_fallback_dir: str | Path = "scanner_state",
) -> dict[str, Any]:
    payload = ScannerPersistencePayload(
        run=run_payload,
        results=scan_results,
        candidates=candidates,
        position_reviews=position_reviews,
    )
    try:
        db_repo = MonitoringScannerRepository(database_url=database_url)
        if db_repo.db.enabled:
            return db_repo.save_scan(payload)
    except Exception:
        pass

    fallback_repo = JsonScannerRepository(root_path=json_fallback_dir)
    return fallback_repo.save_scan(payload)
