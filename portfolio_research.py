from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone
from typing import Any

from config import (
    BENCHMARK_SYMBOL,
    PORTFOLIO_RESEARCH_DEFAULT_HORIZON,
    PORTFOLIO_RESEARCH_DEFAULT_METHOD,
    PORTFOLIO_RESEARCH_DEFAULT_TOP_N,
)
from logger_setup import logger
from portfolio_research_data import execute_portfolio_research
from portfolio_research_repository import MonitoringPortfolioResearchRepository, PortfolioResearchRunPayload


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(event: str, **fields: Any) -> None:
    parts = [event]
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{key}={value}")
    logger.info(" ".join(parts))


def run_portfolio_research(
    database_url: str | None = None,
    horizon: int = PORTFOLIO_RESEARCH_DEFAULT_HORIZON,
    weighting_method: str = PORTFOLIO_RESEARCH_DEFAULT_METHOD,
    top_n: int = PORTFOLIO_RESEARCH_DEFAULT_TOP_N,
    start_date: str | None = None,
    end_date: str | None = None,
    benchmark: str = BENCHMARK_SYMBOL,
    maximum_position_weight: float | None = None,
    sector_cap: float | None = None,
    minimum_holdings: int | None = None,
    maximum_holdings: int | None = None,
    target_volatility: float | None = None,
    signal_filter: str | None = None,
    regime_filter: str | None = None,
    sector_filter: str | None = None,
    research_run_filter: str | None = None,
    symbol_filter: str | None = None,
    dry_run: bool = False,
    persist: bool = False,
    methods: list[str] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    result = execute_portfolio_research(
        database_url=database_url,
        horizon=horizon,
        method=weighting_method,
        top_n=top_n,
        start_date=start_date,
        end_date=end_date,
        benchmark_symbol=benchmark,
        research_run_id=research_run_filter,
        symbol_filter=symbol_filter,
        sector_filter=sector_filter,
        regime_filter=regime_filter,
        signal_filter=signal_filter,
        methods=methods,
    )
    duration = round(time.perf_counter() - started, 4)

    primary = result.get("primary_result") or {}
    run_id = f"portfolio-research-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    run_payload = {
        "run_id": run_id,
        "created_at": _utc_iso(),
        "horizon": int(horizon),
        "weighting_method": weighting_method,
        "top_n": int(top_n),
        "maximum_position_weight": maximum_position_weight,
        "sector_cap": sector_cap,
        "target_volatility": target_volatility,
        "benchmark": benchmark,
        "start_date": start_date,
        "end_date": end_date,
        "configuration": {
            "minimum_holdings": minimum_holdings,
            "maximum_holdings": maximum_holdings,
            "signal_filter": signal_filter,
            "regime_filter": regime_filter,
            "sector_filter": sector_filter,
            "research_run_filter": research_run_filter,
            "symbol_filter": symbol_filter,
            "methods": methods or [weighting_method],
            "dry_run": bool(dry_run),
        },
        "portfolio_count": int((primary.get("analytics") or {}).get("portfolio_count") or 0),
        "completed_count": int((primary.get("analytics") or {}).get("completed_portfolio_count") or 0),
        "skipped_count": int((primary.get("analytics") or {}).get("skipped_portfolio_count") or 0),
        "status": "completed",
        "duration_seconds": duration,
        "error_message": None,
        "performance": {
            "evaluation_rows_loaded": result.get("eligible_row_count", 0),
            "formation_snapshots_generated": int(primary.get("portfolio_count") or 0),
            "weighting_calculation_time": None,
            "constraint_application_time": None,
            "portfolio_return_calculation_time": None,
            "analytics_time": None,
            "walk_forward_integration_time": None,
            "database_write_time": None,
            "dashboard_payload_time": None,
            "total_duration": duration,
            "average_time_per_snapshot": None,
        },
        "analytics": primary.get("analytics") or {},
        "method_comparison": (result.get("comparison") or {}).get("comparison_table") or [],
        "walk_forward": result.get("walk_forward") or {},
        "warnings": list(primary.get("warnings") or []),
    }

    snapshots = list(primary.get("snapshots") or [])

    persistence = {"storage": "dry_run", "run_id": run_id, "stored_snapshot_count": 0}
    if persist and not dry_run:
        repo = MonitoringPortfolioResearchRepository(database_url=database_url)
        try:
            persistence = repo.save_run(PortfolioResearchRunPayload(run=run_payload, snapshots=snapshots))
        finally:
            repo.close()

    _log(
        "PORTFOLIO_RESEARCH_COMPLETED",
        run_id=run_id,
        horizon=horizon,
        method=weighting_method,
        snapshots=len(snapshots),
        eligible_rows=result.get("eligible_row_count", 0),
        duration=duration,
        storage=persistence.get("storage"),
    )

    return {
        "run": run_payload,
        "snapshots": snapshots,
        "comparison": result.get("comparison") or {},
        "walk_forward": result.get("walk_forward") or {},
        "normalization_warnings": result.get("normalization_warnings") or {},
        "performance": run_payload["performance"],
        "persistence": persistence,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run research-only portfolio construction analysis over historical evaluation rows")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--horizon", type=int, default=PORTFOLIO_RESEARCH_DEFAULT_HORIZON)
    parser.add_argument("--weighting-method", default=PORTFOLIO_RESEARCH_DEFAULT_METHOD)
    parser.add_argument("--top-n", type=int, default=PORTFOLIO_RESEARCH_DEFAULT_TOP_N)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--benchmark", default=BENCHMARK_SYMBOL)
    parser.add_argument("--maximum-position-weight", type=float, default=None)
    parser.add_argument("--sector-cap", type=float, default=None)
    parser.add_argument("--minimum-holdings", type=int, default=None)
    parser.add_argument("--maximum-holdings", type=int, default=None)
    parser.add_argument("--target-volatility", type=float, default=None)
    parser.add_argument("--signal-filter", default=None)
    parser.add_argument("--regime-filter", default=None)
    parser.add_argument("--sector-filter", default=None)
    parser.add_argument("--research-run-filter", default=None)
    parser.add_argument("--symbol-filter", default=None)
    parser.add_argument("--methods", default=None, help="Comma separated method list for comparison")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--persist", action="store_true")

    args = parser.parse_args()
    methods = [part.strip() for part in str(args.methods or "").split(",") if part.strip()] or None
    result = run_portfolio_research(
        database_url=args.database_url,
        horizon=args.horizon,
        weighting_method=args.weighting_method,
        top_n=args.top_n,
        start_date=args.start_date,
        end_date=args.end_date,
        benchmark=args.benchmark,
        maximum_position_weight=args.maximum_position_weight,
        sector_cap=args.sector_cap,
        minimum_holdings=args.minimum_holdings,
        maximum_holdings=args.maximum_holdings,
        target_volatility=args.target_volatility,
        signal_filter=args.signal_filter,
        regime_filter=args.regime_filter,
        sector_filter=args.sector_filter,
        research_run_filter=args.research_run_filter,
        symbol_filter=args.symbol_filter,
        dry_run=args.dry_run,
        persist=args.persist,
        methods=methods,
    )
    print(result)


if __name__ == "__main__":
    main()
