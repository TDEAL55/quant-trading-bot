from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from config import FORWARD_RETURN_HORIZONS, TRADING_MODE, is_safe_mode
from evaluation_repository import MonitoringEvaluationRepository
from factor_bucket_analysis import compute_bucket_statistics
from factor_intelligence_data_quality import evaluate_alignment_quality
from factor_intelligence_repository import FactorIntelligenceRepository, FactorIntelligenceRunPayload
from factor_intelligence_scorecard import SCORE_FORMULA, build_scorecards
from factor_intelligence_utils import short_hash, stable_json, utc_iso
from factor_observation_model import build_factor_observations
from factor_predictive_power import compute_predictive_power
from factor_redundancy import compute_factor_redundancy
from factor_regime_analysis import compute_regime_statistics
from factor_registry import FactorDefinition, build_default_registry
from factor_stability import compute_stability
from security_factor_explainability import build_security_explanation


@dataclass(frozen=True)
class FactorIntelligenceConfig:
    start_date: str | None
    end_date: str | None
    forward_horizon: int
    factor_ids: list[str]
    factor_versions: dict[str, str]
    minimum_sample_size: int
    bucket_count: int
    regime_filter: str | None
    universe_filter: str | None
    benchmark_mode: str
    force_recompute: bool = False


class FactorIntelligenceEngine:
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url
        self.eval_repo = MonitoringEvaluationRepository(database_url=database_url)
        self.repo = FactorIntelligenceRepository(database_url=database_url)

    def close(self) -> None:
        self.eval_repo.close()
        self.repo.close()

    def _validate_config(self, config: FactorIntelligenceConfig) -> None:
        if not is_safe_mode(TRADING_MODE):
            raise RuntimeError("Factor Intelligence is blocked in LIVE mode")
        if config.start_date and config.end_date and config.start_date > config.end_date:
            raise ValueError("start_date must be before or equal to end_date")
        if int(config.forward_horizon) not in set(FORWARD_RETURN_HORIZONS):
            raise ValueError(f"unsupported forward horizon: {config.forward_horizon}")
        if not config.factor_ids:
            raise ValueError("at least one factor_id must be selected")
        if config.minimum_sample_size < 2:
            raise ValueError("minimum_sample_size must be >= 2")
        if config.bucket_count < 2:
            raise ValueError("bucket_count must be >= 2")

    def _run_fingerprint(self, config: FactorIntelligenceConfig) -> str:
        payload = {
            "start_date": config.start_date,
            "end_date": config.end_date,
            "forward_horizon": config.forward_horizon,
            "factor_ids": sorted(config.factor_ids),
            "factor_versions": {k: config.factor_versions[k] for k in sorted(config.factor_versions)},
            "minimum_sample_size": config.minimum_sample_size,
            "bucket_count": config.bucket_count,
            "regime_filter": config.regime_filter,
            "universe_filter": config.universe_filter,
            "benchmark_mode": config.benchmark_mode,
        }
        return short_hash([stable_json(payload)], length=24)

    def _build_registry(self) -> list[FactorDefinition]:
        registry = build_default_registry()
        return registry.list_factors(active_only=True)

    def _select_factors(self, available: list[FactorDefinition], config: FactorIntelligenceConfig) -> list[FactorDefinition]:
        selected = []
        for factor in available:
            if factor.factor_id not in set(config.factor_ids):
                continue
            requested_version = config.factor_versions.get(factor.factor_id)
            if requested_version and requested_version != factor.version:
                continue
            selected.append(factor)
        selected_ids = sorted({f.factor_id for f in selected})
        missing = sorted(set(config.factor_ids) - set(selected_ids))
        if missing:
            raise ValueError(f"invalid factor_ids: {', '.join(missing)}")
        return selected

    def _in_date_range(self, row: dict[str, Any], start_date: str | None, end_date: str | None) -> bool:
        observation = str(row.get("observation_date") or "")
        if not observation:
            return False
        if start_date and observation < start_date:
            return False
        if end_date and observation > end_date:
            return False
        return True

    def _align_rows(
        self,
        observations: list[dict[str, Any]],
        source_rows: list[dict[str, Any]],
        config: FactorIntelligenceConfig,
    ) -> list[dict[str, Any]]:
        source_by_candidate = {int(row.get("research_candidate_id") or 0): row for row in source_rows}
        aligned: list[dict[str, Any]] = []
        for obs in observations:
            candidate_id = int(obs.get("candidate_id") or 0)
            src = source_by_candidate.get(candidate_id)
            if not src:
                continue
            if not self._in_date_range(src, config.start_date, config.end_date):
                continue
            if str(src.get(f"forward_{config.forward_horizon}d_status") or "") != "complete":
                continue
            if config.regime_filter and str(src.get("market_regime") or "unknown") != str(config.regime_filter):
                continue
            if config.universe_filter and str(src.get("sector") or "unknown") != str(config.universe_filter):
                continue
            merged = dict(obs)
            merged.update(src)
            aligned.append(merged)
        aligned.sort(key=lambda row: (str(row.get("factor_id")), str(row.get("factor_version")), str(row.get("observation_date")), str(row.get("symbol"))))
        return aligned

    def run(self, config: FactorIntelligenceConfig) -> dict[str, Any]:
        self._validate_config(config)
        self.eval_repo.db.ensure_schema()
        self.repo.db.ensure_schema()

        run_started = time.perf_counter()
        run_id = f"factor-intel-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        attempt_id = short_hash([run_id, utc_iso(), "attempt"], length=16)
        fingerprint = self._run_fingerprint(config)

        if not config.force_recompute:
            existing = self.repo.fetch_run_by_fingerprint(fingerprint)
            if existing:
                return {
                    "status": "completed",
                    "run_id": existing.get("run_id"),
                    "run_fingerprint": fingerprint,
                    "reused": True,
                    "message": "existing completed run reused",
                }

        run_row = {
            "run_id": run_id,
            "run_fingerprint": fingerprint,
            "attempt_id": attempt_id,
            "started_at": utc_iso(),
            "status": "running",
            "analysis_start_date": config.start_date,
            "analysis_end_date": config.end_date,
            "forward_horizon": config.forward_horizon,
            "universe_filter": config.universe_filter,
            "regime_filter": config.regime_filter,
            "factor_version_set": stable_json(config.factor_versions),
            "sample_count": 0,
            "configuration": config.__dict__,
            "timings": {},
            "error_message": None,
            "created_at": utc_iso(),
            "updated_at": utc_iso(),
        }
        self.repo.create_run(run_row)

        timings: dict[str, float] = {}
        try:
            t0 = time.perf_counter()
            available = self._build_registry()
            factors = self._select_factors(available, config)
            self.repo.register_factors(
                [
                    {
                        "factor_id": f.factor_id,
                        "name": f.name,
                        "description": f.description,
                        "category": f.category,
                        "version": f.version,
                        "direction": f.direction,
                        "calculation_source": f.calculation_source,
                        "lookback_period": f.lookback_period,
                        "minimum_history_required": f.minimum_history_required,
                        "expected_value_type": f.expected_value_type,
                        "active": f.active,
                        "created_at": f.created_at,
                        "metadata": f.metadata,
                    }
                    for f in factors
                ]
            )
            timings["factor_definition_loading_seconds"] = round(time.perf_counter() - t0, 6)

            t0 = time.perf_counter()
            source_rows = self.eval_repo.fetch_evaluation_rows_for_dashboard(limit=50000)
            observations, observation_quality = build_factor_observations(source_rows, factors)
            self.repo.upsert_observations(observations)
            timings["observation_loading_seconds"] = round(time.perf_counter() - t0, 6)

            t0 = time.perf_counter()
            aligned = self._align_rows(observations, source_rows, config)
            timings["forward_label_alignment_seconds"] = round(time.perf_counter() - t0, 6)

            supported_versions = {(factor.factor_id, factor.version) for factor in factors}
            quality_summary = evaluate_alignment_quality(
                aligned_rows=aligned,
                forward_horizon=config.forward_horizon,
                supported_versions=supported_versions,
                minimum_universe_size=2,
            )

            t0 = time.perf_counter()
            predictive = compute_predictive_power(
                aligned_rows=aligned,
                forward_horizon=config.forward_horizon,
                minimum_sample_size=config.minimum_sample_size,
                analysis_start_date=config.start_date,
                analysis_end_date=config.end_date,
            )
            timings["predictive_analysis_seconds"] = round(time.perf_counter() - t0, 6)

            t0 = time.perf_counter()
            buckets = compute_bucket_statistics(
                aligned_rows=aligned,
                forward_horizon=config.forward_horizon,
                requested_bucket_count=config.bucket_count,
                minimum_sample_size=config.minimum_sample_size,
            )
            timings["bucket_analysis_seconds"] = round(time.perf_counter() - t0, 6)

            t0 = time.perf_counter()
            stability = compute_stability(
                evaluation_rows=source_rows,
                factors=factors,
                forward_horizon=config.forward_horizon,
                minimum_sample_size=config.minimum_sample_size,
            )
            timings["stability_analysis_seconds"] = round(time.perf_counter() - t0, 6)

            t0 = time.perf_counter()
            direction_map = {factor.factor_id: factor.direction for factor in factors}
            regime = compute_regime_statistics(
                aligned_rows=aligned,
                direction_map=direction_map,
                forward_horizon=config.forward_horizon,
                minimum_sample_size=config.minimum_sample_size,
            )
            timings["regime_analysis_seconds"] = round(time.perf_counter() - t0, 6)

            t0 = time.perf_counter()
            redundancy = compute_factor_redundancy(
                aligned_rows=aligned,
                minimum_sample_size=config.minimum_sample_size,
            )
            timings["redundancy_analysis_seconds"] = round(time.perf_counter() - t0, 6)

            t0 = time.perf_counter()
            scorecards = build_scorecards(
                predictive_stats=predictive,
                stability_results=stability,
                regime_stats=regime,
                redundancy_stats=redundancy,
                analysis_start_date=config.start_date,
                analysis_end_date=config.end_date,
            )
            timings["scorecard_creation_seconds"] = round(time.perf_counter() - t0, 6)

            t0 = time.perf_counter()
            persisted = self.repo.save_results(
                FactorIntelligenceRunPayload(
                    run=run_row,
                    predictive_stats=predictive,
                    bucket_stats=buckets,
                    stability_results=stability,
                    regime_stats=regime,
                    redundancy_stats=redundancy,
                    scorecards=scorecards,
                )
            )
            timings["persistence_seconds"] = round(time.perf_counter() - t0, 6)

            t0 = time.perf_counter()
            dashboard_payload = self.repo.dashboard_summary(run_id)
            timings["dashboard_payload_creation_seconds"] = round(time.perf_counter() - t0, 6)

            total_measured = round(sum(timings.values()), 6)
            wall_clock = round(time.perf_counter() - run_started, 6)
            unmeasured = round(max(0.0, wall_clock - total_measured), 6)
            timings["total_measured_component_time_seconds"] = total_measured
            timings["total_wall_clock_duration_seconds"] = wall_clock
            timings["unmeasured_overhead_seconds"] = unmeasured

            observation_count = len(observations)
            factor_count = len(factors)
            pair_count = len(redundancy)
            timings["observations_per_second"] = round(observation_count / max(wall_clock, 1e-9), 6)
            timings["factor_pairs_processed"] = pair_count
            timings["average_time_per_factor_seconds"] = round(total_measured / max(factor_count, 1), 6)

            final_status = "completed"
            if not predictive or all(row.get("status") == "insufficient_data" for row in predictive):
                final_status = "insufficient_data"

            self.repo.update_run_status(
                run_id=run_id,
                status=final_status,
                completed_at=utc_iso(),
                sample_count=quality_summary["valid_rows"],
                timings=timings,
            )

            return {
                "status": final_status,
                "run_id": run_id,
                "run_fingerprint": fingerprint,
                "attempt_id": attempt_id,
                "factor_count": len(factors),
                "total_observation_count": len(observations),
                "valid_observation_count": quality_summary["valid_rows"],
                "excluded_observation_count": quality_summary["excluded_rows"],
                "data_quality_summary": quality_summary,
                "persistence": persisted,
                "dashboard_payload": dashboard_payload,
                "timings": timings,
                "score_formula": SCORE_FORMULA,
            }

        except Exception as exc:
            self.repo.update_run_status(
                run_id=run_id,
                status="failed",
                completed_at=utc_iso(),
                error_message=f"{type(exc).__name__}: {exc}",
                timings=timings,
            )
            raise

    def latest(self) -> dict[str, Any]:
        run = self.repo.latest_completed_run()
        if not run:
            return {"status": "empty", "run": {}}
        return {"status": "completed", "run": run}

    def leaderboard(self, run_id: str | None = None, limit: int = 50) -> dict[str, Any]:
        selected_run = run_id
        if not selected_run:
            latest = self.repo.latest_completed_run()
            if not latest:
                return {"status": "empty", "leaderboard": []}
            selected_run = str(latest.get("run_id"))
        return {
            "status": "completed",
            "run_id": selected_run,
            "leaderboard": self.repo.factor_leaderboard(selected_run, limit=limit),
        }

    def factor_details(self, factor_id: str, factor_version: str = "v1", limit: int = 100) -> dict[str, Any]:
        return {
            "status": "completed",
            "factor_id": factor_id,
            "factor_version": factor_version,
            "history": self.repo.factor_history(factor_id=factor_id, factor_version=factor_version, limit=limit),
        }

    def explain(self, symbol: str, snapshot_id: str, factor_weights: dict[str, float] | None = None) -> dict[str, Any]:
        latest = self.repo.latest_completed_run()
        if not latest:
            return {"status": "empty", "explanation": {}}
        factors = self.repo.get_factor_definitions(active_only=True)
        versions = {row["factor_id"]: row["version"] for row in factors}
        rows = self.repo.security_explanation_rows(snapshot_id=snapshot_id, symbol=symbol, factor_versions=versions)
        if factor_weights is None:
            equal = 1.0 / max(len(versions), 1)
            factor_weights = {factor_id: equal for factor_id in versions}
        explanation = build_security_explanation(
            symbol=symbol,
            snapshot_id=snapshot_id,
            factor_rows=rows,
            factor_weights=factor_weights,
            universe_size=max([int(row.get("universe_size") or 0) for row in rows], default=0),
            final_rank=None,
        )
        return {"status": "completed", "run_id": latest.get("run_id"), "explanation": explanation}

    def export_run(self, run_id: str) -> dict[str, Any]:
        payload = self.repo.dashboard_summary(run_id)
        payload["configuration"] = (self.repo.db.query_one("SELECT configuration_json FROM factor_intelligence_runs WHERE run_id = ?", (run_id,)) or {}).get("configuration_json")
        payload["exported_at"] = utc_iso()
        payload["note"] = "Historical research analytics only. No automatic weight promotion."
        return payload


