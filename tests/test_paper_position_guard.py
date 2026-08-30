from paper_position_guard import PositionGuardSettings, review_paper_positions


def test_guard_selects_stop_before_target_and_respects_cycle_limit():
    result = review_paper_positions(
        positions={
            "LOSS": {"quantity": 2, "avg_price": 100, "current_price": 95},
            "WIN": {"quantity": 3, "avg_price": 100, "current_price": 109},
            "HOLD": {"quantity": 1, "avg_price": 100, "current_price": 102},
        },
        open_orders=[],
        settings=PositionGuardSettings(stop_loss_percent=4, take_profit_percent=8, max_exits_per_cycle=1),
    )

    assert result["summary"]["positions_reviewed"] == 3
    assert result["summary"]["exit_candidates"] == 2
    assert result["exit_candidates"][0]["symbol"] == "LOSS"
    assert result["exit_candidates"][0]["recommendation"] == "CLOSE_STOP_LOSS"
    assert next(row for row in result["reviews"] if row["symbol"] == "HOLD")["recommendation"] == "HOLD"


def test_guard_uses_market_value_fallback_and_blocks_duplicate_sell():
    result = review_paper_positions(
        positions={"JPM": {"quantity": 10, "avg_price": 100, "current_price": 0, "market_value": 900}},
        open_orders=[{"symbol": "JPM", "side": "sell", "status": "accepted"}],
        settings=PositionGuardSettings(),
    )

    review = result["reviews"][0]
    assert review["current_market_price"] == 90.0
    assert review["recommendation"] == "EXIT_PENDING"
    assert result["exit_candidates"] == []


def test_guard_fails_closed_when_price_is_missing():
    result = review_paper_positions(
        positions={"AAA": {"quantity": 1, "avg_price": 100}},
        open_orders=[],
        settings=PositionGuardSettings(),
    )

    assert result["reviews"][0]["recommendation"] == "REVIEW_REQUIRED"
    assert result["exit_candidates"] == []


def test_guard_reviews_short_returns_and_uses_buy_to_cover():
    result = review_paper_positions(
        positions={
            "SHORT_WIN": {"quantity": -2, "avg_price": 100, "current_price": 90},
            "SHORT_LOSS": {"quantity": -3, "avg_price": 100, "current_price": 105},
        },
        open_orders=[],
        settings=PositionGuardSettings(stop_loss_percent=4, take_profit_percent=8, max_exits_per_cycle=2),
    )

    by_symbol = {row["symbol"]: row for row in result["reviews"]}
    assert by_symbol["SHORT_WIN"]["recommendation"] == "CLOSE_TAKE_PROFIT"
    assert by_symbol["SHORT_WIN"]["close_side"] == "BUY"
    assert by_symbol["SHORT_WIN"]["return_percent"] == 10.0
    assert by_symbol["SHORT_LOSS"]["recommendation"] == "CLOSE_STOP_LOSS"
    assert by_symbol["SHORT_LOSS"]["return_percent"] == -5.0


def test_stock_guard_ignores_crypto_positions():
    result = review_paper_positions(
        positions={
            "BTC/USD": {
                "quantity": 0.5,
                "avg_price": 100.0,
                "current_price": 80.0,
                "market_value": 40.0,
                "asset_class": "crypto",
            }
        },
        open_orders=[],
        settings=PositionGuardSettings(stop_loss_percent=4, take_profit_percent=8, max_exits_per_cycle=1),
    )

    assert result["reviews"] == []
    assert result["exit_candidates"] == []


def _entry_context(strategy_id, **overrides):
    context = {
        "attribution_checked": True,
        "bot_entry_attributed": True,
        "bot_entry_confirmed": True,
        "strategy_id": strategy_id,
        "entry_timestamp": "2026-08-03T14:00:00+00:00",
        "strategy": {"strategy_id": strategy_id},
    }
    context.update(overrides)
    return context


def test_guard_applies_confirmed_mean_reversion_mean_target():
    result = review_paper_positions(
        positions={"MEAN": {"quantity": 2, "avg_price": 100, "current_price": 102}},
        open_orders=[],
        settings=PositionGuardSettings(),
        entry_contexts={
            "MEAN": _entry_context("stock_mean_reversion_v2", mean_target_price=102),
        },
        current_timestamp="2026-08-03T20:00:00+00:00",
    )

    review = result["reviews"][0]
    assert review["recommendation"] == "CLOSE_TAKE_PROFIT"
    assert review["exit_reason"] == "mean_reversion_target_reached"
    assert review["exit_profile"]["strategy_profile_applied"] is True
    assert review["exit_profile"]["mean_target_price"] == 102


def test_guard_gives_confirmed_trend_more_room_but_keeps_legacy_default():
    result = review_paper_positions(
        positions={
            "TREND": {"quantity": 2, "avg_price": 100, "current_price": 95},
            "LEGACY": {"quantity": 2, "avg_price": 100, "current_price": 95},
        },
        open_orders=[],
        settings=PositionGuardSettings(stop_loss_percent=4, take_profit_percent=8, max_exits_per_cycle=2),
        entry_contexts={
            "TREND": _entry_context("stock_trend_ensemble_v2"),
            "LEGACY": {
                **_entry_context("trend_momentum_v1"),
                "bot_entry_confirmed": False,
            },
        },
    )

    reviews = {row["symbol"]: row for row in result["reviews"]}
    assert reviews["TREND"]["recommendation"] == "HOLD"
    assert reviews["TREND"]["stop_loss_percent"] == 6
    assert reviews["TREND"]["exit_profile"]["trailing_state_status"] == "unavailable"
    assert reviews["LEGACY"]["recommendation"] == "CLOSE_STOP_LOSS"
    assert result["exit_candidates"][0]["symbol"] == "LEGACY"


def test_guard_blocks_automatic_exit_for_unattributed_manual_position():
    result = review_paper_positions(
        positions={"MANUAL": {"quantity": 2, "avg_price": 100, "current_price": 80}},
        open_orders=[],
        settings=PositionGuardSettings(),
        entry_contexts={"MANUAL": {"attribution_checked": True}},
    )

    review = result["reviews"][0]
    assert review["recommendation"] == "REVIEW_REQUIRED"
    assert review["exit_reason"] == "unattributed_position_manual_review_required"
    assert "automatic_exit_blocked_unattributed_position" in review["warnings"]
    assert result["exit_candidates"] == []


def test_guard_mean_reversion_time_stop_is_session_aware():
    result = review_paper_positions(
        positions={"MEAN": {"quantity": 2, "avg_price": 100, "current_price": 101}},
        open_orders=[],
        settings=PositionGuardSettings(),
        entry_contexts={
            "MEAN": _entry_context("stock_mean_reversion_v2", mean_target_price=104),
        },
        current_timestamp="2026-08-10T20:00:00+00:00",
    )

    review = result["reviews"][0]
    assert review["recommendation"] == "CLOSE_TIME_STOP"
    assert review["exit_reason"] == "mean_reversion_time_stop_reached"
    assert review["exit_profile"]["holding_sessions"] == 5
