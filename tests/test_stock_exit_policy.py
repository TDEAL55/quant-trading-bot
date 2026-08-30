from stock_exit_policy import evaluate_stock_exit


def _context(strategy_id, **overrides):
    payload = {
        "attribution_checked": True,
        "bot_entry_attributed": True,
        "bot_entry_confirmed": True,
        "strategy_id": strategy_id,
        "entry_timestamp": "2026-08-03T14:00:00+00:00",
        "strategy": {"strategy_id": strategy_id},
    }
    payload.update(overrides)
    return payload


def _evaluate(strategy_id, *, side="LONG", return_percent=0, price=100, context=None, peak=None, now=None):
    return evaluate_stock_exit(
        strategy_id=strategy_id,
        position_side=side,
        return_percent=return_percent,
        entry_price=100,
        current_price=price,
        entry_context=context if context is not None else _context(strategy_id),
        current_timestamp=now or "2026-08-03T20:00:00+00:00",
        peak_return_percent=peak,
        legacy_stop_loss_percent=4,
        legacy_take_profit_percent=8,
        trading_mode="PAPER",
    )


def test_trend_profile_allows_wider_fallback_stop_and_trails_a_winner():
    inside = _evaluate("stock_trend_ensemble_v2", return_percent=-5, price=95)
    trailing = _evaluate("stock_trend_ensemble_v2", return_percent=5.5, price=105.5, peak=10)

    assert inside["recommendation"] == "HOLD"
    assert inside["profile_id"] == "trend_relative_strength_v2"
    assert inside["stop_loss_percent"] == 6
    assert trailing["recommendation"] == "CLOSE_TAKE_PROFIT"
    assert trailing["exit_reason"] == "trend_trailing_stop_reached"


def test_trend_profile_honors_recorded_entry_stop_and_target():
    context = _context(
        "stock_trend_ensemble_v2",
        strategy={
            "strategy_id": "stock_trend_ensemble_v2",
            "stop": 95,
            "target_or_exit_rule": "exit on trend failure; initial target 110.0000",
        },
    )

    stopped = _evaluate(
        "stock_trend_ensemble_v2",
        return_percent=-5,
        price=95,
        context=context,
    )
    targeted = _evaluate(
        "stock_trend_ensemble_v2",
        return_percent=10,
        price=110,
        context=context,
        peak=10,
    )

    assert stopped["recommendation"] == "CLOSE_STOP_LOSS"
    assert stopped["stop_loss_source"] == "recorded_entry_signal"
    assert targeted["recommendation"] == "CLOSE_TAKE_PROFIT"
    assert targeted["take_profit_source"] == "recorded_entry_signal"


def test_trend_failure_is_an_explicit_strategy_exit():
    decision = _evaluate(
        "stock_trend_ensemble_v2",
        return_percent=1,
        price=101,
        context=_context("stock_trend_ensemble_v2", trend_failure=True),
    )

    assert decision["recommendation"] == "CLOSE_STRATEGY_EXIT"
    assert decision["exit_reason"] == "trend_failure_detected"


def test_mean_reversion_exits_at_entry_mean_and_after_five_sessions():
    context = _context(
        "stock_mean_reversion_v2",
        mean_target_price=102,
    )
    at_mean = _evaluate(
        "stock_mean_reversion_v2",
        return_percent=2,
        price=102,
        context=context,
    )
    timed_out = _evaluate(
        "stock_mean_reversion_v2",
        return_percent=1,
        price=101,
        context={**context, "mean_target_price": 104},
        now="2026-08-10T20:00:00+00:00",
    )

    assert at_mean["recommendation"] == "CLOSE_TAKE_PROFIT"
    assert at_mean["exit_reason"] == "mean_reversion_target_reached"
    assert timed_out["holding_sessions"] == 5
    assert timed_out["recommendation"] == "CLOSE_TIME_STOP"
    assert timed_out["exit_reason"] == "mean_reversion_time_stop_reached"


def test_bearish_profile_is_short_only_and_direction_safe():
    stopped = _evaluate(
        "stock_bearish_trend_v2",
        side="SHORT",
        return_percent=-4,
        price=104,
    )
    trailing = _evaluate(
        "stock_bearish_trend_v2",
        side="SHORT",
        return_percent=3,
        price=97,
        peak=7,
    )
    wrong_side = _evaluate(
        "stock_bearish_trend_v2",
        side="LONG",
        return_percent=-5,
        price=95,
    )

    assert stopped["exit_reason"] == "bearish_short_stop_reached"
    assert trailing["exit_reason"] == "bearish_trailing_stop_reached"
    assert wrong_side["strategy_profile_applied"] is False
    assert wrong_side["exit_reason"] == "stop_loss_threshold_reached"


def test_unattributed_position_requires_manual_review_not_auto_exit():
    decision = _evaluate(
        "stock_trend_ensemble_v2",
        return_percent=-20,
        price=80,
        context={
            "attribution_checked": True,
            "bot_entry_attributed": False,
            "bot_entry_confirmed": False,
        },
    )

    assert decision["recommendation"] == "REVIEW_REQUIRED"
    assert decision["exit_reason"] == "unattributed_position_manual_review_required"


def test_confirmed_legacy_bot_entry_keeps_deterministic_default_bands():
    decision = _evaluate(
        "trend_momentum_v1",
        return_percent=-4,
        price=96,
        context={
            "attribution_checked": True,
            "bot_entry_attributed": True,
            "bot_entry_confirmed": False,
            "strategy_id": "trend_momentum_v1",
            "strategy": {"strategy_id": "trend_momentum_v1"},
        },
    )

    assert decision["profile_id"] == "legacy_default"
    assert decision["recommendation"] == "CLOSE_STOP_LOSS"
    assert decision["exit_reason"] == "stop_loss_threshold_reached"


def test_strategy_exit_policy_is_disabled_outside_paper():
    decision = evaluate_stock_exit(
        strategy_id="stock_trend_ensemble_v2",
        position_side="LONG",
        return_percent=-20,
        entry_price=100,
        current_price=80,
        entry_context=_context("stock_trend_ensemble_v2"),
        current_timestamp="2026-08-03T20:00:00+00:00",
        peak_return_percent=None,
        legacy_stop_loss_percent=4,
        legacy_take_profit_percent=8,
        trading_mode="LIVE",
    )

    assert decision["recommendation"] == "REVIEW_REQUIRED"
    assert decision["exit_reason"] == "paper_exit_policy_disabled_outside_paper"
