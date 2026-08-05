from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from correlation_engine import CorrelationPolicy, summarize_portfolio_correlation
from portfolio_allocator import (
    AllocationPolicy,
    ExistingPosition,
    PortfolioCandidate,
    PortfolioAllocationResult,
    allocate_portfolio_recommendation,
)
from sector_manager import SectorPolicy, summarize_sector_snapshot


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _normalize_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _strategy_action_multiplier(action: str) -> float:
    table = {
        "PROMOTE_RECOMMENDED": 1.20,
        "MAINTAIN": 1.00,
        "REDUCE_RECOMMENDED": 0.75,
        "PAUSE_RECOMMENDED": 0.00,
        "INSUFFICIENT_SAMPLE": 1.00,
    }
    return float(table.get(str(action or "MAINTAIN"), 1.0))


def build_strategy_allocation_evidence(
    strategy_leaderboard: list[dict[str, Any]],
    min_samples: int = 30,
) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    for row in list(strategy_leaderboard or []):
        strategy_id = str(row.get("strategy_id") or "unknown")
        strategy_version = str(row.get("strategy_version") or "unknown")
        key = f"{strategy_id}:{strategy_version}"

        sample = int(row.get("completed_trade_count") or 0)
        win_rate = _safe_float(row.get("win_rate"), 0.0)
        expectancy = _safe_float(row.get("expectancy"), 0.0)
        profit_factor = _safe_float(row.get("profit_factor"), 0.0)
        sharpe = _safe_float(row.get("sharpe_ratio"), 0.0)
        sortino = _safe_float(row.get("sortino_ratio"), 0.0)
        drawdown = _safe_float(row.get("maximum_drawdown"), 0.0)
        recent20 = dict(row.get("recent_20") or row.get("recent20") or {})
        recent60 = dict(row.get("recent_60") or row.get("recent60") or {})
        regime_quality = _safe_float(row.get("regime_compatibility_score"), 50.0)
        stability = max(0.0, min(1.0, _safe_float(row.get("stability_score"), win_rate)))

        action = "MAINTAIN"
        reasons: list[str] = []

        if sample < int(min_samples):
            action = "INSUFFICIENT_SAMPLE"
            reasons.append("insufficient_sample")
        else:
            recent20_expectancy = _safe_float(recent20.get("expectancy"), expectancy)
            recent60_expectancy = _safe_float(recent60.get("expectancy"), expectancy)
            if drawdown >= 0.30:
                action = "PAUSE_RECOMMENDED"
                reasons.append("severe_drawdown")
            elif recent20_expectancy < 0 or recent60_expectancy < 0 or profit_factor < 1.0:
                action = "REDUCE_RECOMMENDED"
                reasons.append("recent_underperformance")
            elif expectancy > 0 and profit_factor >= 1.2 and sharpe > 0.6 and sortino > 0.4 and regime_quality >= 55.0:
                action = "PROMOTE_RECOMMENDED"
                reasons.append("strong_evidence")
            else:
                action = "MAINTAIN"
                reasons.append("mixed_signals")

        evidence[key] = {
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "completed_trade_count": sample,
            "win_rate": win_rate,
            "expectancy": expectancy,
            "profit_factor": profit_factor,
            "sharpe_ratio": sharpe,
            "sortino_ratio": sortino,
            "maximum_drawdown": drawdown,
            "recent20": recent20,
            "recent60": recent60,
            "market_regime_compatibility": regime_quality,
            "stability_score": stability,
            "action": action,
            "allocation_multiplier": _strategy_action_multiplier(action),
            "reasons": reasons,
            "review_only": True,
        }
    return evidence


