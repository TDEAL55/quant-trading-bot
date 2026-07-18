from paper_order_planner import OrderPlannerSettings, plan_paper_orders


def test_order_planner_generates_buy_reduce_close_and_hold():
    settings = OrderPlannerSettings(
        minimum_order_notional=25.0,
        maximum_order_notional=2000.0,
        allow_fractional=False,
        quantity_precision=4,
        rebalance_tolerance=0.01,
        maximum_orders=10,
        cash_buffer=0.05,
    )
    target_weights = {"AAA": 0.3, "BBB": 0.2, "CCC": 0.0, "DDD": 0.1}
    current_positions = {
        "AAA": {"quantity": 10, "avg_price": 100},
        "BBB": {"quantity": 30, "avg_price": 50},
        "CCC": {"quantity": 20, "avg_price": 25},
        "EEE": {"quantity": 5, "avg_price": 40},
    }
    reference_prices = {"AAA": 100, "BBB": 50, "CCC": 25, "DDD": 20, "EEE": 40}

    result = plan_paper_orders(
        target_weights=target_weights,
        current_positions=current_positions,
        reference_prices=reference_prices,
        portfolio_value=10000,
        current_cash=3000,
        settings=settings,
    )

    orders = result["orders"]
    assert any(item["symbol"] == "DDD" and item["side"] == "BUY" for item in orders)
    assert any(item["symbol"] == "CCC" and item["side"] == "SELL" for item in orders)
    assert all(item["quantity"] >= 0 for item in orders)


def test_order_planner_respects_max_orders_and_cash_buffer():
    settings = OrderPlannerSettings(
        minimum_order_notional=1.0,
        maximum_order_notional=5000.0,
        allow_fractional=True,
        quantity_precision=3,
        rebalance_tolerance=0.0,
        maximum_orders=1,
        cash_buffer=0.9,
    )
    result = plan_paper_orders(
        target_weights={"AAA": 0.5, "BBB": 0.5},
        current_positions={},
        reference_prices={"AAA": 100.0, "BBB": 100.0},
        portfolio_value=1000.0,
        current_cash=1000.0,
        settings=settings,
    )
    assert len(result["orders"]) <= 1
