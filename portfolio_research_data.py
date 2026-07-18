from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any

from config import (
    BENCHMARK_SYMBOL,
    PORTFOLIO_RESEARCH_ALLOW_CASH,
    PORTFOLIO_RESEARCH_DEFAULT_HORIZON,
    PORTFOLIO_RESEARCH_DEFAULT_METHOD,
    PORTFOLIO_RESEARCH_DEFAULT_TOP_N,
    PORTFOLIO_RESEARCH_MAX_GROSS_EXPOSURE,
    PORTFOLIO_RESEARCH_MAX_HOLDINGS,
    PORTFOLIO_RESEARCH_MAX_POSITION_WEIGHT,
    PORTFOLIO_RESEARCH_MAX_REDISTRIBUTION_ITERATIONS,
    PORTFOLIO_RESEARCH_MIN_HOLDINGS,
    PORTFOLIO_RESEARCH_NORMALIZATION_TOLERANCE,
    PORTFOLIO_RESEARCH_SECTOR_CAP,
    PORTFOLIO_RESEARCH_TARGET_VOLATILITY,
)
from evaluation_repository import MonitoringEvaluationRepository
from portfolio_analytics import aggregate_portfolio_metrics, build_method_comparison, calculate_snapshot_returns, calculate_turnover
from portfolio_constraints import apply_portfolio_constraints
from portfolio_research_repository import MonitoringPortfolioResearchRepository
from portfolio_weighting import build_raw_weights
from walk_forward_data import generate_walk_forward_windows


def _as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except Exception:
        return None


def _parse_sector_filter(sector_filter: str | None) -> set[str] | None:
    if not sector_filter:
        return None
    return {part.strip().lower() for part in str(sector_filter).split(",") if part.strip()}


def normalize_portfolio_research_rows(
    rows: list[dict[str, Any]],
    horizon: int,
    start_date: str | None = None,
    end_date: str | None = None,
    research_run_id: str | None = None,
    symbol_filter: str | None = None,
    sector_filter: str | None = None,
    regime_filter: str | None = None,
    signal_filter: str | None = None,
) -> dict[str, Any]:
    start_dt = _parse_date(start_date)
    end_dt = _parse_date(end_date)
    symbol_set = {part.strip().upper() for part in str(symbol_filter or "").split(",") if part.strip()} or None
    sector_set = _parse_sector_filter(sector_filter)
    regime_set = {part.strip().lower() for part in str(regime_filter or "").split(",") if part.strip()} or None
    signal_set = {part.strip().upper() for part in str(signal_filter or "").split(",") if part.strip()} or None

    eligible: list[dict[str, Any]] = []
    warnings = {
        "missing_labels": 0,
        "malformed_dates": 0,
        "duplicate_candidates": 0,
        "missing_returns": 0,
        "filtered_out": 0,
    }

    dedupe_keys: set[tuple[str, str, str]] = set()

    for row in rows:
        status = str(row.get(f"forward_{horizon}d_status") or "").lower()
        if status != "complete":
            warnings["missing_labels"] += 1
            continue

        obs_date = _parse_date(row.get("observation_date"))
        if obs_date is None:
            warnings["malformed_dates"] += 1
            continue

        if start_dt and obs_date < start_dt:
            warnings["filtered_out"] += 1
            continue
        if end_dt and obs_date > end_dt:
            warnings["filtered_out"] += 1
            continue

        run_id = str(row.get("research_run_id") or "")
        symbol = str(row.get("symbol") or "").upper()
        if research_run_id and run_id != str(research_run_id):
            warnings["filtered_out"] += 1
            continue
        if symbol_set and symbol not in symbol_set:
            warnings["filtered_out"] += 1
            continue

        sector = str(row.get("sector") or "Unknown")
        regime = str(row.get("market_regime") or "unknown")
        signal = str(row.get("signal") or "").upper()
        if sector_set and sector.lower() not in sector_set:
            warnings["filtered_out"] += 1
            continue
        if regime_set and regime.lower() not in regime_set:
            warnings["filtered_out"] += 1
            continue
        if signal_set and signal not in signal_set:
            warnings["filtered_out"] += 1
            continue

        forward_return = _as_float(row.get(f"forward_{horizon}d_return"), None)
        benchmark_return = _as_float(row.get(f"forward_{horizon}d_benchmark_return"), None)
        excess_return = _as_float(row.get(f"forward_{horizon}d_excess_return"), None)
        if forward_return is None or benchmark_return is None or excess_return is None:
            warnings["missing_returns"] += 1
            continue

        key = (run_id, symbol, obs_date.isoformat())
        if key in dedupe_keys:
            warnings["duplicate_candidates"] += 1
            continue
        dedupe_keys.add(key)

        copied = dict(row)
        copied["_observation_date"] = obs_date.isoformat()
        copied["_forward_return"] = forward_return
        copied["_benchmark_return"] = benchmark_return
        copied["_excess_return"] = excess_return
        copied["_rank"] = _as_float(row.get("rank"), None)
        copied["_symbol"] = symbol
        copied["_run_id"] = run_id
        eligible.append(copied)

    eligible.sort(key=lambda item: (item.get("_observation_date") or "", item.get("_run_id") or "", int(item.get("_rank") or 10**9), item.get("_symbol") or ""))
    return {"rows": eligible, "warnings": warnings}


