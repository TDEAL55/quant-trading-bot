from __future__ import annotations

import json
from typing import Any

from monitoring_db import MonitoringDatabase


def _json_load(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return default


def fetch_quantum_score_dashboard_payload(database_url: str | None, database_factory=MonitoringDatabase) -> dict[str, Any]:
    db = database_factory(database_url=database_url)
    payload: dict[str, Any] = {
        "db_connected": db.enabled,
        "latest_run": {},
        "top_candidates": [],
        "selected_candidate": {},
        "candidate_details": {},
    }
    if not db.enabled:
        return payload

    try:
        db.ensure_schema()
        latest_run = db.fetch_latest_quantum_score_run() or {}
        payload["latest_run"] = latest_run
        run_id = int(latest_run.get("id") or 0)
        if run_id <= 0:
            return payload

        top_candidates = db.fetch_top_quantum_scores(run_id=run_id, limit=50)
        for row in top_candidates:
            row["warnings"] = _json_load(row.get("warnings_json"), [])
            row["rejection_reasons"] = _json_load(row.get("rejection_reasons_json"), [])
            row["factor_values"] = _json_load(row.get("factor_values_json"), {})
            row["weights"] = _json_load(row.get("weights_json"), {})
        payload["top_candidates"] = top_candidates

        selected_row = next((row for row in top_candidates if int(row.get("is_selected") or 0) == 1), top_candidates[0] if top_candidates else {})
        payload["selected_candidate"] = selected_row

        selected_score_id = int(selected_row.get("id") or 0)
        if selected_score_id > 0:
            details = db.fetch_quantum_score_details(selected_score_id)
            for strategy_row in details.get("strategy_scores") or []:
                strategy_row["required_factors"] = _json_load(strategy_row.get("required_factors_json"), [])
                strategy_row["rejection_reasons"] = _json_load(strategy_row.get("rejection_reasons_json"), [])
                strategy_row["warnings"] = _json_load(strategy_row.get("warnings_json"), [])
            payload["candidate_details"] = details
        return payload
    finally:
        db.close()
