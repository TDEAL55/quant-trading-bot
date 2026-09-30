from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_performance_artifact(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def _metrics(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    metrics = dict(raw.get("metrics") or raw.get("summary") or raw)
    count = int(
        metrics.get("number_of_trades")
        or metrics.get("completed_trade_count")
        or metrics.get("trade_count")
        or 0
    )
    return {
        "trade_count": count,
        "total_pnl": _number(metrics.get("total_pnl") if "total_pnl" in metrics else metrics.get("net_profit")),
        "win_rate": _number(metrics.get("win_rate")),
        "expectancy": _number(
            metrics.get("expectancy_per_trade")
            if "expectancy_per_trade" in metrics
            else metrics.get("expectancy")
        ),
        "profit_factor": _number(metrics.get("profit_factor")),
        "maximum_drawdown": _number(metrics.get("maximum_drawdown")),
    }


def _strategy_rows(payload: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    raw = dict(payload or {})
    candidates = (
        list(dict(raw.get("breakdowns") or {}).get("by_strategy") or [])
        or list(raw.get("strategy_scoreboard") or [])
        or list(raw.get("per_strategy") or [])
    )
    rows = []
    for item in candidates:
        row = dict(item or {})
        strategy = str(row.get("strategy") or row.get("strategy_id") or "unknown")
        rows.append(
            {
                "strategy": strategy,
                "trade_count": int(row.get("trade_count") or row.get("completed_trade_count") or 0),
                "pnl": _number(row.get("pnl") if "pnl" in row else row.get("net_profit")),
                "win_rate": _number(row.get("win_rate")),
                "expectancy": _number(
                    row.get("expectancy_per_trade") if "expectancy_per_trade" in row else row.get("expectancy")
                ),
                "profit_factor": _number(row.get("profit_factor")),
            }
        )
    return rows


def build_performance_comparison(
    *,
    backtest: Mapping[str, Any] | None,
    paper: Mapping[str, Any] | None,
    live: Mapping[str, Any] | None,
    minimum_sample: int = 30,
) -> dict[str, Any]:
    """Compare three environments without making allocation decisions."""
    rows = []
    for source, payload in (("backtest", backtest), ("paper", paper), ("live", live)):
        metrics = _metrics(payload)
        available = bool(payload)
        metrics.update(
            {
                "source": source,
                "available": available,
                "sample_sufficient": available and metrics["trade_count"] >= max(int(minimum_sample), 1),
            }
        )
        rows.append(metrics)

    comparable = [row for row in rows if row["sample_sufficient"] and row["expectancy"] is not None]
    flags: list[str] = []
    if len(comparable) >= 2:
        signs = {1 if float(row["expectancy"]) > 0 else -1 if float(row["expectancy"]) < 0 else 0 for row in comparable}
        if len(signs) > 1:
            flags.append("expectancy_sign_differs_across_environments")
    missing = [row["source"] for row in rows if not row["available"]]
    if missing:
        flags.append("missing_sources:" + ",".join(missing))
    if any(row["available"] and not row["sample_sufficient"] for row in rows):
        flags.append("one_or_more_samples_below_minimum")
    by_source = {
        source: {row["strategy"]: row for row in _strategy_rows(payload)}
        for source, payload in (("backtest", backtest), ("paper", paper), ("live", live))
    }
    strategy_comparison = []
    for strategy in sorted(set().union(*(set(values) for values in by_source.values()))):
        for source in ("backtest", "paper", "live"):
            row = dict(by_source[source].get(strategy) or {"strategy": strategy, "trade_count": 0})
            row.update(
                {
                    "source": source,
                    "available": strategy in by_source[source],
                    "sample_sufficient": int(row.get("trade_count") or 0) >= max(int(minimum_sample), 1),
                }
            )
            strategy_comparison.append(row)
    return {
        "rows": rows,
        "strategy_rows": strategy_comparison,
        "minimum_sample": max(int(minimum_sample), 1),
        "comparison_ready": len(comparable) == 3,
        "flags": flags,
        "allocation_changed": False,
        "note": "Comparison is diagnostic only; it never changes live strategy allocation.",
    }
