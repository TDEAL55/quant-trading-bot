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
