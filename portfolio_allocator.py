from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from math import floor
from typing import Any

from correlation_engine import CorrelationPolicy, apply_correlation_reduction, assess_symbol_correlation
from sector_manager import SectorPolicy, apply_sector_constraint, normalize_sector


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _normalize_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _quality_ok(status: str) -> bool:
    normalized = str(status or "").strip().lower()
    return normalized in {"ok", "good", "healthy", "pass", "ready"}


def _confidence_tier(confidence: float) -> str:
    if confidence >= 85:
        return "HIGH"
    if confidence >= 70:
        return "MEDIUM"
    if confidence >= 55:
        return "LOW"
    return "VERY_LOW"


@dataclass(frozen=True)
class PortfolioCandidate:
    symbol: str
    rank: int
    quantum_score: float
    strategy_score: float
    strategy_id: str
    strategy_version: str
    confidence: float
    volatility: float
    reward_risk_ratio: float
    sector: str
    industry: str
    latest_price: float
    liquidity_status: str
    data_quality_status: str
    expected_notional_hint: float = 0.0

    @classmethod
    def from_candidate_payload(cls, payload: dict[str, Any]) -> "PortfolioCandidate":
        quantum = dict(payload.get("quantum_score") or {})
        strategy_scores = dict(payload.get("strategy_specific_scores") or {})
        strategy_rows: list[tuple[str, dict[str, Any]]] = []
        for key, value in strategy_scores.items():
            row = dict(value or {})
            if bool(row.get("eligible", True)):
                strategy_rows.append((str(row.get("strategy_id") or key), row))
        strategy_rows = sorted(
            strategy_rows,
            key=lambda item: (
                -_safe_float(item[1].get("strategy_score"), 0.0),
                -_safe_float(item[1].get("confidence"), 0.0),
                str(item[0]),
            ),
        )
        strategy_id, strategy_payload = strategy_rows[0] if strategy_rows else ("unknown", {})

        risk_reward = dict(quantum.get("risk_reward") or {})
        score = _safe_float(quantum.get("final_score"), _safe_float(payload.get("overall_score") or payload.get("score"), 0.0))
        reward_risk = _safe_float(
            risk_reward.get("reward_risk_ratio"),
            _safe_float(payload.get("expected_reward_risk") or strategy_payload.get("expected_reward_risk"), 0.0),
        )

        return cls(
            symbol=_normalize_symbol(payload.get("symbol")),
            rank=max(_safe_int(payload.get("rank"), 0), 0),
            quantum_score=score,
            strategy_score=_safe_float(strategy_payload.get("strategy_score"), 0.0),
            strategy_id=strategy_id,
            strategy_version=str(strategy_payload.get("strategy_version") or payload.get("strategy_version") or "unknown"),
            confidence=_safe_float(payload.get("confidence") or strategy_payload.get("confidence"), 0.0),
            volatility=_safe_float(payload.get("volatility") or payload.get("volatility_pct"), 0.0),
            reward_risk_ratio=reward_risk,
            sector=normalize_sector(payload.get("sector")),
            industry=str(payload.get("industry") or "Unknown"),
            latest_price=_safe_float(payload.get("latest_price") or payload.get("price") or payload.get("close"), 0.0),
            liquidity_status=str((payload.get("liquidity_status") or quantum.get("liquidity_status") or "ok")),
            data_quality_status=str(payload.get("data_quality_status") or quantum.get("data_quality_status") or "unknown"),
            expected_notional_hint=_safe_float(payload.get("suggested_paper_notional"), 0.0),
        )


@dataclass(frozen=True)
class ExistingPosition:
    symbol: str
    quantity: float
    latest_price: float
    sector: str
    strategy_id: str

    @property
    def notional(self) -> float:
        return max(self.quantity, 0.0) * max(self.latest_price, 0.0)

    @classmethod
    def from_position_payload(cls, payload: dict[str, Any]) -> "ExistingPosition":
        return cls(
            symbol=_normalize_symbol(payload.get("symbol")),
            quantity=max(_safe_float(payload.get("quantity"), 0.0), 0.0),
            latest_price=max(
                _safe_float(payload.get("latest_price"), 0.0),
                _safe_float(payload.get("market_price"), 0.0),
                _safe_float(payload.get("avg_price"), 0.0),
                _safe_float(payload.get("entry_price"), 0.0),
            ),
            sector=normalize_sector(payload.get("sector")),
            strategy_id=str(payload.get("strategy_id") or "unknown"),
        )


