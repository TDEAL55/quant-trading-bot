from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
) -> dict[str, Any]:
    """Evaluate PAPER positions using broker prices; this function never submits orders."""
    settings.validate()
    reviews: list[dict[str, Any]] = []
    exit_candidates: list[dict[str, Any]] = []

    for raw_symbol, raw_position in sorted(dict(positions or {}).items()):
        symbol = str(raw_symbol or "").strip().upper()
        position = dict(raw_position or {})
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
        recommendation = "HOLD"
        reason = "inside_exit_bands"
        warnings: list[str] = []
        priority = 99

        if _has_open_close(list(open_orders or []), symbol, close_side):
            recommendation = "EXIT_PENDING"
            reason = "open_close_order_exists"
        elif entry_price <= 0 or current_price <= 0:
            recommendation = "REVIEW_REQUIRED"
            reason = "missing_entry_or_current_price"
            warnings.append("automatic_exit_blocked_missing_price")
        elif float(return_percent or 0.0) <= -abs(float(settings.stop_loss_percent)):
            recommendation = "CLOSE_STOP_LOSS"
            reason = "stop_loss_threshold_reached"
            priority = 0
        elif float(return_percent or 0.0) >= abs(float(settings.take_profit_percent)):
            recommendation = "CLOSE_TAKE_PROFIT"
            reason = "take_profit_threshold_reached"
            priority = 1

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
            "recommendation": recommendation,
            "exit_reason": reason,
            "reasons": [reason],
            "warnings": warnings,
            "stop_loss_percent": float(settings.stop_loss_percent),
            "take_profit_percent": float(settings.take_profit_percent),
        }
        reviews.append(review)
        if recommendation.startswith("CLOSE_"):
            exit_candidates.append({**review, "quantity": round(quantity, 8), "priority": priority})

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
            "blocked_or_pending": sum(
                1 for item in reviews if item.get("recommendation") in {"EXIT_PENDING", "REVIEW_REQUIRED"}
            ),
        },
    }
