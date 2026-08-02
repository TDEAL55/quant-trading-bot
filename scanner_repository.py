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

        score_versions = [
            str((item.get("quantum_score") or {}).get("score_version") or "")
            for item in payload.results
            if isinstance(item, dict)
        ]
        score_version = next((value for value in score_versions if value), "quantum_v1")
        selected = list(payload.candidates or [])
        selected_primary = dict(selected[0]) if selected else {}
        selected_quantum = dict(selected_primary.get("quantum_score") or {})
        selected_strategies = list(selected_primary.get("eligible_strategy_ids") or [])

        quantum_run_id = self.db.insert_quantum_score_run(
            {
                "scanner_run_id": run_id,
                "score_version": score_version,
                "started_at": run.get("started_at") or _utc_iso(),
                "completed_at": run.get("completed_at") or _utc_iso(),
                "symbol_count": len(payload.results),
                "eligible_count": len([item for item in payload.results if bool(item.get("eligible"))]),
                "selected_symbol": selected_primary.get("symbol"),
                "selected_strategy_id": selected_strategies[0] if selected_strategies else None,
                "selected_final_score": selected_quantum.get("final_score"),
                "configuration": {
                    "component_weights": selected_quantum.get("component_weights") or {},
                    "score_version": score_version,
                },
                "created_at": _utc_iso(),
            }
        )

        selected_symbols = {str(item.get("symbol") or "").upper() for item in selected}
        for result in payload.results:
            quantum = dict(result.get("quantum_score") or {})
            if not quantum:
                continue
            strategy_scores = dict(result.get("strategy_specific_scores") or {})
            security_id = self.db.insert_quantum_security_score(
                quantum_run_id,
                {
                    "symbol": str(result.get("symbol") or "").upper(),
                    "rank": result.get("rank"),
                    "is_selected": str(result.get("symbol") or "").upper() in selected_symbols,
                    "strategy_eligibility": any(bool((row or {}).get("eligible")) for row in strategy_scores.values()),
                    "final_score": quantum.get("final_score"),
                    "data_quality_status": quantum.get("data_quality_status"),
                    "score_timestamp": quantum.get("calculation_timestamp"),
                    "score_version": quantum.get("score_version"),
                    "market_regime": quantum.get("market_regime"),
                    "risk_reward_ratio": (quantum.get("risk_reward") or {}).get("reward_risk_ratio"),
                    "warnings": list(quantum.get("warnings") or []),
                    "rejection_reasons": list(quantum.get("rejection_reasons") or []),
                    "factor_values": dict(quantum.get("factor_values") or {}),
                    "weights": dict(quantum.get("component_weights") or {}),
                    "created_at": _utc_iso(),
                },
            )

            normalized = dict(quantum.get("normalized_component_scores") or {})
            weights = dict(quantum.get("component_weights") or {})
            contributions = dict(quantum.get("weighted_contributions") or {})
            penalties_by_component = {
                str(item.get("component") or ""): float(item.get("penalty_points") or 0.0)
                for item in list(quantum.get("missing_data_penalties") or [])
            }
            for component, normalized_score in normalized.items():
                self.db.insert_quantum_component_contribution(
                    security_id,
                    {
                        "component_name": component,
                        "normalized_score": normalized_score,
                        "weight": weights.get(component),
                        "weighted_contribution": contributions.get(component),
                        "penalty_points": penalties_by_component.get(component, 0.0),
                        "warning": None,
                    },
                )

            for strategy_id, strategy_payload in strategy_scores.items():
                row = dict(strategy_payload or {})
                row.setdefault("strategy_id", strategy_id)
                self.db.insert_quantum_strategy_score(security_id, row)

            for reason in list(quantum.get("rejection_reasons") or []):
                self.db.insert_quantum_rejection(security_id, str(reason), source="quantum")

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