@dataclass(frozen=True)
class AllocationPolicy:
    max_positions: int = 10
    max_position_percent: float = 10.0
    max_sector_percent: float = 30.0
    min_cash_reserve_percent: float = 20.0
    max_correlation: float = 0.80
    max_strategy_percent: float = 40.0
    min_quantum_score: float = 70.0
    min_risk_reward: float = 1.5
    allocation_mode: str = "RECOMMENDATION_ONLY"
    unknown_sector_max_percent: float = 10.0
    allow_fractional_quantity: bool = False


@dataclass(frozen=True)
class ProposedAllocation:
    symbol: str
    rank: int
    target_allocation_percent: float
    target_notional: float
    proposed_quantity: float
    risk_allocation_percent: float
    sector: str
    strategy_id: str
    confidence_tier: str
    average_correlation: float | None
    maximum_correlation: float | None
    selected: bool
    rejection_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PortfolioAllocationResult:
    generated_at: str
    selected_symbols: list[str]
    proposed_allocations: list[ProposedAllocation]
    rejected_allocations: list[ProposedAllocation]
    cash_reserve: float
    total_proposed_exposure: float
    eligible_candidate_count: int
    policy_warnings: list[str] = field(default_factory=list)
    top_warnings: list[str] = field(default_factory=list)
    review_required: bool = True
    current_sector_notional: dict[str, float] = field(default_factory=dict)
    proposed_sector_notional: dict[str, float] = field(default_factory=dict)
    strategy_exposure_notional: dict[str, float] = field(default_factory=dict)
    sector_exposure_summary: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "selected_symbols": list(self.selected_symbols),
            "proposed_allocations": [asdict(item) for item in self.proposed_allocations],
            "rejected_allocations": [asdict(item) for item in self.rejected_allocations],
            "cash_reserve": self.cash_reserve,
            "total_proposed_exposure": self.total_proposed_exposure,
            "eligible_candidate_count": self.eligible_candidate_count,
            "policy_warnings": list(self.policy_warnings),
            "top_warnings": list(self.top_warnings),
            "review_required": self.review_required,
            "current_sector_notional": dict(self.current_sector_notional),
            "proposed_sector_notional": dict(self.proposed_sector_notional),
            "strategy_exposure_notional": dict(self.strategy_exposure_notional),
            "sector_exposure_summary": list(self.sector_exposure_summary),
        }


def _strategy_multiplier(strategy_evidence: dict[str, dict[str, Any]], strategy_id: str, strategy_version: str) -> tuple[float, list[str]]:
    key = f"{strategy_id}:{strategy_version}"
    row = dict((strategy_evidence or {}).get(key) or {})
    if not row:
        return 1.0, []

    action = str(row.get("action") or "MAINTAIN")
    reasons = [f"strategy_action:{action}"]
    sample = _safe_int(row.get("completed_trade_count"), 0)
    if sample < 30:
        reasons.append("INSUFFICIENT_SAMPLE")
    multiplier = _safe_float(row.get("allocation_multiplier"), 1.0)
    if sample < 30 and multiplier > 1.0:
        multiplier = 1.0
    return max(multiplier, 0.0), reasons


def _candidate_order_key(item: PortfolioCandidate) -> tuple[Any, ...]:
    return (
        -item.quantum_score,
        -item.strategy_score,
        -item.confidence,
        item.symbol,
        item.strategy_id,
        item.strategy_version,
    )


def _build_rejected(candidate: PortfolioCandidate, reasons: list[str], warnings: list[str]) -> ProposedAllocation:
    return ProposedAllocation(
        symbol=candidate.symbol,
        rank=candidate.rank,
        target_allocation_percent=0.0,
        target_notional=0.0,
        proposed_quantity=0.0,
        risk_allocation_percent=0.0,
        sector=candidate.sector,
        strategy_id=candidate.strategy_id,
        confidence_tier=_confidence_tier(candidate.confidence),
        average_correlation=None,
        maximum_correlation=None,
        selected=False,
        rejection_reasons=sorted(set(reasons)),
        warnings=sorted(set(warnings)),
    )


def _quantity_for_notional(notional: float, price: float, allow_fractional: bool) -> float:
    if notional <= 0 or price <= 0:
        return 0.0
    if allow_fractional:
        return round(notional / price, 6)
    return float(floor(notional / price))


