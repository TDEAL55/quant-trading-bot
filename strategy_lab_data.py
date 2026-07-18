from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from config import (
    BENCHMARK_SYMBOL,
    PORTFOLIO_RESEARCH_DEFAULT_HORIZON,
    PORTFOLIO_RESEARCH_DEFAULT_METHOD,
    PORTFOLIO_RESEARCH_DEFAULT_TOP_N,
    PORTFOLIO_RESEARCH_MAX_POSITION_WEIGHT,
    PORTFOLIO_RESEARCH_SECTOR_CAP,
    STRATEGY_LAB_COMMISSION_PER_TRADE,
    STRATEGY_LAB_DEFAULT_COMPARISON_MODE,
    STRATEGY_LAB_DEFAULT_HORIZON,
    STRATEGY_LAB_FIXED_REBALANCE_COST,
    STRATEGY_LAB_MAX_ROWS,
    STRATEGY_LAB_MAX_STRATEGIES,
    STRATEGY_LAB_MIN_HOLDINGS,
    STRATEGY_LAB_RELATIVE_DEGRADATION_EPSILON,
    STRATEGY_LAB_SCORECARD_MIN_WINDOWS,
    STRATEGY_LAB_SLIPPAGE_BPS,
    STRATEGY_LAB_TURNOVER_COST_BPS,
)
from evaluation_repository import MonitoringEvaluationRepository
from portfolio_research_data import (
    build_walk_forward_portfolio_validation,
    normalize_portfolio_research_rows,
    run_portfolio_method,
)
from strategy_comparison import leaderboard, pairwise_common_snapshot_comparison, strategy_metrics
from strategy_costs import apply_transaction_costs
from strategy_definitions import StrategyDefinition, built_in_strategy_definitions, definition_by_id
from strategy_lab_repository import MonitoringStrategyLabRepository
from strategy_scorecard import build_strategy_scorecard


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _snapshot_key(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row.get("_run_id") or ""), str(row.get("_observation_date") or ""))


def _passes_factor_rule(row: dict[str, Any], factor_mins: dict[str, Any], factor_maxs: dict[str, Any]) -> bool:
    for name, minimum in factor_mins.items():
        value = _as_float(row.get(name), None)
        if value is None:
            return False
        if value < float(minimum):
            return False
    for name, maximum in factor_maxs.items():
        value = _as_float(row.get(name), None)
        if value is None:
            return False
        if value > float(maximum):
            return False
    return True


def apply_strategy_filters(rows: list[dict[str, Any]], definition: StrategyDefinition) -> dict[str, Any]:
    rules = dict(definition.filter_rules or {})
    required_signals = {str(item).upper() for item in list(rules.get("required_signals") or [])}
    permitted_regimes = {str(item).lower() for item in list(rules.get("permitted_regimes") or [])}
    permitted_sectors = {str(item).lower() for item in list(rules.get("permitted_sectors") or [])}
    factor_mins = dict(rules.get("factor_mins") or {})
    factor_maxs = dict(rules.get("factor_maxs") or {})
    min_score = _as_float(rules.get("min_overall_score"), None)
    min_confidence = _as_float(rules.get("min_confidence"), None)
    max_rank = _as_float(rules.get("max_rank"), None)

    filtered = []
    warnings = {
        "missing_required_fields": 0,
        "rule_excluded": 0,
    }
    for row in rows:
        signal = str(row.get("signal") or "").upper()
        regime = str(row.get("market_regime") or "unknown").lower()
        sector = str(row.get("sector") or "unknown").lower()
        score = _as_float(row.get("overall_score"), None)
        confidence = _as_float(row.get("confidence"), None)
        rank = _as_float(row.get("rank"), None)

        if required_signals and signal not in required_signals:
            warnings["rule_excluded"] += 1
            continue
        if permitted_regimes and regime not in permitted_regimes:
            warnings["rule_excluded"] += 1
            continue
        if permitted_sectors and sector not in permitted_sectors:
            warnings["rule_excluded"] += 1
            continue
        if min_score is not None:
            if score is None:
                warnings["missing_required_fields"] += 1
                continue
            if score < float(min_score):
                warnings["rule_excluded"] += 1
                continue
        if min_confidence is not None:
            if confidence is None:
                warnings["missing_required_fields"] += 1
                continue
            if confidence < float(min_confidence):
                warnings["rule_excluded"] += 1
                continue
        if max_rank is not None:
            if rank is None:
                warnings["missing_required_fields"] += 1
                continue
            if rank > float(max_rank):
                warnings["rule_excluded"] += 1
                continue
        if not _passes_factor_rule(row, factor_mins, factor_maxs):
            warnings["missing_required_fields"] += 1
            continue

        filtered.append(row)

    filtered.sort(key=lambda item: (item.get("_observation_date") or "", int(item.get("_rank") or 10**9), item.get("_symbol") or ""))
    return {"rows": filtered, "warnings": warnings}