def build_formation_snapshots(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row.get("_run_id") or ""), str(row.get("_observation_date") or ""))].append(row)

    snapshots: list[dict[str, Any]] = []
    for (run_id, formation_date), members in sorted(grouped.items(), key=lambda item: (item[0][1], item[0][0])):
        ordered = sorted(members, key=lambda row: (int(row.get("_rank") or 10**9), row.get("_symbol") or ""))
        snapshots.append(
            {
                "research_run_id": run_id,
                "formation_date": formation_date,
                "rows": ordered,
            }
        )
    return snapshots


def _apply_method_to_snapshot(
    snapshot: dict[str, Any],
    method: str,
    top_n: int | None,
    benchmark_symbol: str,
    max_position_weight: float,
    sector_cap: float,
    min_holdings: int,
    max_holdings: int | None,
    target_volatility: float | None,
    max_gross_exposure: float,
    allow_cash: bool,
    normalization_tolerance: float,
    max_redistribution_iterations: int,
    previous_weights: dict[str, float] | None,
) -> dict[str, Any]:
    warnings: list[str] = []
    selected = list(snapshot.get("rows") or [])
    if top_n is not None and int(top_n) > 0:
        selected = selected[: int(top_n)]

    if not selected:
        return {
            "status": "skipped",
            "warning": "no eligible holdings",
            "snapshot": {
                "formation_date": snapshot.get("formation_date"),
                "research_run_id": snapshot.get("research_run_id"),
                "holding_count": 0,
                "invested_weight": 0.0,
                "cash_weight": 1.0,
                "portfolio_return": None,
                "benchmark_return": None,
                "excess_return": None,
                "warnings": ["no eligible holdings"],
                "status": "skipped",
                "holdings": [],
                "symbol_contribution": [],
                "sector_contribution": [],
                "signal_contribution": [],
                "regime_contribution": [],
                "concentration_metrics": {},
            },
            "weights_with_cash": {"CASH": 1.0},
        }

    raw = build_raw_weights(
        selected,
        method=method,
        target_volatility=target_volatility,
        max_gross_exposure=max_gross_exposure,
        allow_leverage=False,
    )
    warnings.extend(raw.get("warnings") or [])
    if raw.get("status") != "ok":
        return {
            "status": "skipped",
            "warning": raw.get("status") or "weighting_failed",
            "snapshot": {
                "formation_date": snapshot.get("formation_date"),
                "research_run_id": snapshot.get("research_run_id"),
                "holding_count": 0,
                "invested_weight": 0.0,
                "cash_weight": 1.0,
                "portfolio_return": None,
                "benchmark_return": None,
                "excess_return": None,
                "warnings": warnings,
                "status": "skipped",
                "holdings": [],
                "symbol_contribution": [],
                "sector_contribution": [],
                "signal_contribution": [],
                "regime_contribution": [],
                "concentration_metrics": {},
            },
            "weights_with_cash": {"CASH": 1.0},
        }

    row_by_symbol = {str(row.get("_symbol") or ""): row for row in selected}
    constrained = apply_portfolio_constraints(
        raw.get("weights") or {},
        row_by_symbol=row_by_symbol,
        max_position_weight=max_position_weight,
        sector_cap=sector_cap,
        min_holdings=min_holdings,
        max_holdings=max_holdings,
        max_gross_exposure=max_gross_exposure,
        allow_cash=allow_cash,
        normalization_tolerance=normalization_tolerance,
        max_iterations=max_redistribution_iterations,
    )
    warnings.extend(constrained.get("warnings") or [])

    weights = constrained.get("weights") or {}
    cash_weight = float(constrained.get("cash_weight") or 0.0)
    holdings_payload = []
    sector_exposure: dict[str, float] = defaultdict(float)
    for symbol, weight in sorted(weights.items()):
        row = row_by_symbol.get(symbol) or {}
        sector = str(row.get("sector") or "Unknown")
        sector_exposure[sector] += float(weight)
        holdings_payload.append(
            {
                "symbol": symbol,
                "weight": float(weight),
                "rank": row.get("rank"),
                "overall_score": row.get("overall_score"),
                "confidence": row.get("confidence"),
                "forward_return": row.get("_forward_return"),
                "benchmark_return": row.get("_benchmark_return"),
                "excess_return": row.get("_excess_return"),
                "sector": sector,
                "signal": row.get("signal"),
                "market_regime": row.get("market_regime"),
            }
        )

    weight_vector = dict(weights)
    weight_vector["CASH"] = cash_weight
    turnover = calculate_turnover(previous_weights, weight_vector)

    snapshot_payload = calculate_snapshot_returns(
        formation_date=str(snapshot.get("formation_date") or ""),
        research_run_id=str(snapshot.get("research_run_id") or ""),
        holdings=holdings_payload,
        benchmark_symbol=benchmark_symbol,
        cash_weight=cash_weight,
        turnover=turnover,
        warnings=warnings,
        status="completed" if constrained.get("status") == "ok" else str(constrained.get("status") or "completed"),
    )
    snapshot_payload["sector_exposure"] = {key: round(value, 10) for key, value in sorted(sector_exposure.items())}

    return {
        "status": "completed" if snapshot_payload.get("status") == "completed" else "skipped",
        "warning": None,
        "snapshot": snapshot_payload,
        "weights_with_cash": weight_vector,
    }


