from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from config import (
    BENCHMARK_SYMBOL,
    FACTOR_WEIGHTS,
    RESEARCH_JOURNAL_VERSION,
    SCANNER_BATCH_SIZE,
    SCANNER_BLOCKED_REGIMES,
    SCANNER_EXCLUDED_SYMBOLS,
    SCANNER_INCLUDE_ETFS,
    SCANNER_MAX_MISSING_PERCENT,
    SCANNER_MAX_RETRIES,
    SCANNER_MAX_STALE_BUSINESS_DAYS,
    SCANNER_MAX_UNIVERSE_SIZE,
    SCANNER_MAX_WORKERS,
    SCANNER_MIN_AVG_DOLLAR_VOLUME,
    SCANNER_MIN_CONFIDENCE,
    SCANNER_MIN_HISTORY_DAYS,
    SCANNER_MIN_PRICE,
    SCANNER_MIN_RISK_QUALITY,
    SCANNER_MIN_SCORE,
    SCANNER_MIN_VOLATILITY_SCORE,
    SCANNER_SYMBOL_TIMEOUT_SECONDS,
    SCANNER_UNIVERSES,
    SCANNER_VERSION,
    SIGNAL_THRESHOLDS,
    STRATEGY_VERSION,
)
from monitoring_db import MonitoringDatabase
from research_repository import MonitoringResearchRepository


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_json(value: Any) -> str:
    import json

    return json.dumps(value if value is not None else {}, sort_keys=True, separators=(",", ":"))


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def build_research_config_snapshot() -> dict[str, Any]:
    return {
        "scanner_version": SCANNER_VERSION,
        "strategy_version": STRATEGY_VERSION,
        "research_journal_version": RESEARCH_JOURNAL_VERSION,
        "benchmark_symbol": BENCHMARK_SYMBOL,
        "factor_weights": dict(FACTOR_WEIGHTS),
        "signal_thresholds": dict(SIGNAL_THRESHOLDS),
        "scanner_universes": list(SCANNER_UNIVERSES),
        "scanner_include_etfs": bool(SCANNER_INCLUDE_ETFS),
        "scanner_max_universe_size": int(SCANNER_MAX_UNIVERSE_SIZE),
        "scanner_excluded_symbols": list(SCANNER_EXCLUDED_SYMBOLS),
        "scanner_min_price": float(SCANNER_MIN_PRICE),
        "scanner_min_avg_dollar_volume": float(SCANNER_MIN_AVG_DOLLAR_VOLUME),
        "scanner_min_history_days": int(SCANNER_MIN_HISTORY_DAYS),
        "scanner_max_missing_percent": float(SCANNER_MAX_MISSING_PERCENT),
        "scanner_max_stale_business_days": int(SCANNER_MAX_STALE_BUSINESS_DAYS),
        "scanner_min_score": float(SCANNER_MIN_SCORE),
        "scanner_min_confidence": float(SCANNER_MIN_CONFIDENCE),
        "scanner_min_risk_quality": float(SCANNER_MIN_RISK_QUALITY),
        "scanner_min_volatility_score": float(SCANNER_MIN_VOLATILITY_SCORE),
        "scanner_allowed_signals": ["BUY", "STRONG_BUY"],
        "scanner_blocked_regimes": list(SCANNER_BLOCKED_REGIMES),
        "scanner_max_workers": int(SCANNER_MAX_WORKERS),
        "scanner_symbol_timeout_seconds": int(SCANNER_SYMBOL_TIMEOUT_SECONDS),
        "scanner_max_retries": int(SCANNER_MAX_RETRIES),
        "scanner_batch_size": int(SCANNER_BATCH_SIZE),
    }


def _candidate_source_rows(scanner_payload: dict[str, Any]) -> list[dict[str, Any]]:
    ranked = list(scanner_payload.get("ranked_candidates") or [])
    scan_results = list(scanner_payload.get("scan_results") or [])
    if ranked:
        ranked_symbols = {str(item.get("symbol", "")).upper() for item in ranked}
        extra_rows = [item for item in scan_results if str(item.get("symbol", "")).upper() not in ranked_symbols]
        return ranked + extra_rows
    return scan_results


