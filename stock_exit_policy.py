from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import re
from typing import Any


TREND_STRATEGY_ID = "stock_trend_ensemble_v2"
MEAN_REVERSION_STRATEGY_ID = "stock_mean_reversion_v2"
BEARISH_STRATEGY_ID = "stock_bearish_trend_v2"
SUPPORTED_STOCK_EXIT_STRATEGIES = frozenset(
    {
        TREND_STRATEGY_ID,
        MEAN_REVERSION_STRATEGY_ID,
        BEARISH_STRATEGY_ID,
    }
)


@dataclass(frozen=True)
class StockExitProfile:
    profile_id: str
    strategy_id: str
    expected_side: str
    stop_loss_percent: float
    take_profit_percent: float
    trailing_activation_percent: float | None = None
    trailing_distance_percent: float | None = None
    maximum_holding_sessions: int | None = None


STRATEGY_EXIT_PROFILES: dict[str, StockExitProfile] = {
    TREND_STRATEGY_ID: StockExitProfile(
        profile_id="trend_relative_strength_v2",
        strategy_id=TREND_STRATEGY_ID,
        expected_side="LONG",
        stop_loss_percent=6.0,
        take_profit_percent=18.0,
        trailing_activation_percent=8.0,
        trailing_distance_percent=4.0,
    ),
    MEAN_REVERSION_STRATEGY_ID: StockExitProfile(
        profile_id="mean_reversion_v2",
        strategy_id=MEAN_REVERSION_STRATEGY_ID,
        expected_side="LONG",
        stop_loss_percent=3.0,
        take_profit_percent=5.0,
        maximum_holding_sessions=5,
    ),
    BEARISH_STRATEGY_ID: StockExitProfile(
        profile_id="bearish_trend_paper_v2",
        strategy_id=BEARISH_STRATEGY_ID,
        expected_side="SHORT",
        # Short losses are unbounded, so this sleeve does not receive the wider
        # long-trend stop even in PAPER.
        stop_loss_percent=4.0,
        take_profit_percent=12.0,
        trailing_activation_percent=6.0,
        trailing_distance_percent=3.0,
    ),
}


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _as_datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def business_sessions_elapsed(entry_timestamp: Any, current_timestamp: Any) -> int:
    """Approximate completed US sessions using weekdays without intraday shortcuts."""
    entry = _as_datetime(entry_timestamp)
    current = _as_datetime(current_timestamp)
    if entry is None or current is None or current <= entry:
        return 0

    sessions = 0
    cursor = entry.date() + timedelta(days=1)
    while cursor <= current.date():
        if cursor.weekday() < 5:
            sessions += 1
        cursor += timedelta(days=1)
    return sessions


def _mean_target_price(entry_context: dict[str, Any]) -> float:
    explicit = _as_float(entry_context.get("mean_target_price"), 0.0)
    if explicit > 0:
        return explicit

    strategy = dict(entry_context.get("strategy") or {})
    explicit = _as_float(strategy.get("mean_target_price"), 0.0)
    if explicit > 0:
        return explicit

    supporting = dict(strategy.get("supporting_factors") or {})
    factor_values = dict(supporting.get("factor_values") or {})
    trend_values = dict(factor_values.get("trend_strength") or {})
    return max(_as_float(trend_values.get("ema20"), 0.0), 0.0)


def _recorded_risk_percentages(
    entry_context: dict[str, Any],
    *,
    position_side: str,
    entry_price: float,
) -> tuple[float | None, float | None]:
    strategy = dict(entry_context.get("strategy") or {})
    base = max(float(entry_price), 0.0)
    if base <= 0:
        return None, None

    stop_price = _as_float(entry_context.get("stop_price") or strategy.get("stop"), 0.0)
    target_price = _as_float(entry_context.get("target_price") or strategy.get("target"), 0.0)
    if target_price <= 0:
        text = str(strategy.get("target_or_exit_rule") or "")
        match = re.search(r"initial\s+target\s+([0-9]+(?:\.[0-9]+)?)", text, flags=re.IGNORECASE)
        target_price = _as_float(match.group(1), 0.0) if match else 0.0

    if str(position_side or "").strip().upper() == "SHORT":
        stop_percent = ((stop_price / base) - 1.0) * 100.0 if stop_price > base else None
        target_percent = (1.0 - (target_price / base)) * 100.0 if 0 < target_price < base else None
    else:
        stop_percent = (1.0 - (stop_price / base)) * 100.0 if 0 < stop_price < base else None
        target_percent = ((target_price / base) - 1.0) * 100.0 if target_price > base else None

    valid_stop = round(float(stop_percent), 8) if stop_percent is not None and 0 < stop_percent <= 25 else None
    valid_target = round(float(target_percent), 8) if target_percent is not None and 0 < target_percent <= 100 else None
    return valid_stop, valid_target