def _common_snapshot_keys(filtered_by_strategy: dict[str, list[dict[str, Any]]], min_holdings: int) -> set[tuple[str, str]]:
    key_sets = []
    for rows in filtered_by_strategy.values():
        counts: dict[tuple[str, str], int] = defaultdict(int)
        for row in rows:
            counts[_snapshot_key(row)] += 1
        key_sets.append({key for key, count in counts.items() if count >= int(min_holdings)})
    if not key_sets:
        return set()
    shared = key_sets[0]
    for item in key_sets[1:]:
        shared = shared & item
    return shared


def _regime_summary(strategy_id: str, snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    regimes = ["strong_bull", "bull", "neutral", "bear", "strong_bear", "unknown"]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for snapshot in snapshots:
        for holding in snapshot.get("holdings") or []:
            regime = str(holding.get("market_regime") or "unknown").lower()
            grouped[regime].append(snapshot)
    rows = []
    for regime in regimes:
        snapshots_for_regime = grouped.get(regime, [])
        net_excess = [float(item.get("net_excess_return") or item.get("excess_return") or 0.0) for item in snapshots_for_regime]
        if not snapshots_for_regime:
            continue
        rows.append(
            {
                "strategy_id": strategy_id,
                "market_regime": regime,
                "observation_count": len(snapshots_for_regime),
                "portfolio_count": len(snapshots_for_regime),
                "average_net_return": round(sum(float(item.get("net_portfolio_return") or item.get("portfolio_return") or 0.0) for item in snapshots_for_regime) / len(snapshots_for_regime), 6),
                "average_net_excess_return": round(sum(net_excess) / len(net_excess), 6),
                "positive_net_excess_rate": round(len([v for v in net_excess if v > 0]) / len(net_excess), 6) if net_excess else None,
                "volatility": None,
                "drawdown": None,
                "sharpe_like_ratio": None,
                "turnover": round(sum(float(item.get("turnover") or 0.0) for item in snapshots_for_regime if item.get("turnover") is not None) / len([1 for item in snapshots_for_regime if item.get("turnover") is not None]), 6) if [1 for item in snapshots_for_regime if item.get("turnover") is not None] else None,
                "concentration": round(sum(float((item.get("concentration_metrics") or {}).get("hhi") or 0.0) for item in snapshots_for_regime) / len(snapshots_for_regime), 6),
                "warnings": [],
            }
        )
    return rows


def _factor_exposure(rows: list[dict[str, Any]]) -> dict[str, Any]:
    factors = [
        "trend_score",
        "momentum_score",
        "volume_score",
        "volatility_score",
        "liquidity_score",
        "market_regime_score",
        "risk_quality_score",
        "overall_score",
        "confidence",
        "rank",
    ]
    payload: dict[str, Any] = {}
    for factor in factors:
        values = [float(row[factor]) for row in rows if _as_float(row.get(factor), None) is not None]
        payload[factor] = round(sum(values) / len(values), 6) if values else None
    return payload


def _diff_exposure(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    diff: dict[str, Any] = {}
    for key, value in current.items():
        base = baseline.get(key)
        if value is None or base is None:
            diff[f"{key}_difference"] = None
        else:
            diff[f"{key}_difference"] = round(float(value) - float(base), 6)
    return diff


def run_strategy_laboratory(
    database_url: str | None,
    strategy_ids: list[str] | None = None,
    horizon: int = STRATEGY_LAB_DEFAULT_HORIZON,
    benchmark: str = BENCHMARK_SYMBOL,
    start_date: str | None = None,
    end_date: str | None = None,
    comparison_mode: str = STRATEGY_LAB_DEFAULT_COMPARISON_MODE,
    top_n: int = PORTFOLIO_RESEARCH_DEFAULT_TOP_N,
    weighting_method: str = PORTFOLIO_RESEARCH_DEFAULT_METHOD,
    max_position_weight: float = PORTFOLIO_RESEARCH_MAX_POSITION_WEIGHT,
    sector_cap: float = PORTFOLIO_RESEARCH_SECTOR_CAP,
    min_holdings: int = STRATEGY_LAB_MIN_HOLDINGS,
    commission_per_trade: float = STRATEGY_LAB_COMMISSION_PER_TRADE,
    fixed_rebalance_cost: float = STRATEGY_LAB_FIXED_REBALANCE_COST,
    turnover_cost_bps: float = STRATEGY_LAB_TURNOVER_COST_BPS,
    slippage_bps: float = STRATEGY_LAB_SLIPPAGE_BPS,
    walk_forward_enabled: bool = True,
) -> dict[str, Any]:
    repo = MonitoringEvaluationRepository(database_url=database_url)
    try:
        repo.db.ensure_schema()
        rows = repo.fetch_evaluation_rows_for_dashboard(limit=int(STRATEGY_LAB_MAX_ROWS))
    finally:
        repo.close()

    normalized = normalize_portfolio_research_rows(rows, horizon=horizon, start_date=start_date, end_date=end_date)
    all_rows = normalized["rows"]

    definitions = built_in_strategy_definitions()
    selected_ids = strategy_ids or [item.strategy_id for item in definitions if item.enabled]
    selected_ids = selected_ids[: int(STRATEGY_LAB_MAX_STRATEGIES)]
    selected_definitions = [definition_by_id(definitions, strategy_id) for strategy_id in selected_ids]

    filtered_by_strategy: dict[str, list[dict[str, Any]]] = {}
    filter_warnings: dict[str, dict[str, int]] = {}
    for definition in selected_definitions:
        filtered = apply_strategy_filters(all_rows, definition)
        filtered_by_strategy[definition.strategy_id] = filtered["rows"]
        filter_warnings[definition.strategy_id] = filtered["warnings"]

    mode = str(comparison_mode or STRATEGY_LAB_DEFAULT_COMPARISON_MODE).strip().lower()
    if mode not in {"common_snapshots", "all_available"}:
        mode = STRATEGY_LAB_DEFAULT_COMPARISON_MODE

    common_keys = _common_snapshot_keys(filtered_by_strategy, min_holdings=min_holdings)
    if mode == "common_snapshots":
        for strategy_id in list(filtered_by_strategy.keys()):
            filtered_by_strategy[strategy_id] = [row for row in filtered_by_strategy[strategy_id] if _snapshot_key(row) in common_keys]

    baseline_rows = filtered_by_strategy.get("baseline_scanner", [])
    baseline_exposure = _factor_exposure(baseline_rows)

    cost_config = {
        "commission_per_trade": float(commission_per_trade),
        "fixed_rebalance_cost": float(fixed_rebalance_cost),
        "turnover_cost_bps": float(turnover_cost_bps),
        "slippage_bps": float(slippage_bps),
    }

    strategy_results = []
    for definition in selected_definitions:
        strategy_rows = filtered_by_strategy.get(definition.strategy_id, [])
        portfolio_config = dict(definition.portfolio_configuration or {})
        method = weighting_method or portfolio_config.get("weighting_method") or PORTFOLIO_RESEARCH_DEFAULT_METHOD
        strategy_top_n = int(top_n or portfolio_config.get("top_n") or PORTFOLIO_RESEARCH_DEFAULT_TOP_N)
        strategy_horizon = int(portfolio_config.get("horizon") or horizon)
        strategy_benchmark = str(portfolio_config.get("benchmark") or benchmark)

        simulated = run_portfolio_method(
            strategy_rows,
            method=method,
            horizon=strategy_horizon,
            top_n=strategy_top_n,
            benchmark_symbol=strategy_benchmark,
            max_position_weight=float(max_position_weight),
            sector_cap=float(sector_cap),
            min_holdings=int(min_holdings),
        )
        snapshots = apply_transaction_costs(simulated.get("snapshots") or [], cost_config)

        metrics = strategy_metrics(
            snapshots=snapshots,
            eligible_candidate_count=len(strategy_rows),
            warnings=list(simulated.get("warnings") or []),
        )

        walk_forward = {
            "windows": [],
            "completed_windows": 0,
            "validation_average_net_excess_return": None,
            "positive_validation_window_rate": None,
            "degradation_average": None,
            "degradation_volatility": None,
            "sign_consistency": None,
            "early_vs_recent_delta": None,
            "performance_decay_flag": False,
            "unstable_window_count": 0,
        }
        if walk_forward_enabled:
            wf = build_walk_forward_portfolio_validation(
                strategy_rows,
                method=method,
                horizon=strategy_horizon,
                benchmark_symbol=strategy_benchmark,
                top_n=strategy_top_n,
            )
            windows = list(wf.get("windows") or [])
            validations = [float(item.get("validation_portfolio_excess_return") or 0.0) for item in windows if item.get("status") == "completed" and item.get("validation_portfolio_excess_return") is not None]
            degradations = [float(item.get("degradation") or 0.0) for item in windows if item.get("status") == "completed" and item.get("degradation") is not None]
            positive = len([v for v in validations if v > 0])
            sign_consistency = None
            if degradations:
                sign = [1 if v > 0 else -1 if v < 0 else 0 for v in degradations]
                nonzero = [v for v in sign if v != 0]
                if nonzero:
                    sign_consistency = round(abs(sum(nonzero)) / len(nonzero), 6)
            early_vs_recent = None
            if len(validations) >= 2:
                early_vs_recent = round(validations[-1] - validations[0], 6)
            avg_deg = round(sum(degradations) / len(degradations), 6) if degradations else None
            deg_vol = None
            if len(degradations) > 1:
                mean_deg = sum(degradations) / len(degradations)
                deg_vol = round((sum((v - mean_deg) ** 2 for v in degradations) / len(degradations)) ** 0.5, 6)
            walk_forward = {
                "windows": windows,
                "completed_windows": len(validations),
                "validation_average_net_excess_return": round(sum(validations) / len(validations), 6) if validations else None,
                "positive_validation_window_rate": round(positive / len(validations), 6) if validations else None,
                "degradation_average": avg_deg,
                "degradation_volatility": deg_vol,
                "sign_consistency": sign_consistency,
                "early_vs_recent_delta": early_vs_recent,
                "performance_decay_flag": bool(avg_deg is not None and avg_deg < -abs(STRATEGY_LAB_RELATIVE_DEGRADATION_EPSILON)),
                "unstable_window_count": len([item for item in degradations if item < -abs(STRATEGY_LAB_RELATIVE_DEGRADATION_EPSILON)]),
            }

        regime = _regime_summary(definition.strategy_id, snapshots)
        exposure = _factor_exposure(strategy_rows)
        exposure_diff = _diff_exposure(exposure, baseline_exposure)

        scorecard = build_strategy_scorecard(
            metrics={
                "average_net_excess_return": metrics.get("average_net_excess_return"),
                "positive_net_excess_rate": metrics.get("positive_net_excess_rate"),
                "maximum_drawdown": metrics.get("maximum_drawdown"),
                "average_turnover": metrics.get("average_turnover"),
                "average_concentration": metrics.get("hhi"),
                "completed_portfolio_count": metrics.get("completed_portfolio_count"),
            },
            walk_forward_summary=walk_forward,
            data_quality={
                **normalized.get("warnings", {}),
                **filter_warnings.get(definition.strategy_id, {}),
            },
            min_windows=int(STRATEGY_LAB_SCORECARD_MIN_WINDOWS),
        )

        strategy_results.append(
            {
                "strategy_id": definition.strategy_id,
                "strategy_name": definition.strategy_name,
                "definition": {
                    "strategy_id": definition.strategy_id,
                    "strategy_name": definition.strategy_name,
                    "description": definition.description,
                    "version": definition.version,
                    "enabled": definition.enabled,
                    "filter_rules": definition.filter_rules,
                    "ranking_convention": definition.ranking_convention,
                    "portfolio_configuration": definition.portfolio_configuration,
                    "supported_horizons": definition.supported_horizons,
                    "created_at": definition.created_at,
                    "configuration_fingerprint": definition.configuration_fingerprint,
                },
                "eligible_rows": strategy_rows,
                "eligible_candidate_count": len(strategy_rows),
                "snapshots": snapshots,
                "analytics": metrics,
                "walk_forward": walk_forward,
                "regime": regime,
                "factor_exposure": {"absolute": exposure, "difference_vs_baseline": exposure_diff},
                "scorecard": scorecard,
                "warnings": simulated.get("warnings") or [],
                "strategy_specific_exclusions": filter_warnings.get(definition.strategy_id, {}),
            }
        )

    pairwise = pairwise_common_snapshot_comparison(strategy_results)
    ranked = leaderboard(strategy_results)

    common_snapshot_count = len(common_keys)
    summary = {
        "comparison_mode": mode,
        "common_snapshot_count": common_snapshot_count,
        "all_available_snapshot_count": max((item.get("analytics") or {}).get("formation_snapshot_count", 0) for item in strategy_results) if strategy_results else 0,
        "strategy_count": len(strategy_results),
        "overall_data_quality": normalized.get("warnings", {}),
        "leaderboard_order": [item.get("strategy_id") for item in ranked],
    }

    return {
        "definitions": [item["definition"] for item in strategy_results],
        "strategy_results": strategy_results,
        "pairwise": pairwise,
        "leaderboard": ranked,
        "summary": summary,
        "normalization_warnings": normalized.get("warnings", {}),
        "cost_configuration": cost_config,
    }


def persist_strategy_laboratory_run(
    database_url: str | None,
    run_result: dict[str, Any],
    horizon: int,
    benchmark: str,
    comparison_mode: str,
    start_date: str | None,
    end_date: str | None,
    duration_seconds: float,
    performance: dict[str, Any],
) -> dict[str, Any]:
    repository = MonitoringStrategyLabRepository(database_url=database_url)
    run_id = f"strategy-lab-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    payload = {
        "run_id": run_id,
        "created_at": _utc_iso(),
        "horizon": int(horizon),
        "benchmark": benchmark,
        "comparison_mode": comparison_mode,
        "start_date": start_date,
        "end_date": end_date,
        "strategy_ids": [item.get("strategy_id") for item in run_result.get("strategy_results") or []],
        "portfolio_configuration": {
            "default_top_n": PORTFOLIO_RESEARCH_DEFAULT_TOP_N,
            "default_weighting_method": PORTFOLIO_RESEARCH_DEFAULT_METHOD,
            "default_horizon": PORTFOLIO_RESEARCH_DEFAULT_HORIZON,
        },
        "transaction_cost_configuration": run_result.get("cost_configuration") or {},
        "status": "completed",
        "duration_seconds": round(float(duration_seconds), 6),
        "error_message": None,
        "summary": run_result.get("summary") or {},
        "performance": performance,
    }
    results = []
    for item in run_result.get("strategy_results") or []:
        analytics = item.get("analytics") or {}
        results.append(
            {
                "strategy_id": item.get("strategy_id"),
                "eligible_candidate_count": item.get("eligible_candidate_count", 0),
                "snapshot_count": analytics.get("formation_snapshot_count", 0),
                "completed_count": analytics.get("completed_portfolio_count", 0),
                "skipped_count": analytics.get("skipped_portfolio_count", 0),
                "analytics": analytics,
                "scorecard": item.get("scorecard") or {},
                "walk_forward": item.get("walk_forward") or {},
                "regime": item.get("regime") or [],
                "factor_exposure": item.get("factor_exposure") or {},
                "warnings": item.get("warnings") or [],
            }
        )
    try:
        return repository.save_run(
            payload=type("_Payload", (), {
                "definitions": run_result.get("definitions") or [],
                "run": payload,
                "results": results,
                "pairwise": run_result.get("pairwise") or [],
            })()
        )
    finally:
        repository.close()


def fetch_strategy_lab_dashboard_payload(database_url: str | None) -> dict[str, Any]:
    repository = MonitoringStrategyLabRepository(database_url=database_url)
    payload = {
        "db_connected": repository.db.enabled,
        "total_runs": 0,
        "latest_run": {},
        "results": [],
        "pairwise": [],
    }
    if not repository.db.enabled:
        return payload
    try:
        repository.db.ensure_schema()
        latest_run = repository.fetch_latest_run() or {}
        run_id = str(latest_run.get("run_id") or "")
        payload["total_runs"] = repository.count_runs()
        payload["latest_run"] = latest_run
        payload["results"] = repository.fetch_results_for_run(run_id) if run_id else []
        payload["pairwise"] = repository.fetch_pairwise_for_run(run_id) if run_id else []
        return payload
    finally:
        repository.close()
