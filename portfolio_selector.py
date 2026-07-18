from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from config import (
    PORTFOLIO_MAX_CANDIDATES,
    PORTFOLIO_MAX_POSITIONS,
    PORTFOLIO_MAX_SECTOR_PERCENT,
    PORTFOLIO_MAX_SYMBOL_PERCENT,
    PORTFOLIO_MAX_SYMBOLS_PER_SECTOR,
    PORTFOLIO_MIN_CASH_RESERVE_PERCENT,
    POSITION_REVIEW_MAX_HOLD_DAYS,
    POSITION_REVIEW_MIN_HOLD_SCORE,
    POSITION_REVIEW_MIN_WATCH_SCORE,
    POSITION_REVIEW_RISK_OFF_REGIMES,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_portfolio_shortlist(
    ranked_candidates: list[dict[str, Any]],
    current_positions: list[dict[str, Any]] | None = None,
    pending_order_symbols: list[str] | None = None,
    cooldown_symbols: list[str] | None = None,
    current_cash: float | None = None,
    portfolio_value: float | None = None,
    risk_state: dict[str, Any] | None = None,
    max_candidates: int = PORTFOLIO_MAX_CANDIDATES,
    max_positions: int = PORTFOLIO_MAX_POSITIONS,
    max_symbols_per_sector: int = PORTFOLIO_MAX_SYMBOLS_PER_SECTOR,
    max_sector_percent: float = PORTFOLIO_MAX_SECTOR_PERCENT,
    max_symbol_percent: float = PORTFOLIO_MAX_SYMBOL_PERCENT,
    min_cash_reserve_percent: float = PORTFOLIO_MIN_CASH_RESERVE_PERCENT,
) -> dict[str, Any]:
    positions = current_positions or []
    pending_set = {str(symbol).upper() for symbol in (pending_order_symbols or [])}
    cooldown_set = {str(symbol).upper() for symbol in (cooldown_symbols or [])}
    existing_set = {str(position.get("symbol", "")).upper() for position in positions}
    risk_state = dict(risk_state or {})

    selected: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    warnings: list[str] = []

    if risk_state.get("daily_loss_stop_active") or risk_state.get("portfolio_loss_stop_active"):
        warnings.append("Risk stop is active, no new entries should be selected")
        for candidate in ranked_candidates[:max_candidates]:
            rejected.append({
                "symbol": candidate.get("symbol"),
                "reason": "risk stop active",
                "rank": candidate.get("rank"),
            })
        return {
            "selected": [],
            "rejected": rejected,
            "portfolio_warnings": warnings,
            "selection_summary": {
                "timestamp": _now_iso(),
                "selected_count": 0,
                "rejected_count": len(rejected),
                "reason": "risk_stop_active",
            },
        }

    sector_counts: dict[str, int] = {}
    sector_allocations: dict[str, float] = {}
    available_cash = float(current_cash if current_cash is not None else 0.0)
    total_value = float(portfolio_value if portfolio_value is not None else max(available_cash, 0.0))
    reserve_amount = total_value * (min_cash_reserve_percent / 100.0)
    deployable_cash = max(available_cash - reserve_amount, 0.0)

    for candidate in ranked_candidates[: max(max_candidates, 0)]:
        symbol = str(candidate.get("symbol", "")).upper()
        sector = str(candidate.get("sector") or "Unknown")

        if len(selected) >= max_positions:
            rejected.append({"symbol": symbol, "reason": "maximum positions reached", "rank": candidate.get("rank")})
            continue
        if symbol in existing_set:
            rejected.append({"symbol": symbol, "reason": "already in current positions", "rank": candidate.get("rank")})
            continue
        if symbol in pending_set:
            rejected.append({"symbol": symbol, "reason": "pending order exists", "rank": candidate.get("rank")})
            continue
        if symbol in cooldown_set:
            rejected.append({"symbol": symbol, "reason": "symbol in cooldown", "rank": candidate.get("rank")})
            continue

        current_sector_count = sector_counts.get(sector, 0)
        if current_sector_count >= max_symbols_per_sector:
            rejected.append({"symbol": symbol, "reason": "sector symbol cap reached", "rank": candidate.get("rank")})
            continue

        proposed_symbol_alloc = min(max_symbol_percent, 100.0)
        proposed_sector_alloc = sector_allocations.get(sector, 0.0) + proposed_symbol_alloc
        if proposed_sector_alloc > max_sector_percent:
            rejected.append({"symbol": symbol, "reason": "sector allocation limit reached", "rank": candidate.get("rank")})
            continue

        suggested_notional = min(deployable_cash, total_value * (proposed_symbol_alloc / 100.0)) if total_value > 0 else 0.0
        if suggested_notional <= 0:
            rejected.append({"symbol": symbol, "reason": "insufficient deployable cash after reserve", "rank": candidate.get("rank")})
            continue

        deployable_cash -= suggested_notional
        sector_counts[sector] = current_sector_count + 1
        sector_allocations[sector] = proposed_sector_alloc

        selected.append(
            {
                "rank": candidate.get("rank"),
                "symbol": symbol,
                "sector": sector,
                "score": float(candidate.get("overall_score") or 0.0),
                "confidence": float(candidate.get("confidence") or 0.0),
                "suggested_max_allocation_percent": float(proposed_symbol_alloc),
                "suggested_paper_notional": round(float(suggested_notional), 2),
                "reasons_selected": [
                    "eligible scanner candidate",
                    "passes diversification constraints",
                    "within cash reserve policy",
                ],
                "warnings": ["research candidate only - no order submitted"],
            }
        )

    return {
        "selected": selected,
        "rejected": rejected,
        "portfolio_warnings": warnings,
        "selection_summary": {
            "timestamp": _now_iso(),
            "selected_count": len(selected),
            "rejected_count": len(rejected),
            "cash_reserve_required": round(reserve_amount, 2),
            "cash_remaining_after_selection": round(deployable_cash, 2),
            "sector_counts": dict(sector_counts),
        },
    }


def review_existing_positions(
    held_positions: list[dict[str, Any]],
    score_results_by_symbol: dict[str, dict[str, Any]],
    atr_trailing_stop_hits: dict[str, bool] | None = None,
    max_holding_days: int = POSITION_REVIEW_MAX_HOLD_DAYS,
    min_hold_score: float = POSITION_REVIEW_MIN_HOLD_SCORE,
    min_watch_score: float = POSITION_REVIEW_MIN_WATCH_SCORE,
    risk_off_regimes: list[str] | None = None,
) -> dict[str, Any]:
    risk_off = {str(item).lower() for item in (risk_off_regimes or POSITION_REVIEW_RISK_OFF_REGIMES)}
    atr_stops = {str(symbol).upper(): bool(value) for symbol, value in (atr_trailing_stop_hits or {}).items()}

    reviews: list[dict[str, Any]] = []
    summary_counts = {"HOLD": 0, "WATCH": 0, "REDUCE": 0, "EXIT": 0}

    for position in held_positions:
        symbol = str(position.get("symbol", "")).upper()
        score_result = dict(score_results_by_symbol.get(symbol) or {})
        score = float(score_result.get("overall_score") or 0.0)
        confidence = float(score_result.get("confidence") or 0.0)
        signal = str(score_result.get("signal") or "HOLD").upper()
        regime = str(score_result.get("regime") or "unknown").lower()
        data_quality = dict(score_result.get("data_quality") or {})
        components = dict(score_result.get("component_scores") or {})

        reasons: list[str] = []
        warnings: list[str] = []
        recommendation = "HOLD"

        holding_days = int(position.get("holding_days") or 0)
        if holding_days > max_holding_days:
            reasons.append("maximum holding period reached")
            recommendation = "WATCH"

        if score < min_watch_score or signal in {"EXIT", "REDUCE"}:
            recommendation = "REDUCE"
            reasons.append("signal/score deterioration")
        elif score < min_hold_score:
            recommendation = "WATCH"
            reasons.append("score below hold threshold")

        if float(components.get("risk_quality") or 0.0) < 35.0:
            recommendation = "REDUCE"
            reasons.append("risk-quality score dropped sharply")

        if regime in risk_off:
            recommendation = "EXIT"
            reasons.append("market regime is risk-off")

        if atr_stops.get(symbol, False):
            recommendation = "EXIT"
            reasons.append("ATR trailing-stop condition met")

        if not bool(data_quality.get("history_sufficient", True)):
            reasons.append("data quality unreliable")
            if recommendation == "HOLD":
                recommendation = "WATCH"

        if recommendation == "EXIT" and confidence < 40.0:
            warnings.append("low confidence exit signal")

        summary_counts[recommendation] += 1
        reviews.append(
            {
                "symbol": symbol,
                "current_quantity": float(position.get("quantity") or 0.0),
                "current_entry_price": float(position.get("entry_price") or 0.0),
                "current_market_price": float(position.get("market_price") or 0.0),
                "score": score,
                "confidence": confidence,
                "recommendation": recommendation,
                "reasons": reasons or ["no risk triggers activated"],
                "warnings": warnings,
            }
        )

    return {
        "reviews": reviews,
        "summary": {
            "timestamp": _now_iso(),
            "counts": summary_counts,
            "note": "Recommendations are read-only research outputs only.",
        },
    }
