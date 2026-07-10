import math
from typing import Iterable

import pandas as pd


DEFAULT_COST_SCENARIOS_BPS = [0.0, 2.0, 5.0, 10.0, 20.0]


def _safe_sharpe(returns):
    if len(returns) < 2:
        return 0.0
    series = pd.Series(returns, dtype=float)
    std = float(series.std(ddof=1))
    if std == 0.0:
        return 0.0
    return float((series.mean() / std) * math.sqrt(252.0))


def _max_drawdown_from_returns(returns):
    if not returns:
        return 0.0
    equity = (pd.Series(returns, dtype=float) + 1.0).cumprod()
    rolling_peak = equity.cummax()
    drawdown = (equity / rolling_peak) - 1.0
    return float(drawdown.min())


def _compounded_return(returns):
    if not returns:
        return 0.0
    return float((pd.Series(returns, dtype=float) + 1.0).prod() - 1.0)


def compute_trade_series_metrics(trade_returns):
    values = [float(item) for item in trade_returns]
    if not values:
        return {
            "compounded_return": 0.0,
            "average_trade": 0.0,
            "win_rate": 0.0,
            "maximum_drawdown": 0.0,
            "sharpe_ratio": 0.0,
        }

    return {
        "compounded_return": _compounded_return(values),
        "average_trade": float(pd.Series(values, dtype=float).mean()),
        "win_rate": float(sum(1 for item in values if item > 0.0) / len(values)),
        "maximum_drawdown": _max_drawdown_from_returns(values),
        "sharpe_ratio": _safe_sharpe(values),
    }


def apply_round_trip_cost_to_trade(gross_trade_return, total_round_trip_bps, regulatory_fee_bps=0.0):
    gross_return = float(gross_trade_return)
    total_bps = float(total_round_trip_bps)
    regulatory_bps = float(regulatory_fee_bps)

    if not math.isfinite(gross_return):
        raise ValueError("gross trade return must be finite")
    if total_bps < 0.0 or regulatory_bps < 0.0:
        raise ValueError("cost assumptions must be non-negative")
    if regulatory_bps > total_bps:
        raise ValueError("regulatory_fee_bps cannot exceed total_round_trip_bps")

    slippage_bps = total_bps - regulatory_bps
    side_slippage = (slippage_bps / 2.0) / 10000.0
    side_regulatory = (regulatory_bps / 2.0) / 10000.0

    buy_multiplier = 1.0 + side_slippage + side_regulatory
    sell_multiplier = 1.0 - side_slippage - side_regulatory
    if buy_multiplier <= 0.0 or sell_multiplier <= 0.0:
        raise ValueError("cost assumptions produce invalid multipliers")

    net_return = ((1.0 + gross_return) * (sell_multiplier / buy_multiplier)) - 1.0
    return float(net_return)


def net_returns_for_cost_scenario(gross_returns, total_round_trip_bps, regulatory_fee_bps=0.0):
    return [
        apply_round_trip_cost_to_trade(item, total_round_trip_bps, regulatory_fee_bps=regulatory_fee_bps)
        for item in gross_returns
    ]


def evaluate_cost_scenarios(gross_returns, scenarios_bps=None, regulatory_fee_bps=0.0):
    scenarios = scenarios_bps or DEFAULT_COST_SCENARIOS_BPS
    gross_values = [float(item) for item in gross_returns]
    gross_metrics = compute_trade_series_metrics(gross_values)

    output = {
        "gross": {
            "trade_count": len(gross_values),
            **gross_metrics,
        },
        "net_scenarios": [],
    }

    for total_bps in scenarios:
        net_values = net_returns_for_cost_scenario(gross_values, total_bps, regulatory_fee_bps=regulatory_fee_bps)
        net_metrics = compute_trade_series_metrics(net_values)
        output["net_scenarios"].append(
            {
                "total_round_trip_bps": float(total_bps),
                "regulatory_fee_bps": float(regulatory_fee_bps),
                "slippage_bps": float(total_bps - regulatory_fee_bps),
                "trade_count": len(net_values),
                **net_metrics,
            }
        )

    return output


def break_even_round_trip_cost_bps(gross_returns, precision_bps=1e-6, upper_bound_bps=2000.0):
    gross_values = [float(item) for item in gross_returns]
    if not gross_values:
        return 0.0

    gross_compounded = _compounded_return(gross_values)
    if gross_compounded <= -1.0:
        return 0.0

    low = 0.0
    high = float(upper_bound_bps)

    if _compounded_return(net_returns_for_cost_scenario(gross_values, low)) < 0.0:
        return 0.0

    if _compounded_return(net_returns_for_cost_scenario(gross_values, high)) > 0.0:
        return high

    for _ in range(80):
        mid = (low + high) / 2.0
        mid_value = _compounded_return(net_returns_for_cost_scenario(gross_values, mid))
        if mid_value >= 0.0:
            low = mid
        else:
            high = mid
        if (high - low) <= precision_bps:
            break

    return float(low)


def assert_cost_monotonicity(gross_returns, scenarios_bps: Iterable[float], regulatory_fee_bps=0.0):
    ordered = sorted(float(item) for item in scenarios_bps)
    previous = None
    for total_bps in ordered:
        net_values = net_returns_for_cost_scenario(gross_returns, total_bps, regulatory_fee_bps=regulatory_fee_bps)
        compounded = _compounded_return(net_values)
        if previous is not None and compounded > (previous + 1e-15):
            raise AssertionError("Higher cost scenario improved compounded net return")
        previous = compounded