def run_portfolio_method(
    rows: list[dict[str, Any]],
    method: str = PORTFOLIO_RESEARCH_DEFAULT_METHOD,
    horizon: int = PORTFOLIO_RESEARCH_DEFAULT_HORIZON,
    top_n: int = PORTFOLIO_RESEARCH_DEFAULT_TOP_N,
    benchmark_symbol: str = BENCHMARK_SYMBOL,
    max_position_weight: float = PORTFOLIO_RESEARCH_MAX_POSITION_WEIGHT,
    sector_cap: float = PORTFOLIO_RESEARCH_SECTOR_CAP,
    min_holdings: int = PORTFOLIO_RESEARCH_MIN_HOLDINGS,
    max_holdings: int = PORTFOLIO_RESEARCH_MAX_HOLDINGS,
    target_volatility: float = PORTFOLIO_RESEARCH_TARGET_VOLATILITY,
    max_gross_exposure: float = PORTFOLIO_RESEARCH_MAX_GROSS_EXPOSURE,
    allow_cash: bool = PORTFOLIO_RESEARCH_ALLOW_CASH,
    normalization_tolerance: float = PORTFOLIO_RESEARCH_NORMALIZATION_TOLERANCE,
    max_redistribution_iterations: int = PORTFOLIO_RESEARCH_MAX_REDISTRIBUTION_ITERATIONS,
) -> dict[str, Any]:
    snapshots = build_formation_snapshots(rows)
    results: list[dict[str, Any]] = []
    skipped = 0
    warnings: list[str] = []
    previous_weights: dict[str, float] | None = None
    for index, snapshot in enumerate(snapshots, start=1):
        item = _apply_method_to_snapshot(
            snapshot,
            method=method,
            top_n=top_n,
            benchmark_symbol=benchmark_symbol,
            max_position_weight=max_position_weight,
            sector_cap=sector_cap,
            min_holdings=min_holdings,
            max_holdings=max_holdings,
            target_volatility=target_volatility,
            max_gross_exposure=max_gross_exposure,
            allow_cash=allow_cash,
            normalization_tolerance=normalization_tolerance,
            max_redistribution_iterations=max_redistribution_iterations,
            previous_weights=previous_weights,
        )
        payload = dict(item["snapshot"])
        payload["snapshot_id"] = f"{method}-{index}"
        payload["horizon"] = int(horizon)
        payload["weighting_method"] = method
        results.append(payload)
        previous_weights = item.get("weights_with_cash")
        if item.get("status") != "completed":
            skipped += 1
        if item.get("warning"):
            warnings.append(str(item.get("warning")))

    analytics = aggregate_portfolio_metrics(results)
    return {
        "method": method,
        "horizon": int(horizon),
        "snapshots": results,
        "analytics": analytics,
        "warnings": sorted(set(warnings)),
        "portfolio_count": len(results),
        "skipped_count": skipped,
    }


