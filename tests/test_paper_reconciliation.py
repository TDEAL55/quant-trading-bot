from paper_reconciliation import reconcile_paper_positions


def test_reconciliation_exact_match():
    result = reconcile_paper_positions(
        planned_positions={"AAA": {"quantity": 10, "weight": 0.2}},
        actual_positions={"AAA": {"quantity": 10, "weight": 0.2}},
        expected_cash=1000,
        actual_cash=1000,
        expected_buying_power=1000,
        actual_buying_power=1000,
        orders=[],
        tolerance=0.01,
    )
    assert result["reconciliation_status"] == "matched"
    assert result["position_mismatch_count"] == 0


def test_reconciliation_pending_and_failed():
    result = reconcile_paper_positions(
        planned_positions={"AAA": {"quantity": 10, "weight": 0.2}},
        actual_positions={"AAA": {"quantity": 9, "weight": 0.18}},
        expected_cash=1000,
        actual_cash=900,
        expected_buying_power=1000,
        actual_buying_power=900,
        orders=[
            {"submission_status": "pending", "filled_quantity": 0, "quantity": 1},
            {"submission_status": "failed", "filled_quantity": 0, "quantity": 1},
        ],
        tolerance=0.01,
    )
    assert result["failed_order_count"] == 1
    assert result["reconciliation_status"] in {"failed", "pending", "mismatch"}


def test_reconciliation_failing_scenario_covers_all_mismatch_types():
    result = reconcile_paper_positions(
        planned_positions={
            "AAA": {"quantity": 10, "weight": 0.25},
            "BBB": {"quantity": 5, "weight": 0.10},
            "CCC": {"quantity": 8, "weight": 0.20},
        },
        actual_positions={
            "AAA": {"quantity": 9, "weight": 0.23},
            "CCC": {"quantity": 8, "weight": 0.20},
            "ZZZ": {"quantity": 2, "weight": 0.04},
        },
        expected_cash=1000,
        actual_cash=900,
        expected_buying_power=1000,
        actual_buying_power=850,
        orders=[
            {"submission_status": "submitted", "filled_quantity": 1, "quantity": 3},
            {"submission_status": "pending", "filled_quantity": 0, "quantity": 2},
            {"submission_status": "failed", "filled_quantity": 0, "quantity": 1},
        ],
        tolerance=0.0001,
    )
    symbols = {item["symbol"] for item in result["mismatches"]}
    assert result["reconciliation_status"] == "failed"
    assert result["position_mismatch_count"] >= 2
    assert "AAA" in symbols
    assert "BBB" in symbols
    assert "ZZZ" in symbols
    assert result["cash_difference"] == -100.0
    assert result["buying_power_difference"] == -150.0
    assert result["unfilled_order_count"] == 2
    assert result["failed_order_count"] == 1