def build_research_candidate_records(scanner_payload: dict[str, Any], research_run_id: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    ordered_rows = _candidate_source_rows(scanner_payload)
    for position, row in enumerate(ordered_rows, start=1):
        components = dict(row.get("component_scores") or {})
        factors = dict((row.get("data_quality") or {}).get("factor") or {})
        rejection_reasons = list(row.get("rejection_reasons") or [])
        status = "REJECTED" if rejection_reasons or not row.get("eligible") else "ELIGIBLE"
        records.append(
            {
                "research_run_id": research_run_id,
                "symbol": str(row.get("symbol") or "").upper(),
                "company_name": row.get("company_name") or str(row.get("symbol") or ""),
                "rank": row.get("rank") if row.get("rank") is not None else None,
                "overall_score": _as_float(row.get("overall_score")),
                "confidence": _as_float(row.get("confidence")),
                "signal": row.get("signal") or "HOLD",
                "market_regime": row.get("regime") or "unknown",
                "sector": row.get("sector") or "Unknown",
                "industry": row.get("industry") or "Unknown",
                "latest_price": _as_float(row.get("latest_price")),
                "average_dollar_volume": _as_float(row.get("average_dollar_volume")),
                "liquidity_score": _as_float(row.get("liquidity_score")),
                "trend_score": _as_float(components.get("trend")),
                "momentum_score": _as_float(components.get("momentum")),
                "volume_score": _as_float(components.get("volume")),
                "volatility_score": _as_float(components.get("volatility")),
                "market_regime_score": _as_float(components.get("market_regime")),
                "risk_quality_score": _as_float(components.get("risk_quality")),
                "rejection_status": status,
                "rejection_reasons": rejection_reasons,
                "strategy_reasons": list(row.get("reasons") or []),
                "factor_breakdown": {
                    "component_scores": components,
                    "factors": factors,
                    "warnings": list(row.get("warnings") or []),
                    "data_quality": dict(row.get("data_quality") or {}),
                },
                "ranking_score": _as_float(row.get("ranking_score")) if row.get("ranking_score") is not None else None,
                "created_at": row.get("scan_timestamp") or _utc_iso(),
                "observed_order": position,
            }
        )
    return records


def build_research_run_record(
    scanner_payload: dict[str, Any],
    research_run_id: str,
    scanner_version: str | None = None,
    strategy_version: str | None = None,
    data_source: str = "cached",
    data_mode: str = "research",
    completed_at: str | None = None,
    scanner_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = dict(scanner_payload.get("summary") or {})
    scans = list(scanner_payload.get("scan_results") or [])
    candidates = _candidate_source_rows(scanner_payload)
    scores = [_as_float(item.get("overall_score")) for item in scans if item.get("overall_score") is not None]
    confidences = [_as_float(item.get("confidence")) for item in scans if item.get("confidence") is not None]
    return {
        "research_run_id": research_run_id,
        "started_at": summary.get("started_at") or summary.get("scan_started_at") or _utc_iso(),
        "completed_at": completed_at or _utc_iso(),
        "scanner_version": scanner_version or summary.get("scanner_version") or SCANNER_VERSION,
        "strategy_version": strategy_version or summary.get("strategy_version") or STRATEGY_VERSION,
        "benchmark_symbol": summary.get("benchmark_symbol") or BENCHMARK_SYMBOL,
        "market_regime": summary.get("market_regime") or (candidates[0].get("regime") if candidates else "unknown"),
        "universe_size": _as_int(summary.get("symbol_count"), len(scanner_payload.get("scan_results") or [])),
        "scanned_count": _as_int(summary.get("symbol_count"), len(scans)),
        "eligible_count": _as_int(summary.get("eligible_count"), len(scanner_payload.get("ranked_candidates") or [])),
        "rejected_count": _as_int(summary.get("rejection_count"), len([item for item in scans if not item.get("eligible") or item.get("status") == "rejected"])),
        "error_count": _as_int(summary.get("error_count"), len([item for item in scans if item.get("status") == "error"])),
        "average_overall_score": round(statistics.mean(scores), 4) if scores else 0.0,
        "average_confidence": round(statistics.mean(confidences), 4) if confidences else 0.0,
        "scanner_duration_seconds": _as_float(summary.get("duration_seconds")),
        "data_source": data_source,
        "data_mode": data_mode,
        "scanner_config": scanner_config or build_research_config_snapshot(),
        "factor_weights": dict(FACTOR_WEIGHTS),
        "scanner_summary": summary,
        "status": str(summary.get("status") or "completed"),
    }


def _empty_distribution() -> list[dict[str, Any]]:
    return []


def _bucket_counts(values: list[float], buckets: list[tuple[float, float, str]]) -> list[dict[str, Any]]:
    counts = []
    for minimum, maximum, label in buckets:
        count = len([value for value in values if minimum <= value < maximum])
        counts.append({"bucket": label, "count": count})
    return counts


def _group_average(rows: list[dict[str, Any]], group_key: str, value_key: str, count_key: str = "count") -> list[dict[str, Any]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(group_key) or "Unknown")].append(_as_float(row.get(value_key)))
    result = []
    for group in sorted(grouped):
        values = grouped[group]
        result.append({group_key: group, count_key: len(values), f"average_{value_key}": round(statistics.mean(values), 4) if values else 0.0})
    return result


def build_research_analytics(candidates: list[dict[str, Any]], recent_runs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    recent_runs = list(recent_runs or [])
    candidate_scores = [_as_float(item.get("overall_score")) for item in candidates]
    candidate_confidences = [_as_float(item.get("confidence")) for item in candidates]
    total_runs = len(recent_runs)
    total_candidates = len(candidates)
    score_buckets = _bucket_counts(candidate_scores, [(0, 20, "0-19"), (20, 40, "20-39"), (40, 60, "40-59"), (60, 80, "60-79"), (80, 101, "80-100")])
    confidence_buckets = _bucket_counts(candidate_confidences, [(0, 20, "0-19"), (20, 40, "20-39"), (40, 60, "40-59"), (60, 80, "60-79"), (80, 101, "80-100")])
    sector_counts = Counter(str(item.get("sector") or "Unknown") for item in candidates)
    regime_counts = Counter(str(item.get("market_regime") or "unknown") for item in candidates)
    signal_counts = Counter(str(item.get("signal") or "HOLD").upper() for item in candidates)
    recurring_symbols = Counter(str(item.get("symbol") or "").upper() for item in candidates)
    return {
        "total_research_runs": total_runs,
        "total_candidate_observations": total_candidates,
        "average_candidates_per_run": round(total_candidates / total_runs, 4) if total_runs else 0.0,
        "average_overall_score": round(statistics.mean(candidate_scores), 4) if candidate_scores else 0.0,
        "average_confidence": round(statistics.mean(candidate_confidences), 4) if candidate_confidences else 0.0,
        "score_distribution": score_buckets,
        "confidence_distribution": confidence_buckets,
        "candidate_count_by_sector": [{"sector": sector, "count": count} for sector, count in sorted(sector_counts.items(), key=lambda item: (-item[1], item[0]))],
        "candidate_count_by_regime": [{"market_regime": regime, "count": count} for regime, count in sorted(regime_counts.items(), key=lambda item: (-item[1], item[0]))],
        "signal_distribution": [{"signal": signal, "count": count} for signal, count in sorted(signal_counts.items(), key=lambda item: (-item[1], item[0]))],
        "top_recurring_symbols": [{"symbol": symbol, "count": count} for symbol, count in recurring_symbols.most_common(10)],
        "average_score_by_sector": _group_average(candidates, "sector", "overall_score"),
        "average_confidence_by_sector": _group_average(candidates, "sector", "confidence"),
        "average_score_by_regime": _group_average(candidates, "market_regime", "overall_score"),
        "average_confidence_by_regime": _group_average(candidates, "market_regime", "confidence"),
    }


def fetch_research_dashboard_payload(
    database_url: str | None,
    selected_run_id: str | None = None,
    database_factory=MonitoringDatabase,
) -> dict[str, Any]:
    repository = MonitoringResearchRepository(database_url=database_url)
    payload = {
        "db_connected": repository.db.enabled,
        "latest_research_run": {},
        "recent_research_runs": [],
        "selected_research_run_id": selected_run_id,
        "selected_research_candidates": [],
        "research_analytics": {
            "total_research_runs": 0,
            "total_candidate_observations": 0,
            "average_candidates_per_run": 0.0,
            "average_overall_score": 0.0,
            "average_confidence": 0.0,
            "score_distribution": [],
            "confidence_distribution": [],
            "candidate_count_by_sector": [],
            "candidate_count_by_regime": [],
            "signal_distribution": [],
            "top_recurring_symbols": [],
            "average_score_by_sector": [],
            "average_confidence_by_sector": [],
            "average_score_by_regime": [],
            "average_confidence_by_regime": [],
        },
        "latest_research_summary": {},
    }
    if not repository.db.enabled:
        return payload

    try:
        repository.db.ensure_schema()
        recent_runs = repository.fetch_recent_research_runs(limit=25)
        latest_run = repository.fetch_latest_research_run() or {}
        selected_id = str(selected_run_id or latest_run.get("research_run_id") or "")
        selected_candidates = repository.fetch_research_candidates_for_run(selected_id) if selected_id else []
        all_candidates = repository.fetch_highest_ranked_candidates_across_stored_runs(limit=1000)
        payload.update(
            {
                "latest_research_run": latest_run,
                "recent_research_runs": recent_runs,
                "selected_research_run_id": selected_id,
                "selected_research_candidates": selected_candidates,
                "research_analytics": build_research_analytics(all_candidates, recent_runs),
                "latest_research_summary": latest_run,
            }
        )
        return payload
    finally:
        repository.db.close()
