from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from monitoring_db import MonitoringDatabase


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True, separators=(",", ":"))


def _json_load(value: Any, default: Any) -> Any:
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


class SelfImprovingRepository:
    def __init__(self, database_url: str | None = None):
        self.db = MonitoringDatabase(database_url=database_url)

    def _adapt_query(self, query: str) -> str:
        return self.db._adapt_query(query)

    def save_trade_memory_records(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        if not self.db.enabled:
            return {"storage": "disabled", "saved": 0}
        self.db.ensure_schema()
        conn = self.db.conn
        with conn:
            cursor = conn.cursor()
            try:
                for row in records:
                    cursor.execute(
                        self._adapt_query(
                            """
                            INSERT INTO trade_memory_records (
                                trade_memory_id, trade_id, run_id, broker_order_ids_json, client_order_ids_json,
                                symbol, strategy_id, strategy_version, quantum_score_version,
                                quantum_score_at_entry, strategy_specific_score, factor_values_json,
                                component_scores_json, factor_weights_json, entry_timestamp, exit_timestamp,
                                entry_price, exit_price, quantity, realized_gross_pnl, estimated_fees,
                                estimated_slippage, net_pnl, percentage_return, holding_duration_hours,
                                max_adverse_excursion, max_favorable_excursion, market_regime_entry,
                                market_regime_exit, benchmark_return_during_trade, sector, industry,
                                entry_reason, exit_reason, stop_level, target_level, confidence,
                                data_quality_status, execution_mode, completed_only, source_order_status, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(trade_memory_id) DO UPDATE SET
                                broker_order_ids_json = excluded.broker_order_ids_json,
                                client_order_ids_json = excluded.client_order_ids_json,
                                quantum_score_version = excluded.quantum_score_version,
                                quantum_score_at_entry = excluded.quantum_score_at_entry,
                                strategy_specific_score = excluded.strategy_specific_score,
                                factor_values_json = excluded.factor_values_json,
                                component_scores_json = excluded.component_scores_json,
                                factor_weights_json = excluded.factor_weights_json,
                                benchmark_return_during_trade = excluded.benchmark_return_during_trade,
                                sector = excluded.sector,
                                industry = excluded.industry,
                                entry_reason = excluded.entry_reason,
                                exit_reason = excluded.exit_reason,
                                stop_level = excluded.stop_level,
                                target_level = excluded.target_level,
                                confidence = excluded.confidence,
                                data_quality_status = excluded.data_quality_status,
                                source_order_status = excluded.source_order_status
                            """
                        ),
                        (
                            row.get("trade_memory_id"),
                            row.get("trade_id"),
                            row.get("run_id"),
                            _stable_json(row.get("broker_order_ids") or []),
                            _stable_json(row.get("client_order_ids") or []),
                            row.get("symbol"),
                            row.get("strategy_id"),
                            row.get("strategy_version"),
                            row.get("quantum_score_version"),
                            row.get("quantum_score_at_entry"),
                            row.get("strategy_specific_score"),
                            _stable_json(row.get("factor_values") or {}),
                            _stable_json(row.get("component_scores") or {}),
                            _stable_json(row.get("factor_weights") or {}),
                            row.get("entry_timestamp"),
                            row.get("exit_timestamp"),
                            row.get("entry_price"),
                            row.get("exit_price"),
                            row.get("quantity"),
                            row.get("realized_gross_pnl"),
                            row.get("estimated_fees"),
                            row.get("estimated_slippage"),
                            row.get("net_pnl"),
                            row.get("percentage_return"),
                            row.get("holding_duration_hours"),
                            row.get("max_adverse_excursion"),
                            row.get("max_favorable_excursion"),
                            row.get("market_regime_entry"),
                            row.get("market_regime_exit"),
                            row.get("benchmark_return_during_trade"),
                            row.get("sector"),
                            row.get("industry"),
                            row.get("entry_reason"),
                            row.get("exit_reason"),
                            row.get("stop_level"),
                            row.get("target_level"),
                            row.get("confidence"),
                            row.get("data_quality_status"),
                            row.get("execution_mode"),
                            1 if row.get("completed_only", True) else 0,
                            row.get("source_order_status"),
                            row.get("created_at") or _utc_iso(),
                        ),
                    )
            finally:
                cursor.close()
        return {"storage": "database", "saved": len(records)}

    def list_trade_memory_records(self, limit: int = 5000) -> list[dict[str, Any]]:
        if not self.db.enabled:
            return []
        self.db.ensure_schema()
        rows = self.db.query_all(
            "SELECT * FROM trade_memory_records ORDER BY exit_timestamp DESC LIMIT ?",
            (int(limit),),
        )
        result = []
        for row in rows:
            item = dict(row)
            item["broker_order_ids"] = _json_load(row.get("broker_order_ids_json"), [])
            item["client_order_ids"] = _json_load(row.get("client_order_ids_json"), [])
            item["factor_values"] = _json_load(row.get("factor_values_json"), {})
            item["component_scores"] = _json_load(row.get("component_scores_json"), {})
            item["factor_weights"] = _json_load(row.get("factor_weights_json"), {})
            item["completed_only"] = bool(int(row.get("completed_only") or 0))
            result.append(item)
        return result

    def save_strategy_leaderboard_snapshot(self, rows: list[dict[str, Any]], *, sample_minimum: int, configuration: dict[str, Any]) -> str:
        if not self.db.enabled:
            return ""
        self.db.ensure_schema()
        version_id = str(configuration.get("version_id") or f"leaderboard:{_utc_iso()}")
        captured_at = _utc_iso()
        self.db.execute(
            """
            INSERT OR REPLACE INTO strategy_leaderboard_versions (
                version_id, captured_at, leaderboard_version, sample_minimum,
                source_trade_count, configuration_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                captured_at,
                str(configuration.get("leaderboard_version") or "strategy_leaderboard_v2"),
                int(sample_minimum),
                int(sum(int(row.get("completed_trade_count") or 0) for row in rows)),
                _stable_json(configuration),
                captured_at,
            ),
        )

        conn = self.db.conn
        with conn:
            cursor = conn.cursor()
            try:
                cursor.execute(self._adapt_query("DELETE FROM strategy_leaderboard_records WHERE version_id = ?"), (version_id,))
                for row in rows:
                    cursor.execute(
                        self._adapt_query(
                            """
                            INSERT INTO strategy_leaderboard_records (
                                version_id, strategy_id, strategy_version, completed_trade_count,
                                win_rate, loss_rate, average_return, median_return,
                                gross_profit, gross_loss, net_profit, profit_factor,
                                expectancy, sharpe_ratio, sortino_ratio, maximum_drawdown,
                                average_winner, average_loser, payoff_ratio, average_holding_hours,
                                best_trade, worst_trade, consecutive_wins, consecutive_losses,
                                recent20_json, recent60_json, full_history_json,
                                by_regime_json, by_sector_json, by_score_bucket_json,
                                sample_status, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """
                        ),
                        (
                            version_id,
                            row.get("strategy_id"),
                            row.get("strategy_version"),
                            row.get("completed_trade_count"),
                            row.get("win_rate"),
                            row.get("loss_rate"),
                            row.get("average_return"),
                            row.get("median_return"),
                            row.get("gross_profit"),
                            row.get("gross_loss"),
                            row.get("net_profit"),
                            row.get("profit_factor"),
                            row.get("expectancy"),
                            row.get("sharpe_ratio"),
                            row.get("sortino_ratio"),
                            row.get("maximum_drawdown"),
                            row.get("average_winner"),
                            row.get("average_loser"),
                            row.get("payoff_ratio"),
                            row.get("average_holding_time"),
                            row.get("best_trade"),
                            row.get("worst_trade"),
                            row.get("consecutive_wins"),
                            row.get("consecutive_losses"),
                            _stable_json(row.get("recent_20") or {}),
                            _stable_json(row.get("recent_60") or {}),
                            _stable_json(row.get("full_history") or {}),
                            _stable_json(row.get("performance_by_regime") or {}),
                            _stable_json(row.get("performance_by_sector") or {}),
                            _stable_json(row.get("performance_by_score_range") or {}),
                            row.get("sample_status"),
                            captured_at,
                        ),
                    )
            finally:
                cursor.close()
        return version_id

    def save_regime_calculation(self, row: dict[str, Any]) -> str:
        if not self.db.enabled:
            return ""
        self.db.ensure_schema()
        regime_calc_id = str(row.get("regime_calc_id") or f"regime:{_utc_iso()}")
        self.db.execute(
            """
            INSERT OR REPLACE INTO market_regime_calculations (
                regime_calc_id, regime_id, regime_version, inputs_json,
                regime_score, regime_confidence, warnings_json,
                calculated_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                regime_calc_id,
                row.get("regime_id"),
                row.get("regime_version"),
                _stable_json(row.get("inputs") or {}),
                row.get("score"),
                row.get("confidence"),
                _stable_json(row.get("warnings") or []),
                row.get("calculation_timestamp") or _utc_iso(),
                _utc_iso(),
            ),
        )
        return regime_calc_id

    def save_strategy_regime_metrics(self, regime_calc_id: str, rows: list[dict[str, Any]]) -> int:
        if not self.db.enabled:
            return 0
        self.db.ensure_schema()
        conn = self.db.conn
        with conn:
            cursor = conn.cursor()
            try:
                for row in rows:
                    cursor.execute(
                        self._adapt_query(
                            """
                            INSERT INTO strategy_regime_metrics (
                                regime_calc_id, strategy_id, strategy_version, regime_id,
                                sample_count, win_rate, expectancy, drawdown,
                                recent_degradation, compatibility_score, pause_recommended,
                                reasons_json, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """
                        ),
                        (
                            regime_calc_id,
                            row.get("strategy_id"),
                            row.get("strategy_version"),
                            row.get("regime_id"),
                            row.get("sample_count"),
                            row.get("win_rate"),
                            row.get("expectancy"),
                            row.get("drawdown"),
                            row.get("recent_degradation"),
                            row.get("compatibility_score"),
                            1 if row.get("pause_recommended") else 0,
                            _stable_json(row.get("reasons") or []),
                            _utc_iso(),
                        ),
                    )
            finally:
                cursor.close()
        return len(rows)

    def save_factor_effectiveness(self, rows: list[dict[str, Any]]) -> int:
        if not self.db.enabled:
            return 0
        self.db.ensure_schema()
        conn = self.db.conn
        with conn:
            cursor = conn.cursor()
            try:
                for row in rows:
                    cursor.execute(
                        self._adapt_query(
                            """
                            INSERT INTO factor_effectiveness_metrics (
                                analysis_version, factor_name, factor_bucket,
                                strategy_id, regime_id, sample_count,
                                win_rate, average_return, median_return,
                                expectancy, profit_factor, drawdown_contribution,
                                forward_return_correlation, stability_score,
                                predictive_status, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """
                        ),
                        (
                            row.get("analysis_version"),
                            row.get("factor_name"),
                            row.get("factor_bucket"),
                            row.get("strategy_id"),
                            row.get("regime_id"),
                            row.get("sample_count"),
                            row.get("win_rate"),
                            row.get("average_return"),
                            row.get("median_return"),
                            row.get("expectancy"),
                            row.get("profit_factor"),
                            row.get("drawdown_contribution"),
                            row.get("forward_return_correlation"),
                            row.get("stability_score"),
                            row.get("predictive_status"),
                            _utc_iso(),
                        ),
                    )
            finally:
                cursor.close()
        return len(rows)

    def _save_recommendation_rows(self, table: str, rows: list[dict[str, Any]], mapping: list[str]) -> int:
        if not self.db.enabled:
            return 0
        self.db.ensure_schema()
        placeholders = ", ".join(["?"] * len(mapping))
        columns = ", ".join(mapping)
        conn = self.db.conn
        with conn:
            cursor = conn.cursor()
            try:
                for row in rows:
                    values = []
                    for key in mapping:
                        value = row.get(key)
                        if key.endswith("_json"):
                            value = _stable_json(value or {})
                        values.append(value)
                    cursor.execute(self._adapt_query(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"), tuple(values))
            finally:
                cursor.close()
        return len(rows)

    def save_allocation_recommendations(self, rows: list[dict[str, Any]]) -> int:
        payloads = []
        for row in rows:
            payloads.append(
                {
                    "recommendation_id": row.get("recommendation_id"),
                    "recommendation_version": row.get("recommendation_version"),
                    "strategy_id": row.get("strategy_id"),
                    "strategy_version": row.get("strategy_version"),
                    "symbol": row.get("symbol"),
                    "quantum_score": row.get("quantum_score"),
                    "strategy_score": row.get("strategy_score"),
                    "historical_expectancy": row.get("historical_expectancy"),
                    "profit_factor": row.get("profit_factor"),
                    "drawdown": row.get("drawdown"),
                    "sample_size": row.get("sample_size"),
                    "market_regime": row.get("market_regime"),
                    "portfolio_concentration": row.get("portfolio_concentration"),
                    "stability_score": row.get("stability_score"),
                    "recommended_allocation_pct": row.get("recommended_allocation_pct"),
                    "recommended_risk_pct": row.get("recommended_risk_pct"),
                    "confidence_tier": row.get("confidence_tier"),
                    "reasons_json": row.get("reasons") or [],
                    "warnings_json": row.get("warnings") or [],
                    "max_allowed_allocation_pct": row.get("max_allowed_allocation_pct"),
                    "policy_passed": 1 if row.get("policy_passed") else 0,
                    "review_required": 1 if row.get("review_required", True) else 0,
                    "created_at": _utc_iso(),
                }
            )
        return self._save_recommendation_rows(
            "allocation_recommendations",
            payloads,
            [
                "recommendation_id", "recommendation_version", "strategy_id", "strategy_version", "symbol",
                "quantum_score", "strategy_score", "historical_expectancy", "profit_factor", "drawdown",
                "sample_size", "market_regime", "portfolio_concentration", "stability_score",
                "recommended_allocation_pct", "recommended_risk_pct", "confidence_tier", "reasons_json",
                "warnings_json", "max_allowed_allocation_pct", "policy_passed", "review_required", "created_at",
            ],
        )

    def save_strategy_state_recommendations(self, rows: list[dict[str, Any]]) -> int:
        payloads = []
        for row in rows:
            payloads.append(
                {
                    "recommendation_id": row.get("recommendation_id"),
                    "recommendation_version": row.get("recommendation_version"),
                    "strategy_id": row.get("strategy_id"),
                    "strategy_version": row.get("strategy_version"),
                    "current_state": row.get("current_state"),
                    "proposed_state": row.get("proposed_state"),
                    "sample_size": row.get("sample_size"),
                    "net_expectancy": row.get("net_expectancy"),
                    "profit_factor": row.get("profit_factor"),
                    "sharpe_ratio": row.get("sharpe_ratio"),
                    "drawdown": row.get("drawdown"),
                    "recent_degradation": row.get("recent_degradation"),
                    "regime_specific_result": row.get("regime_specific_result"),
                    "stability_score": row.get("stability_score"),
                    "automation_allowed": 1 if row.get("automation_allowed") else 0,
                    "review_required": 1 if row.get("review_required", True) else 0,
                    "reasons_json": row.get("reasons") or [],
                    "warnings_json": row.get("warnings") or [],
                    "created_at": _utc_iso(),
                }
            )
        return self._save_recommendation_rows(
            "strategy_state_recommendations",
            payloads,
            [
                "recommendation_id", "recommendation_version", "strategy_id", "strategy_version", "current_state", "proposed_state",
                "sample_size", "net_expectancy", "profit_factor", "sharpe_ratio", "drawdown", "recent_degradation",
                "regime_specific_result", "stability_score", "automation_allowed", "review_required", "reasons_json", "warnings_json", "created_at",
            ],
        )

    def save_weight_change_recommendations(self, rows: list[dict[str, Any]]) -> int:
        payloads = []
        for row in rows:
            payloads.append(
                {
                    "recommendation_id": row.get("recommendation_id"),
                    "recommendation_version": row.get("recommendation_version"),
                    "factor_name": row.get("factor_name"),
                    "current_weight": row.get("current_weight"),
                    "proposed_weight": row.get("proposed_weight"),
                    "evidence_json": row.get("evidence") or {},
                    "sample_size": row.get("sample_size"),
                    "expected_benefit": row.get("expected_benefit"),
                    "risk_score": row.get("risk_score"),
                    "confidence": row.get("confidence"),
                    "rollback_plan": row.get("rollback_plan"),
                    "walk_forward_passed": 1 if row.get("walk_forward_passed") else 0,
                    "out_of_sample_passed": 1 if row.get("out_of_sample_passed") else 0,
                    "review_required": 1 if row.get("review_required", True) else 0,
                    "rejected_reason": row.get("rejected_reason"),
                    "created_at": _utc_iso(),
                }
            )
        return self._save_recommendation_rows(
            "weight_change_recommendations",
            payloads,
            [
                "recommendation_id", "recommendation_version", "factor_name", "current_weight", "proposed_weight", "evidence_json",
                "sample_size", "expected_benefit", "risk_score", "confidence", "rollback_plan", "walk_forward_passed",
                "out_of_sample_passed", "review_required", "rejected_reason", "created_at",
            ],
        )

    def save_report_snapshot(self, report: dict[str, Any]) -> str:
        if not self.db.enabled:
            return ""
        self.db.ensure_schema()
        report_id = str(report.get("report_id") or f"{report.get('report_type', 'report')}:{_utc_iso()}")
        unresolved = list(report.get("unresolved_data_quality_issues") or [])
        self.db.execute(
            """
            INSERT OR REPLACE INTO intelligence_report_snapshots (
                report_id, report_type, report_version, report_period_start,
                report_period_end, payload_json, unresolved_data_quality_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id,
                report.get("report_type"),
                report.get("report_version"),
                report.get("period_start") or report.get("market_date"),
                report.get("period_end") or report.get("market_date"),
                _stable_json(report),
                _stable_json(unresolved),
                _utc_iso(),
            ),
        )
        return report_id

    def save_portfolio_intelligence_result(
        self,
        *,
        allocation_run_id: str,
        source_scan_run_id: str,
        account_equity: float,
        available_cash: float,
        investable_capital: float,
        result: dict[str, Any],
        configuration: dict[str, Any] | None = None,
    ) -> str:
        if not self.db.enabled:
            return ""
        self.db.ensure_schema()

        run_id = str(allocation_run_id or f"portfolio-allocation:{_utc_iso()}")
        generated_at = str(result.get("generated_at") or _utc_iso())
        selected_count = int(result.get("selected_count") or len((result.get("allocation") or {}).get("proposed_allocations") or []))
        rejected_count = int(result.get("rejected_count") or len((result.get("allocation") or {}).get("rejected_allocations") or []))
        review_required = 1 if bool(result.get("review_required", True)) else 0

        self.db.execute(
            """
            INSERT INTO portfolio_allocation_runs (
                allocation_run_id, source_scan_run_id, generated_at,
                account_equity, available_cash, investable_capital,
                proposed_exposure, cash_reserve, selected_count, rejected_count,
                portfolio_risk_score, diversification_score, policy_version,
                review_required, configuration_json, summary_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(allocation_run_id) DO UPDATE SET
                source_scan_run_id = excluded.source_scan_run_id,
                generated_at = excluded.generated_at,
                account_equity = excluded.account_equity,
                available_cash = excluded.available_cash,
                investable_capital = excluded.investable_capital,
                proposed_exposure = excluded.proposed_exposure,
                cash_reserve = excluded.cash_reserve,
                selected_count = excluded.selected_count,
                rejected_count = excluded.rejected_count,
                portfolio_risk_score = excluded.portfolio_risk_score,
                diversification_score = excluded.diversification_score,
                policy_version = excluded.policy_version,
                review_required = excluded.review_required,
                configuration_json = excluded.configuration_json,
                summary_json = excluded.summary_json
            """,
            (
                run_id,
                str(source_scan_run_id or ""),
                generated_at,
                float(account_equity),
                float(available_cash),
                float(investable_capital),
                float(result.get("total_proposed_exposure") or 0.0),
                float(result.get("cash_reserve") or 0.0),
                selected_count,
                rejected_count,
                float(result.get("portfolio_risk_score") or 0.0),
                float(result.get("diversification_score") or 0.0),
                str((configuration or {}).get("policy_version") or "portfolio_intelligence_v1"),
                review_required,
                _stable_json(configuration or {}),
                _stable_json(result),
            ),
        )

        self.db.execute("DELETE FROM portfolio_allocation_recommendations WHERE allocation_run_id = ?", (run_id,))
        self.db.execute("DELETE FROM portfolio_exposure_snapshots WHERE allocation_run_id = ?", (run_id,))

        allocation = dict(result.get("allocation") or {})
        all_rows = list(allocation.get("proposed_allocations") or []) + list(allocation.get("rejected_allocations") or [])
        for idx, row in enumerate(all_rows, start=1):
            rec_id = f"{run_id}:rec:{idx:04d}:{str(row.get('symbol') or 'UNKNOWN')}"
            self.db.execute(
                """
                INSERT INTO portfolio_allocation_recommendations (
                    recommendation_id, allocation_run_id, symbol, rank,
                    strategy_id, strategy_version, quantum_score, strategy_score,
                    target_allocation_pct, target_notional, proposed_quantity,
                    sector, confidence_tier, average_correlation, maximum_correlation,
                    risk_reward_ratio, selected, rejection_reasons_json,
                    warnings_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rec_id,
                    run_id,
                    str(row.get("symbol") or ""),
                    int(row.get("rank") or 0),
                    str(row.get("strategy_id") or ""),
                    str(row.get("strategy_version") or ""),
                    row.get("quantum_score"),
                    row.get("strategy_score"),
                    float(row.get("target_allocation_percent") or 0.0),
                    float(row.get("target_notional") or 0.0),
                    float(row.get("proposed_quantity") or 0.0),
                    str(row.get("sector") or "Unknown"),
                    str(row.get("confidence_tier") or "UNKNOWN"),
                    row.get("average_correlation"),
                    row.get("maximum_correlation"),
                    row.get("risk_reward_ratio"),
                    1 if bool(row.get("selected")) else 0,
                    _stable_json(row.get("rejection_reasons") or []),
                    _stable_json(row.get("warnings") or []),
                    generated_at,
                ),
            )

        exposure_rows = list(result.get("sector_exposures") or []) + list(result.get("strategy_exposures") or [])
        for idx, row in enumerate(exposure_rows, start=1):
            exposure_type = "sector" if "sector" in row else "strategy"
            key = row.get("sector") if exposure_type == "sector" else row.get("strategy_id")
            self.db.execute(
                """
                INSERT INTO portfolio_exposure_snapshots (
                    snapshot_id, allocation_run_id, exposure_type, exposure_key,
                    current_exposure_pct, proposed_exposure_pct,
                    maximum_allowed_pct, policy_passed, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{run_id}:exp:{idx:04d}",
                    run_id,
                    exposure_type,
                    str(key or "unknown"),
                    float(row.get("current_exposure_pct") or 0.0),
                    float(row.get("proposed_exposure_pct") or 0.0),
                    float(row.get("maximum_allowed_pct") or 0.0),
                    1 if bool(row.get("policy_passed", True)) else 0,
                    generated_at,
                ),
            )

        self.db.execute(
            """
            INSERT INTO portfolio_intelligence_reports (
                report_id, allocation_run_id, report_type, payload_json,
                review_required, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(report_id) DO UPDATE SET
                allocation_run_id = excluded.allocation_run_id,
                report_type = excluded.report_type,
                payload_json = excluded.payload_json,
                review_required = excluded.review_required,
                created_at = excluded.created_at
            """,
            (
                f"{run_id}:summary",
                run_id,
                "portfolio_intelligence_summary",
                _stable_json(result),
                review_required,
                generated_at,
            ),
        )
        return run_id

    def fetch_latest_portfolio_intelligence(self) -> dict[str, Any]:
        if not self.db.enabled:
            return {"run": {}, "recommendations": [], "exposures": [], "reports": []}
        self.db.ensure_schema()
        run = self.db.query_one("SELECT * FROM portfolio_allocation_runs ORDER BY generated_at DESC LIMIT 1") or {}
        if not run:
            return {"run": {}, "recommendations": [], "exposures": [], "reports": []}

        run_id = str(run.get("allocation_run_id") or "")
        recommendations = self.db.query_all(
            "SELECT * FROM portfolio_allocation_recommendations WHERE allocation_run_id = ? ORDER BY selected DESC, rank ASC, symbol ASC",
            (run_id,),
        )
        for row in recommendations:
            row["rejection_reasons"] = _json_load(row.get("rejection_reasons_json"), [])
            row["warnings"] = _json_load(row.get("warnings_json"), [])

        exposures = self.db.query_all(
            "SELECT * FROM portfolio_exposure_snapshots WHERE allocation_run_id = ? ORDER BY exposure_type ASC, proposed_exposure_pct DESC",
            (run_id,),
        )
        reports = self.db.query_all(
            "SELECT * FROM portfolio_intelligence_reports WHERE allocation_run_id = ? ORDER BY created_at DESC",
            (run_id,),
        )
        for row in reports:
            row["payload"] = _json_load(row.get("payload_json"), {})

        return {
            "run": run,
            "recommendations": recommendations,
            "exposures": exposures,
            "reports": reports,
        }

    def save_model_version(self, payload: dict[str, Any]) -> str:
        if not self.db.enabled:
            return ""
        self.db.ensure_schema()
        model_version_id = str(payload.get("model_version_id") or f"model:{_utc_iso()}")
        self.db.execute(
            """
            INSERT OR REPLACE INTO intelligence_model_versions (
                model_version_id, model_name, model_version,
                configuration_json, is_active, review_only, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                model_version_id,
                payload.get("model_name"),
                payload.get("model_version"),
                _stable_json(payload.get("configuration") or {}),
                1 if payload.get("is_active", True) else 0,
                1 if payload.get("review_only", True) else 0,
                _utc_iso(),
            ),
        )
        return model_version_id

    def fetch_dashboard_payload(self) -> dict[str, Any]:
        if not self.db.enabled:
            return {
                "db_connected": False,
                "trade_memory": [],
                "strategy_leaderboard": [],
                "latest_regime": {},
                "strategy_regime_matrix": [],
                "factor_effectiveness": [],
                "allocation_recommendations": [],
                "strategy_state_recommendations": [],
                "weight_change_recommendations": [],
                "daily_report": {},
                "weekly_report": {},
                "portfolio_intelligence": {
                    "run": {},
                    "recommendations": [],
                    "exposures": [],
                    "reports": [],
                },
            }

        self.db.ensure_schema()
        trade_memory = self.list_trade_memory_records(limit=500)

        latest_leaderboard = self.db.query_one("SELECT * FROM strategy_leaderboard_versions ORDER BY captured_at DESC LIMIT 1") or {}
        leaderboard_rows = []
        if latest_leaderboard:
            leaderboard_rows = self.db.query_all(
                "SELECT * FROM strategy_leaderboard_records WHERE version_id = ? ORDER BY net_profit DESC",
                (str(latest_leaderboard.get("version_id")),),
            )
            for row in leaderboard_rows:
                row["recent_20"] = _json_load(row.get("recent20_json"), {})
                row["recent_60"] = _json_load(row.get("recent60_json"), {})
                row["full_history"] = _json_load(row.get("full_history_json"), {})
                row["performance_by_regime"] = _json_load(row.get("by_regime_json"), {})
                row["performance_by_sector"] = _json_load(row.get("by_sector_json"), {})
                row["performance_by_score_range"] = _json_load(row.get("by_score_bucket_json"), {})

        latest_regime = self.db.query_one("SELECT * FROM market_regime_calculations ORDER BY calculated_at DESC LIMIT 1") or {}
        if latest_regime:
            latest_regime["inputs"] = _json_load(latest_regime.get("inputs_json"), {})
            latest_regime["warnings"] = _json_load(latest_regime.get("warnings_json"), [])

        matrix = []
        if latest_regime:
            matrix = self.db.query_all(
                "SELECT * FROM strategy_regime_metrics WHERE regime_calc_id = ? ORDER BY strategy_id ASC",
                (str(latest_regime.get("regime_calc_id")),),
            )
            for row in matrix:
                row["reasons"] = _json_load(row.get("reasons_json"), [])

        factor_rows = self.db.query_all(
            "SELECT * FROM factor_effectiveness_metrics ORDER BY created_at DESC LIMIT 300",
        )

        allocation_rows = self.db.query_all(
            "SELECT * FROM allocation_recommendations ORDER BY created_at DESC LIMIT 100",
        )
        for row in allocation_rows:
            row["reasons"] = _json_load(row.get("reasons_json"), [])
            row["warnings"] = _json_load(row.get("warnings_json"), [])

        state_rows = self.db.query_all(
            "SELECT * FROM strategy_state_recommendations ORDER BY created_at DESC LIMIT 100",
        )
        for row in state_rows:
            row["reasons"] = _json_load(row.get("reasons_json"), [])
            row["warnings"] = _json_load(row.get("warnings_json"), [])

        weight_rows = self.db.query_all(
            "SELECT * FROM weight_change_recommendations ORDER BY created_at DESC LIMIT 100",
        )
        for row in weight_rows:
            row["evidence"] = _json_load(row.get("evidence_json"), {})

        reports = self.db.query_all(
            "SELECT * FROM intelligence_report_snapshots ORDER BY created_at DESC LIMIT 20",
        )
        daily_report = {}
        weekly_report = {}
        for row in reports:
            payload = _json_load(row.get("payload_json"), {})
            if not daily_report and str(row.get("report_type") or "") == "daily":
                daily_report = payload
            if not weekly_report and str(row.get("report_type") or "") == "weekly":
                weekly_report = payload

        portfolio_payload = self.fetch_latest_portfolio_intelligence()

        return {
            "db_connected": True,
            "trade_memory": trade_memory,
            "strategy_leaderboard_version": latest_leaderboard,
            "strategy_leaderboard": leaderboard_rows,
            "latest_regime": latest_regime,
            "strategy_regime_matrix": matrix,
            "factor_effectiveness": factor_rows,
            "allocation_recommendations": allocation_rows,
            "strategy_state_recommendations": state_rows,
            "weight_change_recommendations": weight_rows,
            "daily_report": daily_report,
            "weekly_report": weekly_report,
            "portfolio_intelligence": portfolio_payload,
        }

    def close(self) -> None:
        self.db.close()
