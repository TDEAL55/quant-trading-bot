from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from stock_exit_policy import evaluate_stock_exit


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _has_open_close(open_orders: list[dict[str, Any]], symbol: str, close_side: str) -> bool:
    final_statuses = {"filled", "canceled", "cancelled", "rejected", "expired", "done_for_day"}
    expected_side = str(close_side or "").strip().lower()
    for order in list(open_orders or []):
        if str((order or {}).get("symbol") or "").strip().upper() != symbol:
            continue
        if str((order or {}).get("side") or "").strip().lower() != expected_side:
            continue
        if str((order or {}).get("status") or "").strip().lower() not in final_statuses:
            return True
    return False


@dataclass(frozen=True)
class PositionGuardSettings:
    stop_loss_percent: float = 4.0
    take_profit_percent: float = 8.0
    max_exits_per_cycle: int = 1

    def validate(self) -> None:
        if not 0 < float(self.stop_loss_percent) <= 25:
            raise ValueError("stop_loss_percent must be > 0 and <= 25")
        if not 0 < float(self.take_profit_percent) <= 100:
            raise ValueError("take_profit_percent must be > 0 and <= 100")
        if int(self.max_exits_per_cycle) < 1:
            raise ValueError("max_exits_per_cycle must be at least 1")


def review_paper_positions(
    positions: dict[str, dict[str, Any]] | None,
    open_orders: list[dict[str, Any]] | None,
    settings: PositionGuardSettings,
    *,
    entry_contexts: dict[str, dict[str, Any]] | None = None,
    current_timestamp: Any | None = None,
    trading_mode: str = "PAPER",
    peak_return_state: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Evaluate PAPER positions using broker prices; this function never submits orders."""
    settings.validate()
    reviews: list[dict[str, Any]] = []
    exit_candidates: list[dict[str, Any]] = []
    attribution_was_checked = entry_contexts is not None
    contexts = dict(entry_contexts or {})
    peak_state = peak_return_state
    observed_peak_keys: set[str] = set()
    reviewed_at = current_timestamp or datetime.now(timezone.utc).isoformat()

    for raw_symbol, raw_position in sorted(dict(positions or {}).items()):
        symbol = str(raw_symbol or "").strip().upper()
        position = dict(raw_position or {})
        asset_class = str(position.get("asset_class") or "").strip().lower()
        if asset_class == "crypto" or "/" in symbol:
            # Crypto has a separate 24/7 guard with crypto-specific stop/target settings.
            continue
        if asset_class in {"us_option", "option"}:
            # Options have a separate expiry/Greeks-aware guard and must not be treated as shares.
            continue
        signed_quantity = _as_float(position.get("quantity"), 0.0)
        quantity = abs(signed_quantity)
        position_side = "SHORT" if signed_quantity < 0 else "LONG"
        close_side = "BUY" if position_side == "SHORT" else "SELL"
        entry_price = _as_float(position.get("avg_price"), 0.0)
        current_price = _as_float(position.get("current_price"), 0.0)
        if current_price <= 0 and quantity > 0:
            current_price = abs(_as_float(position.get("market_value"), 0.0)) / quantity

        if not symbol or quantity <= 0:
            continue

        if entry_price > 0 and current_price > 0:
            return_percent = (
                ((entry_price - current_price) / entry_price) * 100.0
                if position_side == "SHORT"
                else ((current_price / entry_price) - 1.0) * 100.0
            )
        else:
            return_percent = None
        entry_context = dict(contexts.get(symbol) or {})
        if attribution_was_checked:
            entry_context.setdefault("attribution_checked", True)
            entry_context.setdefault("bot_entry_attributed", False)
            entry_context.setdefault("bot_entry_confirmed", False)
        strategy_id = str(entry_context.get("strategy_id") or position.get("strategy_id") or "").strip()
        peak_key = "|".join(
            [
                symbol,
                position_side,
                str(entry_context.get("entry_timestamp") or entry_price),
            ]
        )
        observed_peak_keys.add(peak_key)
        trusted_peak_values = [
            value
            for value in (
                entry_context.get("peak_return_percent"),
                position.get("peak_return_percent"),
                peak_state.get(peak_key) if peak_state is not None else None,
            )
            if value is not None
        ]
        peak_return = (
            max(float(return_percent or 0.0), *[_as_float(value, 0.0) for value in trusted_peak_values])
            if trusted_peak_values
            else None
        )
        recommendation = "HOLD"
        reason = "inside_exit_bands"
        warnings: list[str] = []
        priority = 99
        policy_result: dict[str, Any] = {
            "profile_id": "not_evaluated",
            "strategy_profile_applied": False,
            "attribution_reason": str(entry_context.get("attribution_reason") or "not_checked"),
            "stop_loss_percent": float(settings.stop_loss_percent),
            "take_profit_percent": float(settings.take_profit_percent),
            "peak_return_percent": peak_return,
            "trailing_state_status": "available" if peak_return is not None else "unavailable",
            "holding_sessions": 0,
        }

        if _has_open_close(list(open_orders or []), symbol, close_side):
            recommendation = "EXIT_PENDING"
            reason = "open_close_order_exists"
        elif entry_price <= 0 or current_price <= 0:
            recommendation = "REVIEW_REQUIRED"
            reason = "missing_entry_or_current_price"
            warnings.append("automatic_exit_blocked_missing_price")
        else:
            policy_context = {
                **entry_context,
                "trend_failure": bool(entry_context.get("trend_failure") or position.get("trend_failure")),
                "bearish_trend_reversal": bool(
                    entry_context.get("bearish_trend_reversal") or position.get("bearish_trend_reversal")
                ),
            }
            policy_result = evaluate_stock_exit(
                strategy_id=strategy_id,
                position_side=position_side,
                return_percent=float(return_percent or 0.0),
                entry_price=entry_price,
                current_price=current_price,
                entry_context=policy_context,
                current_timestamp=reviewed_at,
                peak_return_percent=peak_return,
                legacy_stop_loss_percent=float(settings.stop_loss_percent),
                legacy_take_profit_percent=float(settings.take_profit_percent),
                trading_mode=trading_mode,
            )
            recommendation = str(policy_result.get("recommendation") or "HOLD")
            reason = str(policy_result.get("exit_reason") or "inside_exit_bands")
            raw_priority = policy_result.get("priority")
            priority = int(raw_priority if raw_priority is not None else 99)
            if reason == "unattributed_position_manual_review_required":
                warnings.append("automatic_exit_blocked_unattributed_position")

        review = {
            "symbol": symbol,
            "current_quantity": round(signed_quantity, 8),
            "position_side": position_side,
            "close_side": close_side,
            "current_entry_price": round(entry_price, 8),
            "current_market_price": round(current_price, 8),
            "return_percent": round(float(return_percent), 6) if return_percent is not None else None,
            "score": None,
            "confidence": 100.0 if return_percent is not None else 0.0,
            "strategy_id": strategy_id or "unattributed",
            "recommendation": recommendation,
            "exit_reason": reason,
            "reasons": [reason],
            "warnings": warnings,
            "stop_loss_percent": float(policy_result.get("stop_loss_percent") or settings.stop_loss_percent),
            "take_profit_percent": float(policy_result.get("take_profit_percent") or settings.take_profit_percent),
            "exit_profile": {
                key: policy_result.get(key)
                for key in (
                    "profile_id",
                    "strategy_profile_applied",
                    "attribution_reason",
                    "stop_loss_percent",
                    "take_profit_percent",
                    "stop_loss_source",
                    "take_profit_source",
                    "recorded_stop_loss_percent",
                    "recorded_take_profit_percent",
                    "trailing_activation_percent",
                    "trailing_distance_percent",
                    "peak_return_percent",
                    "trailing_state_status",
                    "holding_sessions",
                    "maximum_holding_sessions",
                    "mean_target_price",
                )
            },
        }
        reviews.append(review)
        if recommendation.startswith("CLOSE_"):
            exit_candidates.append({**review, "quantity": round(quantity, 8), "priority": priority})

    if peak_state is not None:
        for stale_key in set(peak_state).difference(observed_peak_keys):
            peak_state.pop(stale_key, None)

    exit_candidates.sort(
        key=lambda item: (
            int(item.get("priority") or 0),
            float(item.get("return_percent") or 0.0),
            str(item.get("symbol") or ""),
        )
    )
    selected_exits = exit_candidates[: int(settings.max_exits_per_cycle)]
    return {
        "reviews": reviews,
        "exit_candidates": selected_exits,
        "summary": {
            "positions_reviewed": len(reviews),
            "exit_candidates": len(exit_candidates),
            "selected_exits": len(selected_exits),
            "stop_loss_candidates": sum(1 for item in exit_candidates if item.get("recommendation") == "CLOSE_STOP_LOSS"),
            "take_profit_candidates": sum(1 for item in exit_candidates if item.get("recommendation") == "CLOSE_TAKE_PROFIT"),
            "strategy_exit_candidates": sum(
                1
                for item in exit_candidates
                if item.get("recommendation") in {"CLOSE_STRATEGY_EXIT", "CLOSE_TIME_STOP"}
            ),
            "blocked_or_pending": sum(
                1 for item in reviews if item.get("recommendation") in {"EXIT_PENDING", "REVIEW_REQUIRED"}
            ),
        },
    }