def run_method_comparison(rows: list[dict[str, Any]], methods: list[str], **kwargs: Any) -> dict[str, Any]:
    method_results = [run_portfolio_method(rows, method=method, **kwargs) for method in methods]
    return {
        "method_results": method_results,
        "comparison_table": build_method_comparison(method_results),
    }


def build_walk_forward_portfolio_validation(
    rows: list[dict[str, Any]],
    method: str,
    horizon: int,
    benchmark_symbol: str,
    top_n: int,
    window_type: str = "rolling",
    training_periods: int = 3,
    validation_periods: int = 1,
    step_periods: int = 1,
) -> dict[str, Any]:
    # Reuse existing walk-forward window generation and apply one fixed portfolio config per window.
    eligible = [dict(item, period_start=date.fromisoformat(str(item["_observation_date"])[:7] + "-01")) for item in rows]
    windows = generate_walk_forward_windows(
        eligible,
        horizon=horizon,
        benchmark_symbol=benchmark_symbol,
        window_type=window_type,
        training_periods=training_periods,
        validation_periods=validation_periods,
        step_periods=step_periods,
        min_training_sample=1,
        min_validation_sample=1,
    )
    results = []
    for window in windows:
        training_result = run_portfolio_method(
            window.get("training_rows") or [],
            method=method,
            horizon=horizon,
            top_n=top_n,
            benchmark_symbol=benchmark_symbol,
        )
        validation_result = run_portfolio_method(
            window.get("validation_rows") or [],
            method=method,
            horizon=horizon,
            top_n=top_n,
            benchmark_symbol=benchmark_symbol,
        )
        train_excess = _as_float((training_result.get("analytics") or {}).get("average_portfolio_excess_return"), None)
        val_excess = _as_float((validation_result.get("analytics") or {}).get("average_portfolio_excess_return"), None)
        degradation = None if train_excess is None or val_excess is None else round(val_excess - train_excess, 6)
        relative = None if degradation is None or train_excess is None or abs(train_excess) <= 1e-9 else round(degradation / abs(train_excess), 6)

        results.append(
            {
                "window_id": window.get("window_id"),
                "training_portfolio_excess_return": train_excess,
                "validation_portfolio_excess_return": val_excess,
                "degradation": degradation,
                "relative_degradation": relative,
                "turnover_change": None
                if (training_result.get("analytics") or {}).get("average_turnover") is None or (validation_result.get("analytics") or {}).get("average_turnover") is None
                else round(float((validation_result.get("analytics") or {}).get("average_turnover") or 0.0) - float((training_result.get("analytics") or {}).get("average_turnover") or 0.0), 6),
                "concentration_change": None
                if (training_result.get("analytics") or {}).get("average_concentration") is None or (validation_result.get("analytics") or {}).get("average_concentration") is None
                else round(float((validation_result.get("analytics") or {}).get("average_concentration") or 0.0) - float((training_result.get("analytics") or {}).get("average_concentration") or 0.0), 6),
                "status": "completed" if window.get("status") == "completed" else "skipped",
            }
        )
    return {"windows": results}


