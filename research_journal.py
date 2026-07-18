from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from config import RESEARCH_JOURNAL_VERSION, SCANNER_VERSION, STRATEGY_VERSION
from logger_setup import logger
from research_data import build_research_candidate_records, build_research_config_snapshot, build_research_run_record
from research_repository import JsonResearchRepository, MonitoringResearchRepository, ResearchPersistencePayload, save_research_results


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(event: str, **fields: Any) -> None:
    parts = [event]
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{key}={value}")
    logger.info(" ".join(parts))


def _normalize_run_id(research_run_id: str | None) -> str:
    if research_run_id:
        return str(research_run_id).strip()
    return f"research-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"


def _validate_scanner_payload(scanner_payload: dict[str, Any]) -> None:
    if not isinstance(scanner_payload, dict):
        raise ValueError("scanner payload must be a dictionary")
    if not scanner_payload.get("summary"):
        raise ValueError("scanner payload summary is required")
    if scanner_payload.get("ranked_candidates") is None and scanner_payload.get("scan_results") is None:
        raise ValueError("scanner payload requires ranked candidates or scan results")


class ResearchJournal:
    def __init__(self, database_url: str | None = None, json_fallback_dir: str = "research_state"):
        self.database_url = database_url
        self.json_fallback_dir = json_fallback_dir

    def _repository(self):
        repository = MonitoringResearchRepository(database_url=self.database_url)
        if repository.db.enabled:
            return repository
        return JsonResearchRepository(root_path=self.json_fallback_dir)

    def record_scanner_run(
        self,
        scanner_payload: dict[str, Any],
        research_run_id: str | None = None,
        data_source: str = "cached",
        data_mode: str = "research",
        scanner_version: str | None = None,
        strategy_version: str | None = None,
        scanner_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _validate_scanner_payload(scanner_payload)
        payload_copy = deepcopy(scanner_payload)
        run_id = _normalize_run_id(research_run_id)
        scanner_config_snapshot = scanner_config or build_research_config_snapshot()
        run_record = build_research_run_record(
            payload_copy,
            research_run_id=run_id,
            scanner_version=scanner_version or SCANNER_VERSION,
            strategy_version=strategy_version or STRATEGY_VERSION,
            data_source=data_source,
            data_mode=data_mode,
            completed_at=_utc_iso(),
            scanner_config=scanner_config_snapshot,
        )
        candidate_records = build_research_candidate_records(payload_copy, run_id)
        _log(
            "RESEARCH_JOURNAL_STARTED",
            research_run_id=run_id,
            scanner_version=run_record.get("scanner_version"),
            strategy_version=run_record.get("strategy_version"),
            candidate_count=len(candidate_records),
            data_source=data_source,
        )
        repository = self._repository()
        try:
            saved = save_research_results(
                run_payload=run_record,
                candidate_payloads=candidate_records,
                database_url=self.database_url,
                json_fallback_dir=self.json_fallback_dir,
            )
            duplicate_run = bool(saved.get("duplicate_run"))
            if duplicate_run:
                _log("RESEARCH_JOURNAL_DUPLICATE_RUN_SKIPPED", research_run_id=run_id)
            _log(
                "RESEARCH_RUN_STORED",
                research_run_id=run_id,
                storage=saved.get("storage"),
                stored_candidate_count=saved.get("stored_candidate_count", len(candidate_records)),
            )
            return {
                "status": "stored",
                "research_run_id": run_id,
                "duplicate_run": duplicate_run,
                "stored_candidate_count": len(candidate_records),
                "storage": saved.get("storage"),
                "saved_at": saved.get("saved_at"),
                "run_record": run_record,
                "candidate_records": candidate_records,
                "research_journal_version": RESEARCH_JOURNAL_VERSION,
            }
        except Exception as exc:
            _log("RESEARCH_PERSISTENCE_FAILURE", research_run_id=run_id, type=type(exc).__name__)
            raise
        finally:
            if getattr(repository, "db", None) is not None:
                repository.db.close()


def journal_scanner_run(
    scanner_payload: dict[str, Any],
    research_run_id: str | None = None,
    database_url: str | None = None,
    data_source: str = "cached",
    data_mode: str = "research",
    scanner_version: str | None = None,
    strategy_version: str | None = None,
    scanner_config: dict[str, Any] | None = None,
    json_fallback_dir: str = "research_state",
) -> dict[str, Any]:
    journal = ResearchJournal(database_url=database_url, json_fallback_dir=json_fallback_dir)
    return journal.record_scanner_run(
        scanner_payload=scanner_payload,
        research_run_id=research_run_id,
        data_source=data_source,
        data_mode=data_mode,
        scanner_version=scanner_version,
        strategy_version=strategy_version,
        scanner_config=scanner_config,
    )