def _legacy_profile(stop_loss_percent: float, take_profit_percent: float) -> StockExitProfile:
    return StockExitProfile(
        profile_id="legacy_default",
        strategy_id="legacy_or_unattributed",
        expected_side="ANY",
        stop_loss_percent=abs(float(stop_loss_percent)),
        take_profit_percent=abs(float(take_profit_percent)),
    )


def resolve_stock_exit_profile(
    *,
    strategy_id: str,
    position_side: str,
    bot_entry_confirmed: bool,
    trading_mode: str,
    legacy_stop_loss_percent: float,
    legacy_take_profit_percent: float,
) -> tuple[StockExitProfile, bool, str]:
    """Return a v2 profile only for an attributed PAPER entry with the expected side."""
    fallback = _legacy_profile(legacy_stop_loss_percent, legacy_take_profit_percent)
    normalized_strategy = str(strategy_id or "").strip()
    normalized_side = str(position_side or "").strip().upper()
    if str(trading_mode or "").strip().upper() != "PAPER":
        return fallback, False, "strategy_exit_profiles_are_paper_only"
    if not bool(bot_entry_confirmed):
        return fallback, False, "bot_entry_not_confirmed"

    profile = STRATEGY_EXIT_PROFILES.get(normalized_strategy)
    if profile is None:
        return fallback, False, "legacy_or_unknown_strategy"
    if normalized_side != profile.expected_side:
        return fallback, False, "strategy_position_side_mismatch"
    return profile, True, "confirmed_strategy_profile"