@dataclass(frozen=True)
class PortfolioIntelligenceResult:
    generated_at: str
    input_candidate_count: int
    eligible_candidate_count: int
    selected_count: int
    rejected_count: int
    total_proposed_exposure: float
    cash_reserve: float
    sector_exposures: list[dict[str, Any]] = field(default_factory=list)
    strategy_exposures: list[dict[str, Any]] = field(default_factory=list)
    correlation_summary: dict[str, Any] = field(default_factory=dict)
    portfolio_risk_score: float = 0.0
    diversification_score: float = 0.0
    top_warnings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    review_required: bool = True
    allocation: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _compute_risk_and_diversification(
    allocation: PortfolioAllocationResult,
    correlation_summary: dict[str, Any],
) -> tuple[float, float]:
    max_position_pct = max([_safe_float(item.target_allocation_percent, 0.0) for item in allocation.proposed_allocations] or [0.0])
    max_sector_pct = max([_safe_float(item.get("proposed_exposure_pct"), 0.0) for item in allocation.sector_exposure_summary] or [0.0])
    max_corr = _safe_float(correlation_summary.get("maximum_correlation"), 0.0)

    concentration_penalty = min((max_position_pct / 10.0) * 20.0, 40.0)
    sector_penalty = min((max_sector_pct / 30.0) * 20.0, 30.0)
    corr_penalty = min(max(max_corr, 0.0) * 30.0, 30.0)

    risk_score = max(0.0, min(100.0, concentration_penalty + sector_penalty + corr_penalty))
    diversification_score = max(0.0, min(100.0, 100.0 - risk_score))
    return round(risk_score, 6), round(diversification_score, 6)


def run_portfolio_intelligence(
    ranked_candidates: list[dict[str, Any]],
    current_positions: list[dict[str, Any]],
    account_equity: float,
    available_cash: float,
    price_history_by_symbol: dict[str, Any] | None = None,
    strategy_leaderboard: list[dict[str, Any]] | None = None,
    allocation_policy: AllocationPolicy | None = None,
    correlation_policy: CorrelationPolicy | None = None,
    sector_policy: SectorPolicy | None = None,
) -> PortfolioIntelligenceResult:
    policy = allocation_policy or AllocationPolicy()
    corr_policy = correlation_policy or CorrelationPolicy()
    sec_policy = sector_policy or SectorPolicy()

    strategy_evidence = build_strategy_allocation_evidence(strategy_leaderboard or [], min_samples=30)

    candidates = [PortfolioCandidate.from_candidate_payload(item) for item in list(ranked_candidates or [])]
    positions = [ExistingPosition.from_position_payload(item) for item in list(current_positions or [])]

    allocation = allocate_portfolio_recommendation(
        candidates=candidates,
        existing_positions=positions,
        account_equity=account_equity,
        available_cash=available_cash,
        strategy_evidence=strategy_evidence,
        price_history_by_symbol=dict(price_history_by_symbol or {}),
        policy=policy,
        correlation_policy=corr_policy,
        sector_policy=sec_policy,
    )

    selected_symbols = [item.symbol for item in allocation.proposed_allocations]
    correlation_summary = summarize_portfolio_correlation(selected_symbols, dict(price_history_by_symbol or {}), corr_policy)
    risk_score, diversification_score = _compute_risk_and_diversification(allocation, correlation_summary)

    strategy_exposures = [
        {
            "strategy_id": key,
            "proposed_exposure_pct": round((value / max(_safe_float(account_equity, 0.0), 1.0)) * 100.0, 6),
        }
        for key, value in sorted(allocation.strategy_exposure_notional.items(), key=lambda item: (-item[1], item[0]))
    ]

    recommendations = [
        f"Review proposed allocation for {row.symbol}: {row.target_allocation_percent:.2f}% target" for row in allocation.proposed_allocations[:10]
    ]

    top_warnings = list(dict.fromkeys([*allocation.policy_warnings, *allocation.top_warnings]))[:20]
    if allocation.review_required:
        recommendations.append("Human approval required before any trade action")

    return PortfolioIntelligenceResult(
        generated_at=_utc_iso(),
        input_candidate_count=len(candidates),
        eligible_candidate_count=allocation.eligible_candidate_count,
        selected_count=len(allocation.proposed_allocations),
        rejected_count=len(allocation.rejected_allocations),
        total_proposed_exposure=round(allocation.total_proposed_exposure, 6),
        cash_reserve=round(allocation.cash_reserve, 6),
        sector_exposures=summarize_sector_snapshot(
            allocation.current_sector_notional,
            allocation.proposed_sector_notional,
            account_equity,
            sec_policy,
        ),
        strategy_exposures=strategy_exposures,
        correlation_summary=correlation_summary,
        portfolio_risk_score=risk_score,
        diversification_score=diversification_score,
        top_warnings=top_warnings,
        recommendations=recommendations,
        review_required=True,
        allocation=allocation.to_dict(),
    )
