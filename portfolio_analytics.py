from __future__ import annotations

import math
import statistics
from typing import Any


def _as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _mean(values: list[float]) -> float | None:
    return round(statistics.mean(values), 6) if values else None


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 6) if values else None


def _std(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    return round(statistics.pstdev(values), 6)


def _downside(values: list[float]) -> float | None:
    if not values:
        return None
    negatives = [min(v, 0.0) for v in values]
    return round(math.sqrt(sum(v * v for v in negatives) / len(negatives)), 6)


def _cumulative(values: list[float]) -> float | None:
    if not values:
        return None
    total = 1.0
    for value in values:
        total *= 1.0 + float(value)
    return round(total - 1.0, 6)


def _max_drawdown(values: list[float]) -> float | None:
    if not values:
        return None
    total = 1.0
    peak = 1.0
    drawdown = 0.0
    for value in values:
        total *= 1.0 + float(value)
        peak = max(peak, total)
        if peak > 0:
            drawdown = min(drawdown, total / peak - 1.0)
    return round(drawdown, 6)


def _ratio(num: float | None, den: float | None) -> float | None:
    if num is None or den is None or abs(den) <= 1e-9:
        return None
    return round(num / den, 6)


def concentration_metrics(weights: dict[str, float], sector_weights: dict[str, float], cash_weight: float) -> dict[str, Any]:
    invested_weights = [w for w in weights.values() if w > 0]
    hhi = sum(w * w for w in invested_weights)
    effective = (1.0 / hhi) if hhi > 0 else None
    top_sorted = sorted(invested_weights, reverse=True)
    return {
        "hhi": round(hhi, 6),
        "effective_holdings": round(effective, 6) if effective is not None else None,
        "top_1_weight": round(top_sorted[0], 6) if top_sorted else 0.0,
        "top_3_weight": round(sum(top_sorted[:3]), 6) if top_sorted else 0.0,
        "largest_sector_weight": round(max(sector_weights.values()), 6) if sector_weights else 0.0,
        "active_sectors": len([v for v in sector_weights.values() if v > 0]),
        "cash_weight": round(max(float(cash_weight), 0.0), 6),
    }


def calculate_turnover(previous_weights: dict[str, float] | None, current_weights: dict[str, float]) -> float | None:
    if previous_weights is None:
        return None
    keys = set(previous_weights.keys()) | set(current_weights.keys()) | {"CASH"}
    total = 0.0
    for key in keys:
        total += abs(float(current_weights.get(key, 0.0)) - float(previous_weights.get(key, 0.0)))
    return round(0.5 * total, 6)


def calculate_snapshot_returns(
    formation_date: str,
    research_run_id: str,
    holdings: list[dict[str, Any]],
    benchmark_symbol: str,
    cash_weight: float,
    turnover: float | None,
    warnings: list[str],
    status: str,
) -> dict[str, Any]:
    portfolio_return = 0.0
    benchmark_return = 0.0
    excess_return = 0.0
    symbol_contrib: dict[str, dict[str, float]] = {}
    sector_contrib: dict[str, dict[str, float]] = {}
    signal_contrib: dict[str, dict[str, float]] = {}
    regime_contrib: dict[str, dict[str, float]] = {}
    sector_weights: dict[str, float] = {}

    by_symbol = {}
    for row in holdings:
        symbol = str(row.get("symbol") or "").upper()
        weight = float(_as_float(row.get("weight"), 0.0) or 0.0)
        raw_return = _as_float(row.get("forward_return"), None)
        bench = _as_float(row.get("benchmark_return"), None)
        excess = _as_float(row.get("excess_return"), None)
        if not symbol or weight <= 0 or raw_return is None or bench is None or excess is None:
            continue
        contribution = weight * raw_return
        excess_contribution = weight * excess
        portfolio_return += contribution
        benchmark_return += weight * bench
        excess_return += excess_contribution

        sector = str(row.get("sector") or "Unknown")
        signal = str(row.get("signal") or "Unknown")
        regime = str(row.get("market_regime") or "Unknown")
        sector_weights[sector] = sector_weights.get(sector, 0.0) + weight

        by_symbol[symbol] = {
            "symbol": symbol,
            "historical_rank": row.get("rank"),
            "score": row.get("overall_score"),
            "confidence": row.get("confidence"),
            "sector": sector,
            "signal": signal,
            "market_regime": regime,
            "weight": round(weight, 10),
            "forward_return": raw_return,
            "excess_return": excess,
            "return_contribution": round(contribution, 10),
            "excess_contribution": round(excess_contribution, 10),
        }

        for target, key in [
            (symbol_contrib, symbol),
            (sector_contrib, sector),
            (signal_contrib, signal),
            (regime_contrib, regime),
        ]:
            node = target.setdefault(key, {"weight": 0.0, "raw_contribution": 0.0, "excess_contribution": 0.0, "count": 0})
            node["weight"] += weight
            node["raw_contribution"] += contribution
            node["excess_contribution"] += excess_contribution
            node["count"] += 1

    holding_weights = {symbol: data["weight"] for symbol, data in by_symbol.items()}
    concentration = concentration_metrics(holding_weights, sector_weights, cash_weight)

    snapshot = {
        "formation_date": formation_date,
        "research_run_id": research_run_id,
        "benchmark_symbol": benchmark_symbol,
        "holding_count": len(by_symbol),
        "invested_weight": round(sum(holding_weights.values()), 10),
        "cash_weight": round(float(cash_weight), 10),
        "portfolio_return": round(portfolio_return, 10),
        "benchmark_return": round(benchmark_return, 10),
        "excess_return": round(excess_return, 10),
        "turnover": turnover,
        "concentration_metrics": concentration,
        "maximum_position": round(max(holding_weights.values()), 10) if holding_weights else 0.0,
        "largest_sector_weight": concentration["largest_sector_weight"],
        "holdings": [by_symbol[k] for k in sorted(by_symbol)],
        "symbol_contribution": [
            {"symbol": key, **{m: round(v, 10) if isinstance(v, float) else v for m, v in value.items()}}
            for key, value in sorted(symbol_contrib.items())
        ],
        "sector_contribution": [
            {"sector": key, **{m: round(v, 10) if isinstance(v, float) else v for m, v in value.items()}}
            for key, value in sorted(sector_contrib.items())
        ],
        "signal_contribution": [
            {"signal": key, **{m: round(v, 10) if isinstance(v, float) else v for m, v in value.items()}}
            for key, value in sorted(signal_contrib.items())
        ],
        "regime_contribution": [
            {"market_regime": key, **{m: round(v, 10) if isinstance(v, float) else v for m, v in value.items()}}
            for key, value in sorted(regime_contrib.items())
        ],
        "warnings": list(warnings),
        "status": status,
    }
    return snapshot


def aggregate_portfolio_metrics(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [item for item in snapshots if str(item.get("status") or "") == "completed"]
    returns = [float(item.get("portfolio_return") or 0.0) for item in completed]
    bench = [float(item.get("benchmark_return") or 0.0) for item in completed]
    excess = [float(item.get("excess_return") or 0.0) for item in completed]
    holdings = [int(item.get("holding_count") or 0) for item in completed]
    cash = [float(item.get("cash_weight") or 0.0) for item in completed]
    turnover = [float(item["turnover"]) for item in completed if item.get("turnover") is not None]
    concentration = [float((item.get("concentration_metrics") or {}).get("hhi") or 0.0) for item in completed]
    sector_concentration = [float((item.get("concentration_metrics") or {}).get("largest_sector_weight") or 0.0) for item in completed]
    max_positions = [float(item.get("maximum_position") or 0.0) for item in completed]

    avg_excess = _mean(excess)
    std_ret = _std(returns)
    downside_ret = _downside(returns)

    ordered = sorted(completed, key=lambda row: (str(row.get("formation_date") or ""), str(row.get("research_run_id") or "")))

    return {
        "portfolio_count": len(snapshots),
        "completed_portfolio_count": len(completed),
        "skipped_portfolio_count": len(snapshots) - len(completed),
        "average_portfolio_return": _mean(returns),
        "median_portfolio_return": _median(returns),
        "average_benchmark_return": _mean(bench),
        "average_portfolio_excess_return": avg_excess,
        "median_portfolio_excess_return": _median(excess),
        "positive_return_rate": round(len([v for v in returns if v > 0]) / len(returns), 6) if returns else None,
        "positive_excess_return_rate": round(len([v for v in excess if v > 0]) / len(excess), 6) if excess else None,
        "standard_deviation": std_ret,
        "downside_deviation": downside_ret,
        "cumulative_compounded_return": _cumulative(returns),
        "cumulative_compounded_excess_return": _cumulative(excess),
        "maximum_drawdown": _max_drawdown(returns),
        "sharpe_like_ratio": _ratio(avg_excess, std_ret),
        "sortino_like_ratio": _ratio(avg_excess, downside_ret),
        "best_portfolio_observation": max(ordered, key=lambda row: float(row.get("portfolio_return") or -10**9), default={}),
        "worst_portfolio_observation": min(ordered, key=lambda row: float(row.get("portfolio_return") or 10**9), default={}),
        "average_number_of_holdings": _mean([float(v) for v in holdings]),
        "average_cash_weight": _mean(cash),
        "average_turnover": _mean(turnover),
        "average_concentration": _mean(concentration),
        "average_sector_concentration": _mean(sector_concentration),
        "maximum_position_observed": round(max(max_positions), 6) if max_positions else None,
        "maximum_sector_exposure_observed": round(max(sector_concentration), 6) if sector_concentration else None,
    }


def build_method_comparison(method_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for result in method_results:
        metrics = result.get("analytics") or {}
        rows.append(
            {
                "method": result.get("method"),
                "portfolio_count": metrics.get("portfolio_count", 0),
                "average_return": metrics.get("average_portfolio_return"),
                "average_excess_return": metrics.get("average_portfolio_excess_return"),
                "positive_excess_rate": metrics.get("positive_excess_return_rate"),
                "volatility": metrics.get("standard_deviation"),
                "downside_deviation": metrics.get("downside_deviation"),
                "sharpe_like_ratio": metrics.get("sharpe_like_ratio"),
                "sortino_like_ratio": metrics.get("sortino_like_ratio"),
                "maximum_drawdown": metrics.get("maximum_drawdown"),
                "cumulative_return": metrics.get("cumulative_compounded_return"),
                "average_turnover": metrics.get("average_turnover"),
                "average_concentration": metrics.get("average_concentration"),
                "average_cash_weight": metrics.get("average_cash_weight"),
                "maximum_position_weight": metrics.get("maximum_position_observed"),
                "largest_sector_exposure": metrics.get("maximum_sector_exposure_observed"),
                "skipped_snapshot_count": metrics.get("skipped_portfolio_count", 0),
                "warnings": result.get("warnings", []),
            }
        )
    return sorted(rows, key=lambda row: (-(row.get("average_excess_return") or -10**9), row.get("method") or ""))