def allocate_portfolio_recommendation(
    candidates: list[PortfolioCandidate],
    existing_positions: list[ExistingPosition],
    account_equity: float,
    available_cash: float,
    strategy_evidence: dict[str, dict[str, Any]] | None = None,
    price_history_by_symbol: dict[str, Any] | None = None,
    policy: AllocationPolicy | None = None,
    correlation_policy: CorrelationPolicy | None = None,
    sector_policy: SectorPolicy | None = None,
) -> PortfolioAllocationResult:
    active = policy or AllocationPolicy()
    corr_policy = correlation_policy or CorrelationPolicy(max_correlation=active.max_correlation)
    sec_policy = sector_policy or SectorPolicy(
        max_sector_percent=active.max_sector_percent,
        unknown_sector_max_percent=active.unknown_sector_max_percent,
    )

    now = _utc_iso()
    equity = _safe_float(account_equity, 0.0)
    cash = _safe_float(available_cash, 0.0)

    policy_warnings: list[str] = []
    if str(active.allocation_mode).upper() != "RECOMMENDATION_ONLY":
        policy_warnings.append("allocation_mode_forced_to_recommendation_only")

    if equity <= 0 or cash < 0:
        policy_warnings.append("non_positive_equity_fail_closed")
        return PortfolioAllocationResult(
            generated_at=now,
            selected_symbols=[],
            proposed_allocations=[],
            rejected_allocations=[_build_rejected(item, ["non_positive_equity"], []) for item in sorted(candidates, key=_candidate_order_key)],
            cash_reserve=max(cash, 0.0),
            total_proposed_exposure=0.0,
            eligible_candidate_count=0,
            policy_warnings=policy_warnings,
            top_warnings=policy_warnings,
            review_required=True,
        )

    min_reserve = equity * (active.min_cash_reserve_percent / 100.0)
    investable_capital = max(min(cash - min_reserve, equity - min_reserve), 0.0)

    current_sector_notional: dict[str, float] = {}
    strategy_notional: dict[str, float] = {}
    existing_symbols = {_normalize_symbol(item.symbol) for item in existing_positions}
    for pos in existing_positions:
        notional = pos.notional
        if notional <= 0:
            continue
        current_sector_notional[pos.sector] = current_sector_notional.get(pos.sector, 0.0) + notional
        strategy_notional[pos.strategy_id] = strategy_notional.get(pos.strategy_id, 0.0) + notional

    proposed_sector_notional = dict(current_sector_notional)
    selected: list[ProposedAllocation] = []
    rejected: list[ProposedAllocation] = []

    ordered_candidates = sorted(list(candidates or []), key=_candidate_order_key)
    eligible_count = 0

    for candidate in ordered_candidates:
        reasons: list[str] = []
        warnings: list[str] = []

        if not candidate.symbol:
            reasons.append("missing_symbol")
        if candidate.symbol in existing_symbols:
            reasons.append("already_in_existing_positions")
        if candidate.latest_price <= 0:
            reasons.append("invalid_latest_price")
        if candidate.quantum_score < active.min_quantum_score:
            reasons.append("quantum_score_below_minimum")
        if candidate.reward_risk_ratio < active.min_risk_reward:
            reasons.append("risk_reward_below_minimum")
        if not _quality_ok(candidate.data_quality_status):
            reasons.append("data_quality_rejected")
        if str(candidate.liquidity_status).strip().lower() in {"bad", "poor", "insufficient", "failed"}:
            reasons.append("liquidity_rejected")
        if len(selected) + len(existing_symbols) >= int(active.max_positions):
            reasons.append("max_positions_reached")

        if reasons:
            rejected.append(_build_rejected(candidate, reasons, warnings))
            continue

        eligible_count += 1

        strategy_multiplier, strategy_reasons = _strategy_multiplier(
            strategy_evidence or {},
            candidate.strategy_id,
            candidate.strategy_version,
        )
        warnings.extend(strategy_reasons)

        score_strength = max(min((candidate.quantum_score - active.min_quantum_score) / max(100.0 - active.min_quantum_score, 1.0), 1.0), 0.0)
        confidence_strength = max(min(candidate.confidence / 100.0, 1.0), 0.0)
        volatility_penalty = min(max(candidate.volatility / 100.0, 0.0), 0.5)

        raw_pct = active.max_position_percent * (0.45 + (0.45 * score_strength) + (0.10 * confidence_strength))
        raw_pct *= (1.0 - volatility_penalty)
        raw_pct *= max(strategy_multiplier, 0.0)
        raw_pct = min(max(raw_pct, 0.0), active.max_position_percent)

        if raw_pct <= 0:
            rejected.append(_build_rejected(candidate, ["strategy_pause_or_zero_allocation"], warnings))
            continue

        target_notional = equity * (raw_pct / 100.0)

        # Position cap.
        max_position_notional = equity * (active.max_position_percent / 100.0)
        target_notional = min(target_notional, max_position_notional)

        # Sector cap (includes existing positions and earlier recommendations).
        sector_result = apply_sector_constraint(
            candidate.sector,
            target_notional,
            equity,
            proposed_sector_notional,
            sec_policy,
        )
        target_notional = _safe_float(sector_result.get("adjusted_notional"), 0.0)
        reasons.extend(list(sector_result.get("reasons") or []))
        warnings.extend(list(sector_result.get("warnings") or []))
        if bool(sector_result.get("rejected")):
            rejected.append(_build_rejected(candidate, reasons or ["sector_rejected"], warnings))
            continue

        # Strategy cap (includes existing positions and earlier recommendations).
        strategy_key = candidate.strategy_id
        strategy_cap = equity * (active.max_strategy_percent / 100.0)
        strategy_used = strategy_notional.get(strategy_key, 0.0)
        strategy_remaining = max(strategy_cap - strategy_used, 0.0)
        if strategy_remaining <= 0:
            rejected.append(_build_rejected(candidate, [*reasons, "strategy_cap_reached"], warnings))
            continue
        if target_notional > strategy_remaining:
            target_notional = strategy_remaining
            warnings.append("strategy_cap_reduction_applied")

        # Correlation checks against existing and already selected names.
        peer_symbols = sorted(existing_symbols | {item.symbol for item in selected})
        corr_assessment = assess_symbol_correlation(
            candidate.symbol,
            peer_symbols,
            dict(price_history_by_symbol or {}),
            corr_policy,
        )
        corr_result = apply_correlation_reduction(target_notional, corr_assessment, corr_policy)
        target_notional = _safe_float(corr_result.get("adjusted_notional"), 0.0)
        warnings.extend(list(corr_result.get("reasons") or []))
        if bool(corr_result.get("rejected")):
            rejected.append(_build_rejected(candidate, [*reasons, "correlation_rejected_after_reduction"], warnings))
            continue

        # Investable capital constraint and deterministic fail-close for exhausted budget.
        invested = sum(item.target_notional for item in selected)
        remaining_investable = max(investable_capital - invested, 0.0)
        target_notional = min(target_notional, remaining_investable)
        if target_notional <= 0:
            rejected.append(_build_rejected(candidate, [*reasons, "investable_capital_exhausted"], warnings))
            continue

        qty = _quantity_for_notional(target_notional, candidate.latest_price, active.allow_fractional_quantity)
        if qty <= 0:
            rejected.append(_build_rejected(candidate, [*reasons, "quantity_below_one_share"], warnings))
            continue

        actual_notional = qty * candidate.latest_price
        allocation_pct = (actual_notional / equity * 100.0) if equity > 0 else 0.0

        proposed = ProposedAllocation(
            symbol=candidate.symbol,
            rank=candidate.rank,
            target_allocation_percent=round(allocation_pct, 6),
            target_notional=round(actual_notional, 6),
            proposed_quantity=round(qty, 6),
            risk_allocation_percent=round(min(allocation_pct * 0.25, active.max_position_percent), 6),
            sector=candidate.sector,
            strategy_id=candidate.strategy_id,
            confidence_tier=_confidence_tier(candidate.confidence),
            average_correlation=(
                None if corr_assessment.get("average_correlation") is None else round(_safe_float(corr_assessment.get("average_correlation"), 0.0), 6)
            ),
            maximum_correlation=(
                None if corr_assessment.get("maximum_correlation") is None else round(_safe_float(corr_assessment.get("maximum_correlation"), 0.0), 6)
            ),
            selected=True,
            rejection_reasons=sorted(set(reasons)),
            warnings=sorted(set(warnings)),
        )
        selected.append(proposed)

        proposed_sector_notional[candidate.sector] = proposed_sector_notional.get(candidate.sector, 0.0) + actual_notional
        strategy_notional[strategy_key] = strategy_notional.get(strategy_key, 0.0) + actual_notional

    total_exposure = sum(item.target_notional for item in selected)
    cash_reserve = max(cash - total_exposure, 0.0)

    sector_summary: list[dict[str, Any]] = []
    for sector in sorted(set(current_sector_notional.keys()) | set(proposed_sector_notional.keys())):
        cap = active.unknown_sector_max_percent if normalize_sector(sector) == "Unknown" else active.max_sector_percent
        cur_pct = (current_sector_notional.get(sector, 0.0) / equity * 100.0) if equity > 0 else 0.0
        prop_pct = (proposed_sector_notional.get(sector, 0.0) / equity * 100.0) if equity > 0 else 0.0
        sector_summary.append(
            {
                "sector": sector,
                "current_exposure_pct": round(cur_pct, 6),
                "proposed_exposure_pct": round(prop_pct, 6),
                "maximum_allowed_pct": round(cap, 6),
                "policy_passed": bool(prop_pct <= cap + 1e-9),
            }
        )

    top_warnings = list(dict.fromkeys(policy_warnings + [w for row in selected for w in row.warnings] + [w for row in rejected for w in row.warnings]))

    return PortfolioAllocationResult(
        generated_at=now,
        selected_symbols=[item.symbol for item in selected],
        proposed_allocations=selected,
        rejected_allocations=rejected,
        cash_reserve=round(cash_reserve, 6),
        total_proposed_exposure=round(total_exposure, 6),
        eligible_candidate_count=eligible_count,
        policy_warnings=policy_warnings,
        top_warnings=top_warnings,
        review_required=True,
        current_sector_notional=current_sector_notional,
        proposed_sector_notional=proposed_sector_notional,
        strategy_exposure_notional=strategy_notional,
        sector_exposure_summary=sorted(sector_summary, key=lambda item: (-float(item["proposed_exposure_pct"]), item["sector"])),
    )


