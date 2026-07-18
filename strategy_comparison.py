from __future__ import annotations

import math
import statistics
from itertools import combinations
from typing import Any


def _snapshot_pairwise_key(row: dict[str, Any]) -> tuple[str | None, str | None]:
    run_id = row.get("research_run_id")
    if run_id is None:
        run_id = row.get("_run_id")
    formation_date = row.get("formation_date")
    if formation_date is None:
        formation_date = row.get("_observation_date")
    return (
        str(run_id) if run_id is not None else None,
        str(formation_date) if formation_date is not None else None,
    )


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


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or abs(denominator) <= 1e-9:
        return None
    return round(float(numerator) / float(denominator), 6)


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    if len(set(xs)) <= 1 or len(set(ys)) <= 1:
        return None
    x_mean = statistics.mean(xs)
    y_mean = statistics.mean(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    denominator = math.sqrt(sum((x - x_mean) ** 2 for x in xs) * sum((y - y_mean) ** 2 for y in ys))
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def strategy_metrics(snapshots: list[dict[str, Any]], eligible_candidate_count: int, warnings: list[str]) -> dict[str, Any]:
    completed = [item for item in snapshots if str(item.get("status") or "") == "completed"]
    gross_returns = [float(item.get("gross_portfolio_return") if item.get("gross_portfolio_return") is not None else item.get("portfolio_return") or 0.0) for item in completed]
    net_returns = [float(item.get("net_portfolio_return") if item.get("net_portfolio_return") is not None else item.get("portfolio_return") or 0.0) for item in completed]
    bench_returns = [float(item.get("benchmark_return") or 0.0) for item in completed]
    gross_excess = [float(item.get("gross_excess_return") if item.get("gross_excess_return") is not None else item.get("excess_return") or 0.0) for item in completed]
    net_excess = [float(item.get("net_excess_return") if item.get("net_excess_return") is not None else item.get("excess_return") or 0.0) for item in completed]
    turnover = [float(item.get("turnover") or 0.0) for item in completed if item.get("turnover") is not None]
    costs = [float(item.get("estimated_transaction_cost") or 0.0) for item in completed]
    cash = [float(item.get("cash_weight") or 0.0) for item in completed]
    concentration = [float((item.get("concentration_metrics") or {}).get("hhi") or 0.0) for item in completed]
    effective = [float((item.get("concentration_metrics") or {}).get("effective_holdings") or 0.0) for item in completed if (item.get("concentration_metrics") or {}).get("effective_holdings") is not None]
    max_position = [float(item.get("maximum_position") or 0.0) for item in completed]
    max_sector = [float(item.get("largest_sector_weight") or 0.0) for item in completed]
    holdings = [int(item.get("holding_count") or 0) for item in completed]

    avg_net_excess = _mean(net_excess)
    volatility = _std(net_returns)
    downside = _downside(net_returns)

    return {
        "eligible_candidate_count": int(eligible_candidate_count),
        "formation_snapshot_count": len(snapshots),
        "completed_portfolio_count": len(completed),
        "skipped_portfolio_count": len(snapshots) - len(completed),
        "average_holdings": _mean([float(v) for v in holdings]),
        "average_gross_return": _mean(gross_returns),
        "average_net_return": _mean(net_returns),
        "median_net_return": _median(net_returns),
        "average_benchmark_return": _mean(bench_returns),
        "average_gross_excess_return": _mean(gross_excess),
        "average_net_excess_return": avg_net_excess,
        "positive_gross_excess_rate": round(len([v for v in gross_excess if v > 0]) / len(gross_excess), 6) if gross_excess else None,
        "positive_net_excess_rate": round(len([v for v in net_excess if v > 0]) / len(net_excess), 6) if net_excess else None,
        "volatility": volatility,
        "downside_deviation": downside,
        "cumulative_gross_return": _cumulative(gross_returns),
        "cumulative_net_return": _cumulative(net_returns),
        "cumulative_net_excess_return": _cumulative(net_excess),
        "maximum_drawdown": _max_drawdown(net_returns),
        "sharpe_like_ratio": _ratio(avg_net_excess, volatility),
        "sortino_like_ratio": _ratio(avg_net_excess, downside),
        "average_turnover": _mean(turnover),
        "average_estimated_transaction_cost": _mean(costs),
        "average_cash_weight": _mean(cash),
        "hhi": _mean(concentration),
        "effective_holdings": _mean(effective),
        "largest_position": round(max(max_position), 6) if max_position else None,
        "largest_sector_exposure": round(max(max_sector), 6) if max_sector else None,
        "best_observation": max(completed, key=lambda row: float(row.get("net_portfolio_return") or row.get("portfolio_return") or -10**9), default={}),
        "worst_observation": min(completed, key=lambda row: float(row.get("net_portfolio_return") or row.get("portfolio_return") or 10**9), default={}),
        "warnings": list(warnings),
    }


def pairwise_common_snapshot_comparison(strategy_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_strategy = {item["strategy_id"]: item for item in strategy_results}
    pairwise_rows = []
    for a_id, b_id in combinations(sorted(by_strategy.keys()), 2):
        a_rows = {_snapshot_pairwise_key(row): row for row in by_strategy[a_id].get("snapshots") or [] if row.get("status") == "completed"}
        b_rows = {_snapshot_pairwise_key(row): row for row in by_strategy[b_id].get("snapshots") or [] if row.get("status") == "completed"}
        common_keys = sorted(set(a_rows.keys()) & set(b_rows.keys()))
        a_net = [float((a_rows[key].get("net_portfolio_return") if a_rows[key].get("net_portfolio_return") is not None else a_rows[key].get("portfolio_return") or 0.0)) for key in common_keys]
        b_net = [float((b_rows[key].get("net_portfolio_return") if b_rows[key].get("net_portfolio_return") is not None else b_rows[key].get("portfolio_return") or 0.0)) for key in common_keys]
        a_net_excess = [float((a_rows[key].get("net_excess_return") if a_rows[key].get("net_excess_return") is not None else a_rows[key].get("excess_return") or 0.0)) for key in common_keys]
        b_net_excess = [float((b_rows[key].get("net_excess_return") if b_rows[key].get("net_excess_return") is not None else b_rows[key].get("excess_return") or 0.0)) for key in common_keys]
        diffs = [a - b for a, b in zip(a_net, b_net)]
        excess_diffs = [a - b for a, b in zip(a_net_excess, b_net_excess)]
        wins = len([value for value in excess_diffs if value > 0])
        ties = len([value for value in excess_diffs if abs(value) <= 1e-12])
        pairwise_rows.append(
            {
                "strategy_a_id": a_id,
                "strategy_b_id": b_id,
                "common_snapshot_count": len(common_keys),
                "average_net_return_difference": _mean(diffs),
                "average_net_excess_return_difference": _mean(excess_diffs),
                "win_rate_a_over_b": round(wins / len(excess_diffs), 6) if excess_diffs else None,
                "median_difference": _median(excess_diffs),
                "volatility_difference": None if not common_keys else round((_std(a_net) or 0.0) - (_std(b_net) or 0.0), 6),
                "drawdown_difference": None if not common_keys else round((_max_drawdown(a_net) or 0.0) - (_max_drawdown(b_net) or 0.0), 6),
                "turnover_difference": round((_mean([float((a_rows[k].get("turnover") or 0.0)) for k in common_keys]) or 0.0) - (_mean([float((b_rows[k].get("turnover") or 0.0)) for k in common_keys]) or 0.0), 6) if common_keys else None,
                "concentration_difference": round((_mean([float((a_rows[k].get("concentration_metrics") or {}).get("hhi") or 0.0) for k in common_keys]) or 0.0) - (_mean([float((b_rows[k].get("concentration_metrics") or {}).get("hhi") or 0.0) for k in common_keys]) or 0.0), 6) if common_keys else None,
                "return_correlation": _pearson(a_net, b_net),
                "ties": ties,
            }
        )
    return pairwise_rows


def leaderboard(strategy_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        strategy_rows,
        key=lambda row: (
            -(float((row.get("scorecard") or {}).get("composite_score") or -10**9)),
            -(float((row.get("analytics") or {}).get("average_net_excess_return") or -10**9)),
            str(row.get("strategy_id") or ""),
        ),
    )