def evaluate_stock_exit(
    *,
    strategy_id: str,
    position_side: str,
    return_percent: float,
    entry_price: float,
    current_price: float,
    entry_context: dict[str, Any] | None,
    current_timestamp: Any,
    peak_return_percent: float | None,
    legacy_stop_loss_percent: float,
    legacy_take_profit_percent: float,
    trading_mode: str = "PAPER",
) -> dict[str, Any]:
    """Evaluate one direction-normalized stock return against its exit profile.

    ``return_percent`` is positive for a profitable long or short and negative
    for a losing long or short. This keeps the bearish rules inverse-safe.
    """
    context = dict(entry_context or {})
    profile, profile_applied, attribution_reason = resolve_stock_exit_profile(
        strategy_id=strategy_id,
        position_side=position_side,
        bot_entry_confirmed=bool(context.get("bot_entry_confirmed", False)),
        trading_mode=trading_mode,
        legacy_stop_loss_percent=legacy_stop_loss_percent,
        legacy_take_profit_percent=legacy_take_profit_percent,
    )
    attribution_checked = bool(context.get("attribution_checked", False))
    bot_entry_attributed = bool(context.get("bot_entry_attributed", False))
    recorded_stop, recorded_target = _recorded_risk_percentages(
        context,
        position_side=position_side,
        entry_price=entry_price,
    )
    if bot_entry_attributed and recorded_stop is not None:
        profile = replace(profile, stop_loss_percent=recorded_stop)
    if bot_entry_attributed and recorded_target is not None:
        profile = replace(profile, take_profit_percent=recorded_target)
    current_return = float(return_percent)
    has_trusted_peak = peak_return_percent is not None
    peak_return = max(current_return, _as_float(peak_return_percent, current_return))
    holding_sessions = business_sessions_elapsed(context.get("entry_timestamp"), current_timestamp)

    decision = {
        "recommendation": "HOLD",
        "exit_reason": "inside_exit_bands",
        "priority": 99,
        "strategy_id": str(strategy_id or "") or "unattributed",
        "profile_id": profile.profile_id,
        "strategy_profile_applied": profile_applied,
        "attribution_reason": attribution_reason,
        "stop_loss_percent": profile.stop_loss_percent,
        "take_profit_percent": profile.take_profit_percent,
        "trailing_activation_percent": profile.trailing_activation_percent,
        "trailing_distance_percent": profile.trailing_distance_percent,
        "peak_return_percent": round(peak_return, 6),
        "trailing_state_status": "available" if has_trusted_peak else "unavailable",
        "holding_sessions": holding_sessions,
        "maximum_holding_sessions": profile.maximum_holding_sessions,
        "mean_target_price": None,
        "recorded_stop_loss_percent": round(recorded_stop, 6) if recorded_stop is not None else None,
        "recorded_take_profit_percent": round(recorded_target, 6) if recorded_target is not None else None,
        "stop_loss_source": "recorded_entry_signal" if bot_entry_attributed and recorded_stop is not None else "profile_default",
        "take_profit_source": "recorded_entry_signal" if bot_entry_attributed and recorded_target is not None else "profile_default",
    }

    if str(trading_mode or "").strip().upper() != "PAPER":
        decision.update(
            recommendation="REVIEW_REQUIRED",
            exit_reason="paper_exit_policy_disabled_outside_paper",
        )
        return decision

    if attribution_checked and not bot_entry_attributed:
        decision.update(
            recommendation="REVIEW_REQUIRED",
            exit_reason="unattributed_position_manual_review_required",
            attribution_reason="no_confirmed_bot_entry",
        )
        return decision

    if current_return <= -abs(profile.stop_loss_percent):
        decision.update(
            recommendation="CLOSE_STOP_LOSS",
            exit_reason=(
                "trend_hard_stop_reached"
                if profile.strategy_id == TREND_STRATEGY_ID
                else "mean_reversion_stop_reached"
                if profile.strategy_id == MEAN_REVERSION_STRATEGY_ID
                else "bearish_short_stop_reached"
                if profile.strategy_id == BEARISH_STRATEGY_ID
                else "stop_loss_threshold_reached"
            ),
            priority=0,
        )
        return decision

    if profile_applied and profile.strategy_id == TREND_STRATEGY_ID and bool(context.get("trend_failure", False)):
        decision.update(recommendation="CLOSE_STRATEGY_EXIT", exit_reason="trend_failure_detected", priority=1)
        return decision

    if profile_applied and profile.strategy_id == BEARISH_STRATEGY_ID and bool(
        context.get("bearish_trend_reversal", False)
    ):
        decision.update(
            recommendation="CLOSE_STRATEGY_EXIT",
            exit_reason="bearish_trend_reversal_detected",
            priority=1,
        )
        return decision

    trailing_activation = profile.trailing_activation_percent
    trailing_distance = profile.trailing_distance_percent
    if (
        profile_applied
        and has_trusted_peak
        and trailing_activation is not None
        and trailing_distance is not None
        and peak_return >= float(trailing_activation)
        and (peak_return - current_return) >= float(trailing_distance)
    ):
        decision.update(
            recommendation="CLOSE_TAKE_PROFIT",
            exit_reason=(
                "bearish_trailing_stop_reached"
                if profile.strategy_id == BEARISH_STRATEGY_ID
                else "trend_trailing_stop_reached"
            ),
            priority=1,
        )
        return decision

    if profile_applied and profile.strategy_id == MEAN_REVERSION_STRATEGY_ID:
        mean_target = _mean_target_price(context)
        decision["mean_target_price"] = round(mean_target, 8) if mean_target > 0 else None
        if mean_target > float(entry_price) and float(current_price) >= mean_target:
            decision.update(
                recommendation="CLOSE_TAKE_PROFIT",
                exit_reason="mean_reversion_target_reached",
                priority=1,
            )
            return decision
        if profile.maximum_holding_sessions is not None and holding_sessions >= profile.maximum_holding_sessions:
            decision.update(
                recommendation="CLOSE_TIME_STOP",
                exit_reason="mean_reversion_time_stop_reached",
                priority=2,
            )
            return decision

    if current_return >= abs(profile.take_profit_percent):
        decision.update(
            recommendation="CLOSE_TAKE_PROFIT",
            exit_reason=(
                "trend_profit_cap_reached"
                if profile.strategy_id == TREND_STRATEGY_ID
                else "mean_reversion_profit_target_reached"
                if profile.strategy_id == MEAN_REVERSION_STRATEGY_ID
                else "bearish_profit_target_reached"
                if profile.strategy_id == BEARISH_STRATEGY_ID
                else "take_profit_threshold_reached"
            ),
            priority=2,
        )
    return decision
