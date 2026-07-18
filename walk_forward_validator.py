from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from config import (
    BENCHMARK_SYMBOL,
    WALK_FORWARD_DEFAULT_HORIZON,
    WALK_FORWARD_MIN_TRAINING_SAMPLE,
    WALK_FORWARD_MIN_VALIDATION_SAMPLE,
    WALK_FORWARD_STEP_PERIODS,
    WALK_FORWARD_TRAINING_PERIODS,
    WALK_FORWARD_VALIDATION_PERIODS,
    WALK_FORWARD_WINDOW_TYPE,
)
from evaluation_repository import MonitoringEvaluationRepository
from logger_setup import logger
from stability_analyzer import (
    aggregate_factor_stability,
    analyze_factor_stability,
    analyze_performance_decay,
    analyze_regime_robustness,
    build_validation_scorecard,
)
from walk_forward_data import (
    compare_training_validation_metrics,
    fetch_walk_forward_dashboard_payload,
    generate_walk_forward_windows,
    build_window_analysis,
    normalize_walk_forward_rows,
)
from walk_forward_repository import MonitoringWalkForwardRepository, WalkForwardRunPayload


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(event: str, **fields: Any) -> None:
    parts = [event]
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{key}={value}")
    logger.info(" ".join(parts))


@dataclass
class WalkForwardValidator:
    database_url: str | None = None

    def validate(
        self,
        horizon: int = WALK_FORWARD_DEFAULT_HORIZON,
        start_date: str | None = None,
        end_date: str | None = None,
        window_type: str = WALK_FORWARD_WINDOW_TYPE,
        training_periods: int = WALK_FORWARD_TRAINING_PERIODS,
        validation_periods: int = WALK_FORWARD_VALIDATION_PERIODS,
        step_periods: int = WALK_FORWARD_STEP_PERIODS,
        min_training_sample: int = WALK_FORWARD_MIN_TRAINING_SAMPLE,
        min_validation_sample: int = WALK_FORWARD_MIN_VALIDATION_SAMPLE,
        benchmark_symbol: str = BENCHMARK_SYMBOL,
        dry_run: bool = False,
        persist: bool = False,
        research_run_id: str | None = None,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        load_started = time.perf_counter()
        evaluation_repo = MonitoringEvaluationRepository(database_url=self.database_url)
        walk_repo = MonitoringWalkForwardRepository(database_url=self.database_url)
        try:
            evaluation_repo.db.ensure_schema()
            rows = evaluation_repo.fetch_evaluation_rows_for_dashboard(limit=5000)
            normalized_rows = normalize_walk_forward_rows(rows, horizon=horizon, start_date=start_date, end_date=end_date, symbol_filter=symbol, research_run_id=research_run_id)
            load_seconds = round(time.perf_counter() - load_started, 4)

            generation_started = time.perf_counter()
            windows = generate_walk_forward_windows(
                normalized_rows,
                horizon=horizon,
                benchmark_symbol=benchmark_symbol,
                window_type=window_type,
                training_periods=training_periods,
                validation_periods=validation_periods,
                step_periods=step_periods,
                min_training_sample=min_training_sample,
                min_validation_sample=min_validation_sample,
            )
            generation_seconds = round(time.perf_counter() - generation_started, 4)

            per_window_started = time.perf_counter()
            completed_windows = 0
            skipped_windows = 0
            factor_stability_rows: list[list[dict[str, Any]]] = []
            processed_windows: list[dict[str, Any]] = []
            for window in windows:
                if str(window.get("status") or "") == "skipped":
                    skipped_windows += 1
                    processed_windows.append(window)
                    continue
                training_metrics = build_window_analysis(window.get("training_rows") or [])
                validation_metrics = build_window_analysis(window.get("validation_rows") or [])
                degradation_metrics = compare_training_validation_metrics(training_metrics, validation_metrics)
                factor_stability = analyze_factor_stability(window.get("training_rows") or [], window.get("validation_rows") or [])
                regime_metrics = analyze_regime_robustness([
                    {
                        "validation_metrics": validation_metrics,
                        "warnings": window.get("warnings") or [],
                        "status": "completed",
                    }
                ])
                factor_stability_rows.append(factor_stability)
                completed_windows += 1
                processed = dict(window)
                processed["training_metrics"] = training_metrics
                processed["validation_metrics"] = validation_metrics
                processed["degradation_metrics"] = degradation_metrics
                processed["factor_stability"] = factor_stability
                processed["regime_metrics"] = regime_metrics
                processed["created_at"] = _utc_iso()
                processed_windows.append(processed)
            per_window_seconds = round(time.perf_counter() - per_window_started, 4)

            factor_started = time.perf_counter()
            factor_stability_summary = aggregate_factor_stability(factor_stability_rows)
            factor_seconds = round(time.perf_counter() - factor_started, 4)

            regime_started = time.perf_counter()
            regime_robustness = analyze_regime_robustness(processed_windows)
            regime_seconds = round(time.perf_counter() - regime_started, 4)

            decay_metrics = analyze_performance_decay(processed_windows)
            scorecard = build_validation_scorecard(processed_windows, factor_stability_summary, decay_metrics, regime_robustness)
            total_seconds = round(time.perf_counter() - started, 4)

            run_id = f"walk-forward-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
            run_payload = {
                "run_id": run_id,
                "created_at": _utc_iso(),
                "window_type": str(window_type),
                "training_periods": int(training_periods),
                "validation_periods": int(validation_periods),
                "step_periods": int(step_periods),
                "horizon": int(horizon),
                "benchmark_symbol": str(benchmark_symbol),
                "configuration_snapshot": {
                    "start_date": start_date,
                    "end_date": end_date,
                    "window_type": window_type,
                    "training_periods": training_periods,
                    "validation_periods": validation_periods,
                    "step_periods": step_periods,
                    "min_training_sample": min_training_sample,
                    "min_validation_sample": min_validation_sample,
                    "benchmark_symbol": benchmark_symbol,
                    "research_run_id": research_run_id,
                    "symbol": symbol,
                },
                "total_windows": len(processed_windows),
                "completed_windows": completed_windows,
                "skipped_windows": skipped_windows,
                "scorecard": scorecard,
                "factor_stability_summary": factor_stability_summary,
                "performance_decay": decay_metrics,
                "regime_robustness": regime_robustness,
                "performance": {
                    "rows_loaded": len(rows),
                    "eligible_rows": len(normalized_rows),
                    "windows_generated": len(processed_windows),
                    "window_generation_time": generation_seconds,
                    "per_window_calculation_time": per_window_seconds,
                    "factor_stability_calculation_time": factor_seconds,
                    "regime_analysis_time": regime_seconds,
                    "database_write_time": 0.0,
                    "dashboard_payload_time": 0.0,
                    "total_duration": total_seconds,
                },
                "status": "completed",
                "duration_seconds": total_seconds,
                "error_message": None,
            }

            db_write_started = time.perf_counter()
            persistence_result = {"storage": "dry_run", "run_id": run_id, "stored_window_count": 0}
            if persist and not dry_run and walk_repo.db.enabled:
                persistence_result = walk_repo.save_run(WalkForwardRunPayload(run=run_payload, windows=processed_windows))
            db_write_seconds = round(time.perf_counter() - db_write_started, 4)
            run_payload["performance"]["database_write_time"] = db_write_seconds

            _log(
                "WALK_FORWARD_VALIDATION_COMPLETED",
                run_id=run_id,
                rows_loaded=len(rows),
                eligible_rows=len(normalized_rows),
                total_windows=len(processed_windows),
                completed_windows=completed_windows,
                skipped_windows=skipped_windows,
                load_seconds=load_seconds,
                generation_seconds=generation_seconds,
                per_window_seconds=per_window_seconds,
                factor_seconds=factor_seconds,
                regime_seconds=regime_seconds,
                db_write_seconds=db_write_seconds,
                total_seconds=total_seconds,
            )
            return {
                "run": run_payload,
                "windows": processed_windows,
                "factor_stability_summary": factor_stability_summary,
                "performance_decay": decay_metrics,
                "regime_robustness": regime_robustness,
                "scorecard": scorecard,
                "performance": {
                    "rows_loaded": len(rows),
                    "eligible_rows": len(normalized_rows),
                    "windows_generated": len(processed_windows),
                    "window_generation_time": generation_seconds,
                    "per_window_calculation_time": per_window_seconds,
                    "factor_stability_calculation_time": factor_seconds,
                    "regime_analysis_time": regime_seconds,
                    "database_write_time": db_write_seconds,
                    "dashboard_payload_time": 0.0,
                    "total_duration": total_seconds,
                },
                "persistence": persistence_result,
            }
        finally:
            evaluation_repo.close()
            walk_repo.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run research-only walk-forward validation over stored evaluation rows")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--horizon", type=int, default=WALK_FORWARD_DEFAULT_HORIZON)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--window-type", choices=["rolling", "expanding"], default=WALK_FORWARD_WINDOW_TYPE)
    parser.add_argument("--training-periods", type=int, default=WALK_FORWARD_TRAINING_PERIODS)
    parser.add_argument("--validation-periods", type=int, default=WALK_FORWARD_VALIDATION_PERIODS)
    parser.add_argument("--step-periods", type=int, default=WALK_FORWARD_STEP_PERIODS)
    parser.add_argument("--min-training-sample", type=int, default=WALK_FORWARD_MIN_TRAINING_SAMPLE)
    parser.add_argument("--min-validation-sample", type=int, default=WALK_FORWARD_MIN_VALIDATION_SAMPLE)
    parser.add_argument("--benchmark", default=BENCHMARK_SYMBOL)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--research-run-id", default=None)
    parser.add_argument("--symbol", default=None)
    args = parser.parse_args()
    result = WalkForwardValidator(database_url=args.database_url).validate(
        horizon=args.horizon,
        start_date=args.start_date,
        end_date=args.end_date,
        window_type=args.window_type,
        training_periods=args.training_periods,
        validation_periods=args.validation_periods,
        step_periods=args.step_periods,
        min_training_sample=args.min_training_sample,
        min_validation_sample=args.min_validation_sample,
        benchmark_symbol=args.benchmark,
        dry_run=args.dry_run,
        persist=args.persist,
        research_run_id=args.research_run_id,
        symbol=args.symbol,
    )
    print(result)


if __name__ == "__main__":
    main()