def _engine(database_url: str | None) -> FactorIntelligenceEngine:
    return FactorIntelligenceEngine(database_url=database_url)


def _build_config(args: argparse.Namespace) -> FactorIntelligenceConfig:
    factor_ids = list(args.factor_id or [])
    if not factor_ids:
        factor_ids = [
            "overall_score",
            "confidence",
            "trend_score",
            "momentum_score",
            "volume_score",
            "volatility_score",
            "liquidity_score",
            "market_regime_score",
            "risk_quality_score",
            "rank",
        ]
    versions = {factor_id: "v1" for factor_id in factor_ids}
    return FactorIntelligenceConfig(
        start_date=args.start_date,
        end_date=args.end_date,
        forward_horizon=int(args.forward_horizon),
        factor_ids=factor_ids,
        factor_versions=versions,
        minimum_sample_size=int(args.minimum_sample_size),
        bucket_count=int(args.bucket_count),
        regime_filter=args.regime,
        universe_filter=args.universe,
        benchmark_mode=args.benchmark_mode,
        force_recompute=bool(args.force_recompute),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Research-only factor intelligence engine")
    parser.add_argument("command", choices=["run", "latest", "leaderboard", "factor", "explain", "export"], help="command")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--forward-horizon", type=int, default=20)
    parser.add_argument("--factor-id", action="append", default=[])
    parser.add_argument("--minimum-sample-size", type=int, default=30)
    parser.add_argument("--bucket-count", type=int, default=10)
    parser.add_argument("--regime", default=None)
    parser.add_argument("--universe", default=None)
    parser.add_argument("--benchmark-mode", default="excess")
    parser.add_argument("--force-recompute", action="store_true")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--snapshot-id", default=None)
    parser.add_argument("--output", default="factor_intelligence.json")
    args = parser.parse_args()

    engine = _engine(args.database_url)
    try:
        if args.command == "run":
            result = engine.run(_build_config(args))
            print(json.dumps(result, sort_keys=True, indent=2))
            return
        if args.command == "latest":
            print(json.dumps(engine.latest(), sort_keys=True, indent=2))
            return
        if args.command == "leaderboard":
            print(json.dumps(engine.leaderboard(run_id=args.run_id), sort_keys=True, indent=2))
            return
        if args.command == "factor":
            if not args.factor_id:
                raise ValueError("--factor-id is required")
            print(json.dumps(engine.factor_details(factor_id=args.factor_id[0]), sort_keys=True, indent=2))
            return
        if args.command == "explain":
            if not args.symbol or not args.snapshot_id:
                raise ValueError("--symbol and --snapshot-id are required")
            print(json.dumps(engine.explain(symbol=args.symbol, snapshot_id=args.snapshot_id), sort_keys=True, indent=2))
            return
        if args.command == "export":
            if not args.run_id:
                raise ValueError("--run-id is required")
            payload = engine.export_run(args.run_id)
            with open(args.output, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, sort_keys=True, indent=2) + "\n")
            print(json.dumps({"status": "completed", "run_id": args.run_id, "output": args.output}, sort_keys=True, indent=2))
            return
    finally:
        engine.close()


if __name__ == "__main__":
    main()
