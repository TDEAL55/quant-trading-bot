from strategy_costs import apply_transaction_costs, apply_transaction_costs_to_snapshot, estimate_transaction_cost


def test_estimate_transaction_cost_non_negative():
    cost = estimate_transaction_cost(holding_count=5, commission_per_trade=0.0, fixed_rebalance_cost=0.0, turnover_cost_bps=10.0, slippage_bps=5.0, turnover=0.5)
    assert cost >= 0.0


def test_apply_transaction_costs_to_snapshot_adds_net_fields():
    snapshot = {
        "portfolio_return": 0.02,
        "benchmark_return": 0.01,
        "turnover": 0.2,
        "selected_count": 5,
    }
    result = apply_transaction_costs_to_snapshot(snapshot, {"commission_per_trade": 0.0, "fixed_rebalance_cost": 0.0, "turnover_cost_bps": 0.0, "slippage_bps": 0.0})
    assert "net_portfolio_return" in result
    assert "net_excess_return" in result


def test_apply_transaction_costs_operates_on_list():
    snapshots = [{"portfolio_return": 0.01, "benchmark_return": 0.0, "turnover": 0.1, "selected_count": 3}]
    result = apply_transaction_costs(snapshots, {"commission_per_trade": 0.0, "fixed_rebalance_cost": 0.0, "turnover_cost_bps": 0.0, "slippage_bps": 0.0})
    assert len(result) == 1
    assert result[0]["net_portfolio_return"] == 0.01
