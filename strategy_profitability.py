from __future__ import annotations

import math
from typing import Any


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _safe_div(numerator: float, denominator: float) -> float:
    if abs(denominator) <= 1e-12:
        return 0.0
    return float(numerator) / float(denominator)


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    var = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(max(var, 0.0))


def _max_drawdown(equity_curve: list[float]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        if peak > 0:
            dd = (peak - value) / peak
            max_dd = max(max_dd, dd)
    return max_dd


def build_strategy_leaderboard(closed_trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for trade in closed_trades:
        key = (str(trade.get("strategy_id") or "unknown"), str(trade.get("strategy_version") or "unknown"))
        grouped.setdefault(key, []).append(dict(trade))

    rows: list[dict[str, Any]] = []
    for (strategy_id, strategy_version), trades in sorted(grouped.items()):
        if not trades:
            continue
        pnl = [_as_float(item.get("net_pnl"), 0.0) for item in trades]
        winners = [value for value in pnl if value > 0]
        losers = [value for value in pnl if value < 0]
        gross_profit = sum(winners)
        gross_loss = abs(sum(losers))
        returns = [_as_float(item.get("percentage_return"), 0.0) for item in trades]
        avg_hold_hours = sum(_as_float(item.get("holding_duration_hours"), 0.0) for item in trades) / max(len(trades), 1)

        expectancy = sum(pnl) / max(len(pnl), 1)
        std_returns = _std(returns)
        sharpe = _safe_div((sum(returns) / max(len(returns), 1)), std_returns)

        # Drawdown is a percentage of a normalized strategy equity curve, not
        # a drawdown of cumulative dollar P/L starting at zero. The old method
        # produced impossible values above 100% and missed losing-first runs.
        equity_curve = [1.0]
        running_equity = 1.0
        for value in returns:
            running_equity *= max(1.0 + float(value), 0.0)
            equity_curve.append(running_equity)

        sample_count = len(trades)
        sample_status = "READY" if sample_count >= 30 else "INSUFFICIENT_SAMPLE"

        rows.append(
            {
                "strategy_id": strategy_id,
                "strategy_version": strategy_version,
                "completed_trade_count": sample_count,
                "net_profit": round(sum(pnl), 6),
                "profit_factor": round(_safe_div(gross_profit, gross_loss), 6),
                "win_rate": round(_safe_div(len(winners), sample_count), 6),
                "average_winner": round(sum(winners) / len(winners), 6) if winners else 0.0,
                "average_loser": round(sum(losers) / len(losers), 6) if losers else 0.0,
                "expectancy": round(expectancy, 6),
                "sharpe_ratio": round(sharpe, 6),
                "maximum_drawdown": round(_max_drawdown(equity_curve), 6),
                "average_holding_time_hours": round(avg_hold_hours, 6),
                "performance_by_regime": _performance_by_regime(trades),
                "sample_status": sample_status,
            }
        )

    return rows


def _performance_by_regime(trades: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[float]] = {}
    for item in trades:
        regime = str(item.get("market_regime") or "unknown")
        grouped.setdefault(regime, []).append(_as_float(item.get("net_pnl"), 0.0))
    result: dict[str, dict[str, float]] = {}
    for regime, values in sorted(grouped.items()):
        result[regime] = {
            "trade_count": float(len(values)),
            "net_pnl": round(sum(values), 6),
            "avg_pnl": round(sum(values) / max(len(values), 1), 6),
        }
    return result


def allocate_equal_risk(active_strategies: list[str]) -> dict[str, float]:
    unique = [item for item in sorted({str(item) for item in active_strategies if str(item).strip()})]
    if not unique:
        return {}
    weight = round(1.0 / len(unique), 10)
    return {item: weight for item in unique}


def paused_strategies_from_drawdown(leaderboard: list[dict[str, Any]], max_drawdown_threshold: float) -> list[str]:
    paused = []
    threshold = max(float(max_drawdown_threshold), 0.0)
    for row in leaderboard:
        if _as_float(row.get("maximum_drawdown"), 0.0) > threshold:
            paused.append(str(row.get("strategy_id") or "unknown"))
    return sorted(set(paused))
