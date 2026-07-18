from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from typing import Any

from config import (
    BENCHMARK_SYMBOL,
    STRATEGY_LAB_COMMISSION_PER_TRADE,
    STRATEGY_LAB_DEFAULT_COMPARISON_MODE,
    STRATEGY_LAB_DEFAULT_HORIZON,
    STRATEGY_LAB_FIXED_REBALANCE_COST,
    STRATEGY_LAB_MIN_HOLDINGS,
    STRATEGY_LAB_SLIPPAGE_BPS,
    STRATEGY_LAB_TURNOVER_COST_BPS,
)
from logger_setup import logger
from strategy_lab_data import fetch_strategy_lab_dashboard_payload, persist_strategy_laboratory_run, run_strategy_laboratory


def _log(event: str, **fields: Any) -> None:
    parts = [event]
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{key}={value}")
    logger.info(" ".join(parts))


def run_strategy_lab(
    database_url: str | None = None,
    strategy_ids: list[str] | None = None,
    horizon: int = STRATEGY_LAB_DEFAULT_HORIZON,
    benchmark: str = BENCHMARK_SYMBOL,
    start_date: str | None = None,
    end_date: str | None = None,
    comparison_mode: str = STRATEGY_LAB_DEFAULT_COMPARISON_MODE,
    top_n: int = 5,
    portfolio_weighting_method: str = "equal_weight",
    maximum_position_weight: float = 0.30,
    sector_cap: float = 0.50,
    minimum_holdings: int = STRATEGY_LAB_MIN_HOLDINGS,
    commission: float = STRATEGY_LAB_COMMISSION_PER_TRADE,
    fixed_rebalance_cost: float = STRATEGY_LAB_FIXED_REBALANCE_COST,
    turnover_cost_bps: float = STRATEGY_LAB_TURNOVER_COST_BPS,
    slippage_bps: float = STRATEGY_LAB_SLIPPAGE_BPS,
    walk_forward_enabled: bool = True,
    dry_run: bool = False,
    persist: bool = False,
    output_format: str = "json",
) -> dict[str, Any]:
    started = time.perf_counter()
    filter_started = time.perf_counter()
    run = run_strategy_laboratory(
        database_url=database_url,
        strategy_ids=strategy_ids,
        horizon=horizon,
        benchmark=benchmark,
        start_date=start_date,
        end_date=end_date,
        comparison_mode=comparison_mode,
        top_n=top_n,
        weighting_method=portfolio_weighting_method,
        max_position_weight=maximum_position_weight,
        sector_cap=sector_cap,
        min_holdings=minimum_holdings,
        commission_per_trade=commission,
        fixed_rebalance_cost=fixed_rebalance_cost,
        turnover_cost_bps=turnover_cost_bps,
        slippage_bps=slippage_bps,
        walk_forward_enabled=walk_forward_enabled,
    )
    total_duration = round(time.perf_counter() - started, 6)
    filter_duration = round(time.perf_counter() - filter_started, 6)

    strategy_count = len(run.get("strategy_results") or [])
    snapshots_total = sum(int((item.get("analytics") or {}).get("formation_snapshot_count") or 0) for item in run.get("strategy_results") or [])

    performance = {
        "evaluation_rows_loaded": sum(int(item.get("eligible_candidate_count") or 0) for item in run.get("strategy_results") or []),
        "strategy_filtering_time": filter_duration,
        "portfolio_simulation_time": None,
        "transaction_cost_time": None,
        "comparative_analytics_time": None,
        "pairwise_comparison_time": None,
        "walk_forward_time": None,
        "persistence_time": None,
        "dashboard_payload_time": None,
        "total_duration": total_duration,
        "average_time_per_strategy": round(total_duration / max(strategy_count, 1), 6),
        "average_time_per_snapshot": round(total_duration / max(snapshots_total, 1), 6),
    }

    persistence = {"storage": "dry_run", "run_id": None, "stored_result_count": 0}
    if persist and not dry_run:
        persist_started = time.perf_counter()
        persistence = persist_strategy_laboratory_run(
            database_url=database_url,
            run_result=run,
            horizon=horizon,
            benchmark=benchmark,
            comparison_mode=comparison_mode,
            start_date=start_date,
            end_date=end_date,
            duration_seconds=total_duration,
            performance=performance,
        )
        performance["persistence_time"] = round(time.perf_counter() - persist_started, 6)

    dashboard_payload = {}
    if persist and not dry_run:
        payload_started = time.perf_counter()
        dashboard_payload = fetch_strategy_lab_dashboard_payload(database_url)
        performance["dashboard_payload_time"] = round(time.perf_counter() - payload_started, 6)

    result = {
        "run_id": f"strategy-lab-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}",
        "summary": run.get("summary") or {},
        "leaderboard": run.get("leaderboard") or [],
        "strategy_results": run.get("strategy_results") or [],
        "pairwise": run.get("pairwise") or [],
        "cost_configuration": run.get("cost_configuration") or {},
        "normalization_warnings": run.get("normalization_warnings") or {},
        "performance": performance,
        "persistence": persistence,
        "dashboard_payload": dashboard_payload,
    }

    _log(
        "STRATEGY_LAB_COMPLETED",
        strategies=strategy_count,
        horizon=horizon,
        benchmark=benchmark,
        mode=comparison_mode,
        duration=total_duration,
        storage=persistence.get("storage"),
    )

    if output_format == "json":
        return result
    return {"text": json.dumps(result, indent=2, sort_keys=True)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run research-only strategy comparison laboratory")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--strategy-ids", default=None, help="Comma-separated strategy IDs")
    parser.add_argument("--horizon", type=int, default=STRATEGY_LAB_DEFAULT_HORIZON)
    parser.add_argument("--benchmark", default=BENCHMARK_SYMBOL)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--comparison-mode", default=STRATEGY_LAB_DEFAULT_COMPARISON_MODE, choices=["common_snapshots", "all_available"])
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--portfolio-weighting-method", default="equal_weight")
    parser.add_argument("--maximum-position-weight", type=float, default=0.30)
    parser.add_argument("--sector-cap", type=float, default=0.50)
    parser.add_argument("--minimum-holdings", type=int, default=STRATEGY_LAB_MIN_HOLDINGS)
    parser.add_argument("--commission", type=float, default=STRATEGY_LAB_COMMISSION_PER_TRADE)
    parser.add_argument("--fixed-rebalance-cost", type=float, default=STRATEGY_LAB_FIXED_REBALANCE_COST)
    parser.add_argument("--turnover-cost-bps", type=float, default=STRATEGY_LAB_TURNOVER_COST_BPS)
    parser.add_argument("--slippage-bps", type=float, default=STRATEGY_LAB_SLIPPAGE_BPS)
    parser.add_argument("--walk-forward-enabled", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--output-format", default="json", choices=["json", "text"])
    args = parser.parse_args()

    strategy_ids = [part.strip() for part in str(args.strategy_ids or "").split(",") if part.strip()] or None
    result = run_strategy_lab(
        database_url=args.database_url,
        strategy_ids=strategy_ids,
        horizon=args.horizon,
        benchmark=args.benchmark,
        start_date=args.start_date,
        end_date=args.end_date,
        comparison_mode=args.comparison_mode,
        top_n=args.top_n,
        portfolio_weighting_method=args.portfolio_weighting_method,
        maximum_position_weight=args.maximum_position_weight,
        sector_cap=args.sector_cap,
        minimum_holdings=args.minimum_holdings,
        commission=args.commission,
        fixed_rebalance_cost=args.fixed_rebalance_cost,
        turnover_cost_bps=args.turnover_cost_bps,
        slippage_bps=args.slippage_bps,
        walk_forward_enabled=args.walk_forward_enabled,
        dry_run=args.dry_run,
        persist=args.persist,
        output_format=args.output_format,
    )
    print(result)


if __name__ == "__main__":
    main()