def fetch_portfolio_research_dashboard_payload(database_url: str | None) -> dict[str, Any]:
    repository = MonitoringPortfolioResearchRepository(database_url=database_url)
    payload = {
        "db_connected": repository.db.enabled,
        "total_runs": 0,
        "latest_run": {},
        "snapshots": [],
    }
    if not repository.db.enabled:
        return payload
    try:
        repository.db.ensure_schema()
        latest_run = repository.fetch_latest_run() or {}
        snapshots = repository.fetch_snapshots_for_run(str(latest_run.get("run_id") or "")) if latest_run.get("run_id") else []
        payload["total_runs"] = repository.count_runs()
        payload["latest_run"] = latest_run
        payload["snapshots"] = snapshots
        return payload
    finally:
        repository.close()


def execute_portfolio_research(
    database_url: str | None,
    horizon: int,
    method: str,
    top_n: int,
    start_date: str | None = None,
    end_date: str | None = None,
    benchmark_symbol: str = BENCHMARK_SYMBOL,
    research_run_id: str | None = None,
    symbol_filter: str | None = None,
    sector_filter: str | None = None,
    regime_filter: str | None = None,
    signal_filter: str | None = None,
    methods: list[str] | None = None,
) -> dict[str, Any]:
    repo = MonitoringEvaluationRepository(database_url=database_url)
    try:
        repo.db.ensure_schema()
        rows = repo.fetch_evaluation_rows_for_dashboard(limit=10000)
    finally:
        repo.close()

    normalized = normalize_portfolio_research_rows(
        rows,
        horizon=horizon,
        start_date=start_date,
        end_date=end_date,
        research_run_id=research_run_id,
        symbol_filter=symbol_filter,
        sector_filter=sector_filter,
        regime_filter=regime_filter,
        signal_filter=signal_filter,
    )
    eligible_rows = normalized["rows"]

    selected_methods = methods or [method]
    comparison = run_method_comparison(
        eligible_rows,
        methods=selected_methods,
        horizon=horizon,
        top_n=top_n,
        benchmark_symbol=benchmark_symbol,
    )
    primary = next((item for item in comparison["method_results"] if item.get("method") == method), comparison["method_results"][0] if comparison["method_results"] else {
        "method": method,
        "snapshots": [],
        "analytics": aggregate_portfolio_metrics([]),
        "warnings": ["no method results"],
    })

    walk_forward = build_walk_forward_portfolio_validation(
        eligible_rows,
        method=method,
        horizon=horizon,
        benchmark_symbol=benchmark_symbol,
        top_n=top_n,
    )

    return {
        "eligible_row_count": len(eligible_rows),
        "normalization_warnings": normalized.get("warnings") or {},
        "primary_result": primary,
        "comparison": comparison,
        "walk_forward": walk_forward,
    }
