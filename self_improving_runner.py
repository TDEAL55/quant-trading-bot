from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from paper_execution_repository import MonitoringPaperExecutionRepository
from self_improving_intelligence import (
    ALLOCATION_RECOMMENDATION_VERSION,
    build_daily_report,
    build_factor_effectiveness,
    build_strategy_leaderboard,
    build_strategy_regime_metrics,
    build_trade_memory_record,
    build_weekly_report,
    classify_market_regime,
    recommend_position_size,
    recommend_strategy_states,
    recommend_weight_changes,
    recommendation_policy_summary,
)
from self_improving_repository import SelfImprovingRepository


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_self_improving_intelligence(database_url: str | None) -> dict[str, Any]:
    execution_repo = MonitoringPaperExecutionRepository(database_url=database_url)
    intelligence_repo = SelfImprovingRepository(database_url=database_url)
    try:
        closed = execution_repo.list_closed_trades(limit=5000)
        runs = execution_repo.list_runs(limit=300)

        strategy_signal_index: dict[str, dict[str, Any]] = {}
        order_ids_by_trade: dict[str, dict[str, list[str]]] = {}

        for run in runs:
            run_id = str(run.get("run_id") or "")
            orders = execution_repo.fetch_orders_for_run(run_id)
            for order in orders:
                payload = dict(order.get("order_payload") or {})
                strategy = dict(payload.get("strategy") or {})
                symbol = str(order.get("symbol") or "").upper()
                key = f"{run_id}:{symbol}"
                strategy_signal_index[key] = strategy

        trade_memory_records: list[dict[str, Any]] = []
        for trade in closed:
            run_id = str(trade.get("run_id") or "")
            symbol = str(trade.get("symbol") or "").upper()
            strategy_signal = strategy_signal_index.get(f"{run_id}:{symbol}", {})
            record = build_trade_memory_record(
                trade,
                broker_order_ids=order_ids_by_trade.get(str(trade.get("trade_id") or ""), {}).get("broker", []),
                client_order_ids=order_ids_by_trade.get(str(trade.get("trade_id") or ""), {}).get("client", []),
                source_order_status="filled",
                quantum_score=dict(strategy_signal.get("quantum_score") or {}),
                strategy_signal=strategy_signal,
                benchmark_return_during_trade=0.0,
                sector=str(trade.get("sector") or "Unknown"),
                industry=str(trade.get("industry") or "Unknown"),
                execution_mode="ALPACA_PAPER" if not bool(trade.get("dry_run")) else "DRY_RUN",
            )
            if record:
                trade_memory_records.append(record)

        intelligence_repo.save_trade_memory_records(trade_memory_records)

        leaderboard = build_strategy_leaderboard(trade_memory_records, minimum_sample=30)
        lb_version_id = intelligence_repo.save_strategy_leaderboard_snapshot(
            leaderboard,
            sample_minimum=30,
            configuration={"version_id": f"lb:{_utc_iso()}", "leaderboard_version": "strategy_leaderboard_v2"},
        )

        regime = classify_market_regime(
            {
                "spy_vs_ema20_pct": 0.0,
                "spy_vs_ema50_pct": 0.0,
                "spy_vs_ema200_pct": 0.0,
                "ema20_slope_pct": 0.0,
                "ema50_slope_pct": 0.0,
                "ema200_slope_pct": 0.0,
                "benchmark_momentum_20d_pct": 0.0,
                "realized_volatility_pct": 18.0,
                "atr_pct": 2.0,
                "breadth_above_200_pct": 50.0,
                "sector_participation_pct": 50.0,
                "drawdown_from_high_pct": -5.0,
            }
        )
        regime["regime_calc_id"] = f"regime:{_utc_iso()}"
        regime_calc_id = intelligence_repo.save_regime_calculation(regime)

        strategy_regime = build_strategy_regime_metrics(trade_memory_records, min_samples=30)
        intelligence_repo.save_strategy_regime_metrics(regime_calc_id, strategy_regime)

        factor_rows = build_factor_effectiveness(trade_memory_records, min_samples=30)
        intelligence_repo.save_factor_effectiveness(factor_rows)

        current_weights: dict[str, float] = {}
        if trade_memory_records:
            current_weights = dict(trade_memory_records[0].get("factor_weights") or {})
        weight_recos = recommend_weight_changes(current_weights or {
            "trend_strength": 20.0,
            "relative_strength": 15.0,
            "momentum_quality": 15.0,
            "volume_confirmation": 10.0,
            "volatility_quality": 10.0,
            "liquidity_quality": 10.0,
            "risk_reward_quality": 10.0,
            "market_regime_alignment": 10.0,
        }, factor_rows)
        intelligence_repo.save_weight_change_recommendations(weight_recos)

        state_recos = recommend_strategy_states(leaderboard, strategy_regime, min_samples=30)
        intelligence_repo.save_strategy_state_recommendations(state_recos)

        alloc_recos: list[dict[str, Any]] = []
        for row in leaderboard:
            alloc_recos.append(
                recommend_position_size(
                    strategy_id=str(row.get("strategy_id") or "unknown"),
                    strategy_version=str(row.get("strategy_version") or "unknown"),
                    symbol="PORTFOLIO",
                    quantum_score=90.0,
                    strategy_score=85.0,
                    historical_expectancy=float(row.get("expectancy") or 0.0),
                    profit_factor=float(row.get("profit_factor") or 0.0),
                    drawdown=float(row.get("maximum_drawdown") or 0.0),
                    sample_size=int(row.get("completed_trade_count") or 0),
                    market_regime=str(regime.get("regime_id") or "unknown"),
                    portfolio_concentration=0.20,
                    recent_stability=0.75,
                    max_allowed_allocation=0.10,
                    hard_cap_allocation=0.10,
                    hard_cap_risk=0.02,
                    risk_policy_checks={
                        "daily_loss_limits": True,
                        "max_position_limits": True,
                        "duplicate_protection": True,
                        "open_order_checks": True,
                        "sector_concentration": True,
                        "strategy_pause_state": True,
                    },
                )
            )
        intelligence_repo.save_allocation_recommendations(alloc_recos)

        today = datetime.now(timezone.utc).date().isoformat()
        daily_report = build_daily_report(
            market_date=today,
            account_equity=0.0,
            daily_pnl=0.0,
            open_positions=[],
            closed_trades=trade_memory_records,
            top_quantum_candidates=[],
            rejected_candidates=[],
            strategy_leaderboard=leaderboard,
            current_market_regime=regime,
            strategy_regime_metrics=strategy_regime,
            factor_updates=factor_rows[:25],
            risk_limit_events=[],
            system_errors=[],
            dry_run_mode=True,
        )
        intelligence_repo.save_report_snapshot(daily_report)

        week_start = (datetime.now(timezone.utc).date() - timedelta(days=7)).isoformat()
        weekly_report = build_weekly_report(
            period_start=week_start,
            period_end=today,
            state_recommendations=state_recos,
            weight_recommendations=weight_recos,
            regime_performance=strategy_regime,
            score_bucket_performance=factor_rows,
            drawdown_analysis={"max_drawdown": max([float(row.get("maximum_drawdown") or 0.0) for row in leaderboard], default=0.0)},
            walk_forward_results={"required": True, "status": "not_run"},
            recommended_changes=["review strategy state recommendations", "review weight recommendations"],
            unresolved_data_quality_issues=[],
        )
        intelligence_repo.save_report_snapshot(weekly_report)

        intelligence_repo.save_model_version(
            {
                "model_name": "self_improving_intelligence",
                "model_version": ALLOCATION_RECOMMENDATION_VERSION,
                "configuration": recommendation_policy_summary(),
                "is_active": True,
                "review_only": True,
            }
        )

        return {
            "status": "completed",
            "trade_memory_saved": len(trade_memory_records),
            "leaderboard_rows": len(leaderboard),
            "leaderboard_version_id": lb_version_id,
            "regime_id": regime.get("regime_id"),
            "factor_rows": len(factor_rows),
            "weight_recommendations": len(weight_recos),
            "state_recommendations": len(state_recos),
            "allocation_recommendations": len(alloc_recos),
        }
    finally:
        intelligence_repo.close()
        execution_repo.close()
