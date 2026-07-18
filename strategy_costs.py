from __future__ import annotations

from typing import Any


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def estimate_transaction_cost(
    holding_count: int,
    turnover: float | None,
    commission_per_trade: float = 0.0,
    fixed_rebalance_cost: float = 0.0,
    turnover_cost_bps: float = 0.0,
    slippage_bps: float = 0.0,
) -> float:
    trades = max(int(holding_count), 0)
    turn = max(_as_float(turnover, 0.0), 0.0)
    commission = max(_as_float(commission_per_trade, 0.0), 0.0) * trades
    fixed = max(_as_float(fixed_rebalance_cost, 0.0), 0.0)
    bps_drag = max(_as_float(turnover_cost_bps, 0.0) + _as_float(slippage_bps, 0.0), 0.0) / 10000.0
    flow_cost = turn * bps_drag
    return round(max(commission + fixed + flow_cost, 0.0), 10)


def apply_transaction_costs_to_snapshot(snapshot: dict[str, Any], cost_config: dict[str, Any]) -> dict[str, Any]:
    result = dict(snapshot)
    holding_count = int(result.get("holding_count") or 0)
    turnover = result.get("turnover")
    estimated_cost = estimate_transaction_cost(
        holding_count=holding_count,
        turnover=turnover,
        commission_per_trade=float(cost_config.get("commission_per_trade") or 0.0),
        fixed_rebalance_cost=float(cost_config.get("fixed_rebalance_cost") or 0.0),
        turnover_cost_bps=float(cost_config.get("turnover_cost_bps") or 0.0),
        slippage_bps=float(cost_config.get("slippage_bps") or 0.0),
    )
    gross_return = _as_float(result.get("portfolio_return"), 0.0)
    benchmark_return = _as_float(result.get("benchmark_return"), 0.0)
    gross_excess = _as_float(result.get("excess_return"), gross_return - benchmark_return)
    net_return = gross_return - estimated_cost
    net_excess = net_return - benchmark_return

    result["gross_portfolio_return"] = round(gross_return, 10)
    result["estimated_transaction_cost"] = estimated_cost
    result["net_portfolio_return"] = round(net_return, 10)
    result["gross_excess_return"] = round(gross_excess, 10)
    result["net_excess_return"] = round(net_excess, 10)
    result["cost_drag"] = round(gross_return - net_return, 10)
    return result


def apply_transaction_costs(snapshots: list[dict[str, Any]], cost_config: dict[str, Any]) -> list[dict[str, Any]]:
    return [apply_transaction_costs_to_snapshot(snapshot, cost_config) for snapshot in snapshots]
