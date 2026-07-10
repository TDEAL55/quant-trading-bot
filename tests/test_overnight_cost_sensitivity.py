import pytest

from overnight_cost_sensitivity import (
    DEFAULT_COST_SCENARIOS_BPS,
    apply_round_trip_cost_to_trade,
    assert_cost_monotonicity,
    break_even_round_trip_cost_bps,
    evaluate_cost_scenarios,
)


def test_higher_costs_cannot_improve_compounded_net_return():
    gross_returns = [0.01, -0.004, 0.006, 0.0025, -0.003, 0.007]
    scenarios = [0.0, 2.0, 5.0, 10.0, 20.0]

    analysis = evaluate_cost_scenarios(gross_returns, scenarios_bps=scenarios, regulatory_fee_bps=0.0)

    compounded_values = [item["compounded_return"] for item in analysis["net_scenarios"]]
    for left, right in zip(compounded_values, compounded_values[1:]):
        assert right <= left + 1e-15

    assert_cost_monotonicity(gross_returns, scenarios, regulatory_fee_bps=0.0)


def test_higher_costs_cannot_improve_each_trade_net_return():
    gross_trade = 0.005
    net_0 = apply_round_trip_cost_to_trade(gross_trade, 0.0)
    net_2 = apply_round_trip_cost_to_trade(gross_trade, 2.0)
    net_5 = apply_round_trip_cost_to_trade(gross_trade, 5.0)
    net_10 = apply_round_trip_cost_to_trade(gross_trade, 10.0)
    net_20 = apply_round_trip_cost_to_trade(gross_trade, 20.0)

    assert net_2 <= net_0
    assert net_5 <= net_2
    assert net_10 <= net_5
    assert net_20 <= net_10


def test_break_even_round_trip_cost_bps_is_positive_for_positive_edge():
    gross_returns = [0.008, 0.007, -0.002, 0.004, 0.005]

    break_even = break_even_round_trip_cost_bps(gross_returns)
    assert break_even > 0.0

    analysis = evaluate_cost_scenarios(
        gross_returns,
        scenarios_bps=DEFAULT_COST_SCENARIOS_BPS,
        regulatory_fee_bps=0.0,
    )
    no_cost = analysis["net_scenarios"][0]["compounded_return"]
    high_cost = analysis["net_scenarios"][-1]["compounded_return"]

    assert no_cost >= high_cost


def test_regulatory_fee_cannot_exceed_total_round_trip_cost():
    with pytest.raises(ValueError, match="regulatory_fee_bps cannot exceed total_round_trip_bps"):
        apply_round_trip_cost_to_trade(0.01, total_round_trip_bps=2.0, regulatory_fee_bps=3.0)