def allocate_portfolio(
    candidates: list[dict[str, Any]],
    starting_portfolio_value: float,
    buying_power: float,
    allow_fractional: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, float]]:
    """Backward-compatible wrapper for older planning modules.

    Returns legacy tuple format while delegating core sizing to the new
    recommendation-only allocator.
    """
    policy = AllocationPolicy(
        max_positions=10,
        max_position_percent=10.0,
        max_sector_percent=30.0,
        min_cash_reserve_percent=20.0,
        max_correlation=0.80,
        max_strategy_percent=40.0,
        min_quantum_score=70.0,
        min_risk_reward=1.5,
        allocation_mode="RECOMMENDATION_ONLY",
        allow_fractional_quantity=bool(allow_fractional),
    )

    candidate_models = [PortfolioCandidate.from_candidate_payload(item) for item in list(candidates or [])]
    result = allocate_portfolio_recommendation(
        candidates=candidate_models,
        existing_positions=[],
        account_equity=max(_safe_float(starting_portfolio_value, 0.0), 0.0),
        available_cash=max(_safe_float(buying_power, 0.0), 0.0),
        strategy_evidence={},
        price_history_by_symbol={},
        policy=policy,
        correlation_policy=CorrelationPolicy(max_correlation=policy.max_correlation),
        sector_policy=SectorPolicy(max_sector_percent=policy.max_sector_percent, unknown_sector_max_percent=policy.unknown_sector_max_percent),
    )

    allocations = []
    for row in result.proposed_allocations:
        allocations.append(
            {
                "rank": row.rank,
                "symbol": row.symbol,
                "signal": "BUY",
                "score": f"{_safe_float(row.target_allocation_percent, 0.0):.2f}",
                "rank_value": f"{_safe_float(row.target_allocation_percent, 0.0):.2f}",
                "latest_price": "0.00",
                "allocation_percentage": f"{row.target_allocation_percent:.2f}%",
                "allocated_dollars": f"{row.target_notional:.2f}",
                "share_quantity": f"{row.proposed_quantity}",
                "estimated_position_value": f"{row.target_notional:.2f}",
                "remaining_cash": f"{result.cash_reserve:.2f}",
                "decision_reason": "; ".join(row.warnings) if row.warnings else "Recommendation-only allocation",
            }
        )

    skipped = []
    for row in result.rejected_allocations:
        skipped.append(
            {
                "symbol": row.symbol,
                "reason": "; ".join(row.rejection_reasons) or "rejected",
            }
        )

    summary = {
        "starting_portfolio_value": max(_safe_float(starting_portfolio_value, 0.0), 0.0),
        "number_of_candidates_received": float(len(candidates or [])),
        "number_of_positions_allocated": float(len(allocations)),
        "total_invested": float(result.total_proposed_exposure),
        "remaining_cash": float(result.cash_reserve),
        "total_exposure_percentage": (
            float(result.total_proposed_exposure) / max(_safe_float(starting_portfolio_value, 0.0), 1.0) * 100.0
        ),
    }

    return allocations, skipped, summary
