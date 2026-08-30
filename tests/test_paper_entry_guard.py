from paper_entry_guard import evaluate_entry_exposure


def _review(**overrides):
    payload = {
        "symbol": "AAA",
        "side": "BUY",
        "planned_quantity": 10.0,
        "reference_price": 100.0,
        "portfolio_equity": 10_000.0,
        "allowed_position_percent": 10.0,
        "positions": {},
        "open_orders": [],
        "reservations": [],
        "same_cycle_orders": [],
    }
    payload.update(overrides)
    return evaluate_entry_exposure(**payload)


def test_baseline_ten_percent_caps_existing_plus_incremental_buy():
    result = _review(
        positions={"AAA": {"quantity": 8.0, "avg_price": 100.0}},
        planned_quantity=3.0,
    )

    assert result["approved"] is False
    assert result["reason"] == "symbol_concentration_limit"
    assert result["current_notional"] == 800.0
    assert result["planned_notional"] == 300.0
    assert result["projected_notional"] == 1100.0
    assert result["maximum_notional"] == 1000.0


def test_approved_confidence_tiers_preserve_fifteen_and_twenty_percent_caps():
    fifteen = _review(
        positions={"AAA": {"quantity": 8.0}},
        planned_quantity=7.0,
        allowed_position_percent=15.0,
    )
    twenty = _review(
        positions={"AAA": {"quantity": 15.0}},
        planned_quantity=5.0,
        allowed_position_percent=20.0,
    )

    assert fifteen["approved"] is True
    assert fifteen["projected_notional"] == fifteen["maximum_notional"] == 1500.0
    assert twenty["approved"] is True
    assert twenty["projected_notional"] == twenty["maximum_notional"] == 2000.0


def test_open_broker_entry_is_counted_and_blocks_duplicate():
    result = _review(
        planned_quantity=2.0,
        open_orders=[
            {
                "symbol": "AAA",
                "side": "buy",
                "status": "partially_filled",
                "requested_quantity": 5.0,
                "filled_quantity": 2.0,
            }
        ],
    )

    assert result["approved"] is False
    assert result["reason"] == "duplicate_entry_pending"
    assert result["pending_entry_quantity"] == 3.0
    assert result["duplicate_entry_count"] == 1


def test_opposite_side_open_broker_order_still_serializes_symbol():
    result = _review(
        planned_quantity=2.0,
        open_orders=[
            {
                "symbol": "AAA",
                "side": "sell",
                "status": "accepted",
                "requested_quantity": 2.0,
                "filled_quantity": 0.0,
            }
        ],
    )

    assert result["approved"] is False
    assert result["reason"] == "duplicate_entry_pending"
    assert result["pending_entry_quantity"] == 0.0
    assert result["duplicate_entry_count"] == 1


def test_same_cycle_planned_order_blocks_second_candidate_for_symbol():
    result = _review(
        planned_quantity=2.0,
        same_cycle_orders=[
            {"symbol": "AAA", "side": "BUY", "quantity": 2.0, "reference_price": 100.0}
        ],
    )

    assert result["approved"] is False
    assert result["reason"] == "duplicate_entry_pending"
    assert result["same_cycle_entry_notional"] == 200.0


def test_opposite_side_same_cycle_order_still_serializes_symbol():
    result = _review(
        planned_quantity=2.0,
        same_cycle_orders=[
            {"symbol": "AAA", "side": "SELL", "quantity": 2.0, "reference_price": 100.0}
        ],
    )

    assert result["approved"] is False
    assert result["reason"] == "duplicate_entry_pending"
    assert result["same_cycle_entry_notional"] == 0.0
    assert result["duplicate_entry_count"] == 1


def test_active_reservation_blocks_overlapping_cycle_even_under_cap():
    result = _review(
        planned_quantity=2.0,
        reservations=[
            {
                "reservation_id": "other",
                "symbol": "AAA",
                "side": "BUY",
                "status": "ACTIVE",
                "quantity": 2.0,
                "reference_price": 100.0,
            }
        ],
    )

    assert result["approved"] is False
    assert result["reason"] == "duplicate_entry_pending"
    assert result["reserved_entry_notional"] == 200.0


def test_buy_to_cover_only_counts_projected_long_exposure():
    result = _review(
        positions={"AAA": {"quantity": -8.0}},
        planned_quantity=10.0,
        allowed_position_percent=10.0,
    )

    assert result["approved"] is True
    assert result["projected_quantity"] == 2.0
    assert result["projected_notional"] == 200.0


def test_guard_fails_closed_when_exposure_input_or_active_order_size_is_unavailable():
    missing_orders = _review(open_orders=None)
    unquantified_order = _review(
        open_orders=[{"symbol": "AAA", "side": "BUY", "status": "accepted"}]
    )

    assert missing_orders["approved"] is False
    assert missing_orders["reason"] == "exposure_data_unavailable"
    assert unquantified_order["approved"] is False
    assert unquantified_order["reason"] == "exposure_data_unavailable"
